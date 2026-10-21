"""
backend/wake_listener.py
------------------------
Local, offline "Hey Jarvis" wake-word listener built on openWakeWord.

Design goals (see Phase 8.2/8.4 plan):
- **Fully local at inference time.** Uses openWakeWord's ONNX runtime. This
  module performs NO network access and NEVER downloads model files. All model
  files must already exist on disk before the listener is constructed.
- **Explicit, separate model loading.** Model loading happens once, at
  construction time (``_load_model``), and is entirely separate from microphone
  startup (``start``). If any required model file is missing, a clear
  ``WakeListenerSetupError`` is raised that names the exact expected path(s).
- **Reuses ``sounddevice`` for capture** (mono, 16 kHz, int16), feeding
  1280-sample (80 ms) frames to openWakeWord -- exactly the frame size
  openWakeWord's own utilities use.
- **Releases the microphone before signalling.** On a wake detection the
  listener stops and closes its own audio stream *first*, then invokes the
  supplied ``on_wake`` callback. The callback is only a signal that a wake
  occurred (e.g. push a WebSocket event in Stage 3); it must never open a
  microphone itself.
- **Thread/stream lifecycle is guarded** so only one capture thread and one
  audio stream can exist per listener. A module-level singleton accessor
  (``get_wake_listener``) additionally prevents duplicate listeners across the
  application.

Public API
----------
- ``WakeWordListener(...)``        -- construct (loads model, opens no mic).
- ``.start(on_wake=None)``         -- open mic + start capture worker thread.
- ``.stop()`` / ``.close()``       -- stop stream, release mic, join worker.
- ``.is_running``                  -- whether capture is currently active.
- ``get_wake_listener(**kwargs)``  -- process-wide singleton accessor.
"""

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np
import sounddevice as sd

# ---------------------------------------------------------------------------
# Audio / detection constants
# ---------------------------------------------------------------------------

#: Capture sample rate. openWakeWord's pretrained models expect 16 kHz audio.
SAMPLE_RATE = 16_000

#: Mono capture.
CHANNELS = 1

#: 16-bit signed PCM, matching openWakeWord's expected ``int16`` input.
DTYPE = "int16"

#: Frame (and step) size fed to ``Model.predict`` -- 1280 samples == 80 ms at
#: 16 kHz. This is the exact frame size openWakeWord's own ``predict_clip``
#: utility uses, so it is the safest choice for streaming detection.
FRAME_SAMPLES = 1280

#: Exact pretrained model filenames required for offline ONNX inference.
_MELSPEC_MODEL = "melspectrogram.onnx"
_EMBEDDING_MODEL = "embedding_model.onnx"
_WAKE_MODEL = "hey_jarvis_v0.1.onnx"

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WakeListenerError(RuntimeError):
    """Base error for wake-listener runtime failures (device / stream issues)."""


class WakeListenerSetupError(WakeListenerError):
    """Raised when required model files are missing or openWakeWord is unusable."""

# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------

class WakeWordListener:
    """Always-on "Hey Jarvis" detector backed by openWakeWord + sounddevice.

    Thread/stream lifecycle
    -----------------------
    * ``start()`` opens a ``sounddevice.InputStream`` and launches a single
      daemon worker thread (``_run``). The audio callback only enqueues raw
      ``int16`` frames; the worker buffers them into 1280-sample frames and
      runs inference, so a slow prediction can never overflow the audio device.
    * ``stop()`` is idempotent: it sets the stop event, stops+closes the stream
      (releasing the mic), signals the worker to exit, and joins it. The worker
      is joined *outside* the lock to avoid deadlocking with a worker that is
      itself releasing the stream.
    * On detection, the worker (``_handle_wake``) stops+closes the stream and
      flips ``is_running`` to ``False`` *before* invoking ``on_wake``.
    """

    def __init__(
        self,
        *,
        device: Optional[Union[int, str]] = None,
        threshold: float = 0.5,
        cooldown_seconds: float = 2.0,
        model_dir: Optional[Union[str, Path]] = None,
        on_wake: Optional[Callable[[], None]] = None,
        queue_maxsize: int = 32,
        join_timeout: float = 2.0,
    ) -> None:
        """
        Args:
            device: Microphone device. ``None`` uses the system default input
                device (recommended). An ``int`` index or device-name ``str`` is
                validated against the available devices at ``start()`` time; an
                invalid index raises a clear :class:`WakeListenerError`. The
                previously-used hardcoded index is intentionally NOT assumed.
            threshold: Detection score in ``[0, 1]`` at/above which the wake
                phrase is considered present (openWakeWord default is 0.5).
            cooldown_seconds: Minimum time between two accepted detections, to
                stop buffered frames or a re-``start()`` from firing repeatedly.
            model_dir: Directory containing the three required ``.onnx`` files.
                Defaults to openWakeWord's bundled ``resources/models`` dir.
            on_wake: Optional callback invoked (after the mic is released) when
                the wake phrase is detected. May also be supplied to ``start``.
            queue_maxsize: Max queued audio frames before the callback starts
                dropping (bounds memory if inference briefly lags capture).
            join_timeout: Seconds to wait for the worker thread to exit on stop.
        """
        self._logger = logging.getLogger(f"{__name__}.WakeWordListener")

        self._device = device
        self._threshold = float(threshold)
        self._cooldown = float(cooldown_seconds)
        self._on_wake = on_wake
        self._queue_maxsize = max(1, int(queue_maxsize))
        self._join_timeout = float(join_timeout)
        self._model_dir = (
            Path(model_dir) if model_dir is not None else _default_model_dir()
        )

        # --- lifecycle state (guarded by ``self._lock``) ---
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue(
            maxsize=self._queue_maxsize
        )
        self._stream: Optional[sd.InputStream] = None
        self._worker: Optional[threading.Thread] = None
        self._is_running = False
        self._last_detection = 0.0

        # Explicit, separate model load (never downloads; raises on missing).
        self._model = self._load_model()

    # -- introspection ------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether microphone capture is currently active."""
        return self._is_running

    @property
    def model_dir(self) -> Path:
        """Directory the wake model files were loaded from."""
        return self._model_dir


    # -- model loading ------------------------------------------------------

    def _load_model(self):
        """Load the openWakeWord ONNX model. Separate from mic startup.

        Verifies the three required model files exist and raises a clear setup
        error (with exact expected paths) if any are missing. Never downloads.
        """
        required = {
            "melspectrogram": self._model_dir / _MELSPEC_MODEL,
            "embedding": self._model_dir / _EMBEDDING_MODEL,
            "wake (hey_jarvis)": self._model_dir / _WAKE_MODEL,
        }
        missing = [str(path) for path in required.values() if not path.is_file()]
        if missing:
            raise WakeListenerSetupError(
                "Required openWakeWord model file(s) are missing:\n  - "
                + "\n  - ".join(missing)
                + f"\n\nExpected model directory: {self._model_dir}\n"
                "This listener never downloads models automatically. Populate "
                "the directory once (it is a one-time, offline-safe step) by "
                "running, inside the backend venv:\n"
                '    python -c "import openwakeword; '
                'openwakeword.utils.download_models()"\n'
                "then ensure the three .onnx files listed above are present in "
                "that directory."
            )

        try:
            # Imported lazily so importing this module never fails just because
            # openWakeWord is not yet installed; the error surfaces clearly here.
            from openwakeword.model import Model
        except Exception as exc:  # pragma: no cover - environment dependent
            raise WakeListenerSetupError(
                "openWakeWord is not importable. Ensure it is installed in the "
                "backend venv (see backend/requirements.txt): "
                f"{exc}"
            ) from exc

        try:
            self._logger.info(
                "Loading openWakeWord ONNX models from %s", self._model_dir
            )
            # ``inference_framework='onnx'`` is required on Windows: the default
            # 'tflite' backend is not supported there. The shared melspectrogram
            # and embedding models are resolved by openWakeWord from the package
            # ``resources/models`` dir (which is this default ``model_dir``).
            return Model(
                wakeword_models=[str(required["wake (hey_jarvis)"])],
                inference_framework="onnx",
            )
        except Exception as exc:
            raise WakeListenerSetupError(
                "Failed to initialise the openWakeWord model from "
                f"{self._model_dir}: {exc}"
            ) from exc

    # -- device resolution --------------------------------------------------

    def _resolve_device(self) -> Optional[Union[int, str]]:
        """Validate and return the capture device, or raise a clear error.

        ``None`` -> system default input. An explicit index/name is checked for
        existence and input capability. Never assumes a specific index.
        """
        if self._device is not None:
            try:
                devices = sd.query_devices()
            except Exception as exc:
                raise WakeListenerError(
                    f"Could not query audio devices: {exc}"
                ) from exc

            if isinstance(self._device, int):
                count = len(devices)
                if self._device < 0 or self._device >= count:
                    raise WakeListenerError(
                        f"Requested microphone device index {self._device} is "
                        f"out of range; {count} device(s) are available. Pass a "
                        "valid input device index or None for the system default."
                    )
                info = devices[self._device]
                if int(info.get("max_input_channels", 0)) < 1:
                    raise WakeListenerError(
                        f"Device index {self._device} "
                        f"('{info.get('name', 'unknown')}') has no input "
                        "channels and cannot be used as a microphone."
                    )
            return self._device

        # No explicit device -> use the system default input device.
        try:
            default_input = sd.default.device[0]
        except Exception:
            default_input = -1
        if default_input is None or default_input < 0:
            raise WakeListenerError(
                "No default input microphone is available. Set a valid device "
                "index (see 'python -m backend.wake_listener --list-devices')."
            )
        return default_input


    # -- start / stop -------------------------------------------------------

    def start(self, on_wake: Optional[Callable[[], None]] = None) -> None:
        """Open the microphone and begin listening for the wake phrase.

        Idempotent: a second call while already running is a no-op (logged).
        Assumes the model is already loaded (done in ``__init__``).
        """
        with self._lock:
            if self._is_running:
                self._logger.warning(
                    "Wake listener already running; ignoring start()."
                )
                return
            if self._model is None:  # defensive; normally set in __init__
                raise WakeListenerError("Model not loaded.")

            if on_wake is not None:
                self._on_wake = on_wake

            device = self._resolve_device()

            # Reset lifecycle for a fresh capture session.
            self._stop_event.clear()
            self._clear_queue()

            try:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=FRAME_SAMPLES,
                    device=device,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    callback=self._audio_callback,
                )
                self._stream.start()
            except Exception as exc:
                # Release anything partially opened before re-raising.
                self._close_stream_locked()
                self._is_running = False
                raise WakeListenerError(
                    f"Failed to open/start the microphone (device={device!r}): "
                    f"{exc}"
                ) from exc

            self._is_running = True
            self._worker = threading.Thread(
                target=self._run, name="wake-listener-worker", daemon=True
            )
            self._worker.start()

        self._logger.info(
            "Wake listener started (device=%r, threshold=%.2f, cooldown=%.1fs).",
            device,
            self._threshold,
            self._cooldown,
        )

    def stop(self) -> None:
        """Stop capture, release the microphone, and join the worker.

        Safe to call multiple times and from within the ``on_wake`` callback.
        """
        with self._lock:
            if not self._is_running:
                # Still ensure any lingering stream is released (idempotent).
                self._close_stream_locked()
                return
            self._stop_event.set()
            self._close_stream_locked()
            self._signal_worker_exit()
            worker = self._worker
            self._worker = None
            self._is_running = False

        # Join OUTSIDE the lock so a worker that is itself releasing the stream
        # cannot deadlock against us.
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self._join_timeout)
            if worker.is_alive():
                self._logger.warning(
                    "Wake listener worker did not exit within %.1fs.",
                    self._join_timeout,
                )
        self._logger.info("Wake listener stopped.")

    # ``close`` is a friendly alias for callers that use context managers.
    def close(self) -> None:
        self.stop()

    def __enter__(self) -> "WakeWordListener":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        return False


    # -- audio callback + worker -------------------------------------------

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        """sounddevice capture callback. Runs on the audio thread.

        Only enqueues a detached copy of the int16 samples; all inference and
        lifecycle work happens on the worker thread. Dropping frames under
        backpressure is preferred over blocking the audio device.
        """
        if status:
            self._logger.warning("Audio stream status: %s", status)
        if self._stop_event.is_set():
            return
        try:
            # ``indata`` is a (frames, channels) int16 array; flatten to 1-D and
            # detach from the buffer sounddevice reuses between callbacks.
            frame = np.ascontiguousarray(indata).reshape(-1).copy()
            self._queue.put_nowait(frame)
        except queue.Full:
            self._logger.warning(
                "Wake listener audio queue full; dropping a frame."
            )

    def _run(self) -> None:
        """Worker thread: buffer frames and run inference until stopped."""
        buffer = np.empty(0, dtype=np.int16)
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk is None:  # sentinel -> clean exit
                    break

                buffer = np.concatenate((buffer, chunk))
                while buffer.shape[0] >= FRAME_SAMPLES:
                    frame = buffer[:FRAME_SAMPLES]
                    buffer = buffer[FRAME_SAMPLES:].copy()
                    if self._detect(frame):
                        # Wake phrase detected: release the mic BEFORE signalling.
                        self._handle_wake()
                        return
        except Exception:
            self._logger.exception("Wake listener worker thread crashed.")
            with self._lock:
                self._close_stream_locked()
                self._is_running = False

    def _detect(self, frame: np.ndarray) -> bool:
        """Run inference on one 1280-sample frame; return True on a valid hit.

        Applies the configurable threshold and cooldown. Prediction errors are
        logged and treated as "no detection" so a transient failure cannot
        crash the worker.
        """
        try:
            predictions = self._model.predict(frame)
        except Exception:
            self._logger.exception(
                "openWakeWord prediction failed; skipping frame."
            )
            return False

        now = time.monotonic()
        if (now - self._last_detection) < self._cooldown:
            return False

        for label, score in predictions.items():
            if score >= self._threshold:
                self._last_detection = now
                self._logger.info(
                    "Wake phrase detected: '%s' score=%.3f (threshold=%.2f).",
                    label,
                    float(score),
                    self._threshold,
                )
                return True
        return False

    def _handle_wake(self) -> None:
        """Stop+release the microphone, then invoke the wake callback."""
        # 1. Stop and release the mic stream first (no browser/mic access here).
        with self._lock:
            self._stop_event.set()
            self._close_stream_locked()
            self._worker = None
            self._is_running = False

        # 2. Signal that a wake occurred. The callback must be a lightweight
        #    signal only (e.g. enqueue a WebSocket event) -- it must not open
        #    any microphone itself.
        self._logger.info(
            "Microphone released after wake; signalling callback."
        )
        callback = self._on_wake
        if callback is not None:
            try:
                callback()
            except Exception:
                self._logger.exception("on_wake callback raised an exception.")


    # -- low-level helpers (call ``_close_stream_locked`` under ``self._lock``) --

    def _close_stream_locked(self) -> None:
        """Stop and close the audio stream, releasing the microphone.

        Must be called while holding ``self._lock``. Best-effort and idempotent.
        """
        if self._stream is not None:
            try:
                if self._stream.active:
                    self._stream.stop()
            except Exception:
                self._logger.debug(
                    "Error stopping audio stream.", exc_info=True
                )
            try:
                self._stream.close()
            except Exception:
                self._logger.debug(
                    "Error closing audio stream.", exc_info=True
                )
            self._stream = None

    def _signal_worker_exit(self) -> None:
        """Best-effort: unblock the worker promptly with a sentinel frame."""
        self._clear_queue()
        try:
            self._queue.put_nowait(None)
        except queue.Full:  # extremely unlikely right after clearing
            # The worker also exits on its own via the stop event within ~0.2 s.
            pass

    def _clear_queue(self) -> None:
        """Drain any queued audio frames (no sentinel)."""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass


# ---------------------------------------------------------------------------
# Default model directory
# ---------------------------------------------------------------------------

def _default_model_dir() -> Path:
    """openWakeWord's bundled model directory (where download_models() writes)."""
    import openwakeword  # imported here so a missing package fails clearly later

    return Path(openwakeword.__file__).resolve().parent / "resources" / "models"


# ---------------------------------------------------------------------------
# Process-wide singleton (prevents duplicate listeners / audio streams)
# ---------------------------------------------------------------------------

_shared_listener: Optional[WakeWordListener] = None
_shared_lock = threading.Lock()


def get_wake_listener(**kwargs) -> WakeWordListener:
    """Return the single shared :class:`WakeWordListener` instance.

    The first call constructs it (loading the model); subsequent calls return
    the same object. This guarantees at most one wake listener -- and therefore
    at most one microphone stream -- exists application-wide. ``kwargs`` are
    only applied on first construction.
    """
    global _shared_listener
    with _shared_lock:
        if _shared_listener is None:
            _shared_listener = WakeWordListener(**kwargs)
        return _shared_listener


# ---------------------------------------------------------------------------
# Manual self-test (run by the user; not executed on import)
# ---------------------------------------------------------------------------

def _list_devices() -> None:
    """Print available audio devices with their input channel counts."""
    print("Available audio devices (index: name [in/out channels]):")
    for index, device in enumerate(sd.query_devices()):
        print(
            f"  {index}: {device['name']} "
            f"[in={device['max_input_channels']}, "
            f"out={device['max_output_channels']}]"
        )
    default_in = sd.default.device[0]
    print(f"\nDefault input device index: {default_in}")


def main() -> None:
    """Interactive self-test: listen for 'Hey Jarvis' and print on detection."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Local 'Hey Jarvis' wake-word listener self-test."
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Microphone device index (default: system default input).",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.5, help="Detection threshold."
    )
    parser.add_argument(
        "--cooldown", type=float, default=2.0, help="Seconds between detections."
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List audio devices and exit.",
    )
    args = parser.parse_args()

    if args.list_devices:
        _list_devices()
        return

    def _on_wake() -> None:
        print(">>> WAKE DETECTED: 'Hey Jarvis' <<<", flush=True)

    listener = WakeWordListener(
        device=args.device,
        threshold=args.threshold,
        cooldown_seconds=args.cooldown,
        on_wake=_on_wake,
    )
    listener.start()
    print("Listening for 'Hey Jarvis'... press Ctrl+C to stop.")
    try:
        while listener.is_running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        listener.stop()


if __name__ == "__main__":
    main()
