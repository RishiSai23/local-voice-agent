"""
backend/voice_loop.py
---------------------
Hands-free voice-interaction orchestrator for the local JARVIS assistant.

Owns the full background lifecycle:

    wake_listening -> acknowledging -> recording_command -> transcribing
        -> thinking -> speaking -> (back to) wake_listening

Design goals (Stage 3B):
- **Backend-owned.** Runs without the React frontend open. A single background
  worker thread drives one command at a time.
- **Sequential microphone ownership.** The wake listener releases the mic on
  detection (guaranteed by ``wake_listener``) before command recording starts;
  wake listening resumes only after recording AND playback finish.
- **Thread-safe state machine.** State is guarded by a lock; stop/cancel are
  checked between every pipeline stage so a stopped/cancelled cycle never
  starts the next stage.
- **Local only.** Reuses the existing ``ask_agent`` (allowlisted tools + Ollama),
  ``synthesise_to_wav`` (Piper), and ``transcribe_wav_bytes`` (Whisper.cpp).
  No cloud APIs, no arbitrary shell execution.
- **Prompt wake callback.** The wake callback only signals the orchestrator and
  returns immediately; all heavy work happens on the worker thread.

Public API
----------
- ``get_voice_assistant(**kwargs)``  -- process-wide singleton accessor.
- ``VoiceAssistant`` -- .start() / .stop() / .cancel() / .status() / .is_active
"""
import sys
import io
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

# Ensure both backend directory and project root are in sys.path
_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_BACKEND_DIR), str(_PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    from backend.agent import (
        OllamaConnectionError,
        OllamaHTTPError,
        OllamaTimeoutError,
        ask_agent,
    )
except ImportError:
    from agent import (
        OllamaConnectionError,
        OllamaHTTPError,
        OllamaTimeoutError,
        ask_agent,
    )

try:
    from backend.tts import (
        PiperSynthesisError,
        PiperUnavailableError,
        synthesise_to_wav,
        get_last_tts_engine,
        get_last_fallback_reason,
    )
except ImportError:
    from tts import (
        PiperSynthesisError,
        PiperUnavailableError,
        synthesise_to_wav,
        get_last_tts_engine,
        get_last_fallback_reason,
    )

try:
    from backend.voice_agent import (
        WhisperTranscriptionError,
        WhisperUnavailableError,
        transcribe_wav_bytes,
    )
except ImportError:
    from voice_agent import (
        WhisperTranscriptionError,
        WhisperUnavailableError,
        transcribe_wav_bytes,
    )

try:
    from backend.wake_listener import (
        WakeListenerError,
        WakeListenerSetupError,
        get_wake_listener,
    )
except ImportError:
    from wake_listener import (
        WakeListenerError,
        WakeListenerSetupError,
        get_wake_listener,
    )

try:
    from backend.conversation_store import (
        add_interaction,
        get_active_conversation_id,
    )
except ImportError:
    from conversation_store import (
        add_interaction,
        get_active_conversation_id,
    )

try:
    from backend.diagnostics import VoiceCycleTiming, get_diagnostics_manager
except ImportError:
    from diagnostics import VoiceCycleTiming, get_diagnostics_manager



# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Capture format (must match Whisper's expected input and the wake listener).
SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"

# Audio chunk used for RMS-based speech/silence detection (100 ms).
CHUNK_DURATION = 0.1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)

# RMS amplitude threshold above which a chunk counts as speech. Mirrors the
# proven value used by the standalone voice agent.
SPEECH_THRESHOLD = 150.0

# Lifecycle states (mirrors the inspection-report state machine).
STATE_STOPPED = "stopped"
STATE_WAKE_LISTENING = "wake_listening"
STATE_ACKNOWLEDGING = "acknowledging"
STATE_RECORDING = "recording_command"
STATE_TRANSCRIBING = "transcribing"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_ERROR = "error"

# Short spoken acknowledgement played after a wake detection.
ACK_TEXT = "Yes?"

# Message spoken when a command produced no usable speech.
NO_SPEECH_TEXT = "I didn't catch that."

# Settle delay after the wake listener releases the mic before the command
# recorder opens it (avoids WASAPI device-busy races on Windows).
MIC_SETTLE_SECONDS = 0.15

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VoiceAssistant orchestrator
# ---------------------------------------------------------------------------

class VoiceAssistant:
    """Background hands-free voice loop built on the existing local pipeline.

    A single worker thread runs the command cycle. All state transitions and
    the wake-event flag are guarded by ``self._lock``; stop/cancel use
    ``threading.Event`` objects that are polled between every stage.
    """

    def __init__(
        self,
        *,
        device: Optional[int] = None,
        silence_duration: float = 1.0,
        speech_start_timeout: float = 8.0,
        max_recording: float = 15.0,
        speak_on_no_speech: bool = True,
        join_timeout: float = 5.0,
    ) -> None:
        """
        Args:
            device: Microphone device index for command capture. ``None`` uses
                the system default input (validated before use; never assumed).
            silence_duration: Seconds of continuous silence that end a command.
            speech_start_timeout: Seconds to wait for speech to begin before
                giving up on the current command (returns to wake listening).
            max_recording: Hard cap on a single command's recording length.
            speak_on_no_speech: Whether to speak the no-speech prompt when a
                command captured nothing usable.
            join_timeout: Seconds to wait for the worker thread to exit on stop.
        """
        self._device = device
        self._silence_duration = float(silence_duration)
        self._speech_start_timeout = float(speech_start_timeout)
        self._max_recording = float(max_recording)
        self._speak_on_no_speech = bool(speak_on_no_speech)
        self._join_timeout = float(join_timeout)

        # --- lifecycle state (guarded by self._lock) ---
        self._lock = threading.RLock()
        self._state = STATE_STOPPED
        self._last_error: Optional[str] = None

        # --- cross-thread signals ---
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()
        # Set by the wake callback (listener worker thread) to hand off to the
        # orchestrator worker thread.
        self._wake_pending = threading.Event()

        self._worker: Optional[threading.Thread] = None
        # The active command recorder, so stop()/cancel() can interrupt it.
        self._active_stream: Optional[sd.InputStream] = None
        self._recording_active = False
        # The active sounddevice playback stream, so stop() can interrupt it.
        self._active_playback: Optional[sd.OutputStream] = None

        # Pre-synthesized acknowledgement audio cache: (wav_bytes, sample_rate, numpy_data)
        self._cached_ack: Optional[tuple[bytes, int, np.ndarray]] = None
        self._ack_lock = threading.Lock()

    def _get_or_synthesize_ack(self) -> tuple[Optional[tuple[bytes, int, np.ndarray]], float]:
        """Return cached acknowledgement (wav_bytes, sample_rate, audio_data) and synthesis duration in ms.

        If already cached in memory, synthesis duration is 0.0 ms.
        Otherwise, synthesizes ACK_TEXT on-demand and caches the decoded audio for subsequent wake cycles.
        """
        if self._cached_ack is not None:
            return self._cached_ack, 0.0

        with self._ack_lock:
            if self._cached_ack is not None:
                return self._cached_ack, 0.0

            t0 = time.perf_counter()
            try:
                wav_bytes = synthesise_to_wav(ACK_TEXT)
                synth_ms = (time.perf_counter() - t0) * 1000.0
                sample_rate, audio = wavfile.read(io.BytesIO(wav_bytes))
                channels = int(audio.shape[1]) if getattr(audio, "ndim", 1) > 1 else 1
                data = np.ascontiguousarray(audio.reshape(-1, channels))
                if data.dtype == np.float64:
                    data = data.astype(np.float32)
                self._cached_ack = (wav_bytes, sample_rate, data)
                logger.info(
                    "Acknowledgement audio cached: '%s' (synth=%.1fms, frames=%d, sr=%d)",
                    ACK_TEXT,
                    synth_ms,
                    len(data),
                    sample_rate,
                )
                return self._cached_ack, synth_ms
            except Exception as err:
                logger.warning("Failed to synthesize/cache acknowledgement: %s", err)
                return None, (time.perf_counter() - t0) * 1000.0

    def _prewarm_ack(self) -> None:
        """Asynchronously pre-warm the persistent Piper worker and pre-synthesize the acknowledgement."""
        try:
            self._get_or_synthesize_ack()
        except Exception:
            logger.debug("Background acknowledgement prewarm encountered exception", exc_info=True)

    # -- introspection ------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Whether a full voice-assistant session is running (not stopped)."""
        return self._state != STATE_STOPPED

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state = state

    def status(self) -> Dict[str, Any]:
        """Return a traceback-free snapshot of the assistant lifecycle."""
        with self._lock:
            active_id = None
            try:
                active_id = get_active_conversation_id()
            except Exception:
                pass
            return {
                "state": self._state,
                "running": self._state != STATE_STOPPED,
                "error": self._last_error,
                "active_conversation_id": active_id,
            }


    # -- public controls ----------------------------------------------------

    def start(self) -> Dict[str, Any]:
        """Start the full voice-assistant loop. Idempotent.

        Opens the microphone via the wake listener and launches the worker
        thread. Does NOT block: returns immediately with the new state.
        """
        with self._lock:
            if self._state != STATE_STOPPED:
                return {"status": "already_running", "state": self._state}

            # Reset all cross-thread signals for a fresh session.
            self._stop_event.clear()
            self._cancel_event.clear()
            self._wake_pending.clear()
            self._last_error = None
            self._active_stream = None
            self._active_playback = None
            self._recording_active = False

            self._worker = threading.Thread(
                target=self._run, name="voice-assistant", daemon=True
            )
            self._worker.start()
            # Optimistically report wake_listening; the worker confirms/updates.
            self._state = STATE_WAKE_LISTENING

            # Asynchronously warm the Piper worker and pre-cache the acknowledgement audio
            threading.Thread(
                target=self._prewarm_ack,
                name="voice-assistant-ack-prewarm",
                daemon=True,
            ).start()

        logger.info("Voice assistant starting.")
        return {"status": "started", "state": STATE_WAKE_LISTENING}

    def stop(self) -> Dict[str, Any]:
        """Stop the full loop, interrupt recording/playback, release the mic.

        Safe to call repeatedly and from any state. Joins the worker with a
        bounded timeout; a still-running worker is a daemon so it cannot block
        process exit.
        """
        with self._lock:
            if self._state == STATE_STOPPED:
                return {"status": "not_running", "state": STATE_STOPPED}
            self._stop_event.set()
            self._cancel_event.set()
            # Unblock the worker if it is waiting for a wake event.
            self._wake_pending.set()
            worker = self._worker

        # Interrupt any in-flight capture/playback so their loops exit promptly.
        self._interrupt_audio()

        # Release the wake listener's mic if it is currently armed.
        try:
            get_wake_listener().stop()
        except (WakeListenerSetupError, WakeListenerError) as err:
            logger.warning("Wake listener stop during shutdown: %s", err)
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error stopping wake listener on shutdown")

        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self._join_timeout)
            if worker.is_alive():
                logger.warning(
                    "Voice assistant worker did not exit within %.1fs.",
                    self._join_timeout,
                )

        with self._lock:
            self._worker = None
            self._state = STATE_STOPPED
        logger.info("Voice assistant stopped.")
        return {"status": "stopped", "state": STATE_STOPPED}

    def cancel(self) -> Dict[str, Any]:
        """Cancel the active command capture and return to wake listening.

        Only meaningful while recording/acknowledging; otherwise a no-op that
        reports the current state.
        """
        with self._lock:
            state = self._state
            if state in (STATE_ACKNOWLEDGING, STATE_RECORDING):
                self._cancel_event.set()
                self._interrupt_audio()
                logger.info("Command capture cancelled (was %s).", state)
                return {"status": "cancelling", "state": state}
            return {"status": "nothing_to_cancel", "state": state}

    # -- wake callback (runs on the listener worker thread) -----------------

    def _on_wake(self) -> None:
        """Signal the orchestrator that the wake phrase was detected.

        Runs on the wake listener's capture thread. Must return promptly: it
        only records the hand-off flag and timestamps; it does no audio work.
        The wake listener has already released the microphone before this runs.
        """
        logger.info("Wake detected; signalling orchestrator.")
        self._last_wake_monotonic = time.perf_counter()
        self._wake_pending.set()


    # -- worker loop --------------------------------------------------------

    def _run(self) -> None:
        """Worker thread: arm wake listening, then service one command per wake."""
        try:
            while not self._stop_event.is_set():
                # --- Arm the wake listener (opens the mic) ---
                if not self._arm_wake_listener():
                    # Arming failed; record error and back off before retrying
                    # so we never spin or leave the mic in a bad state.
                    self._set_state(STATE_ERROR)
                    if self._stop_event.wait(timeout=2.0):
                        break
                    continue

                self._set_state(STATE_WAKE_LISTENING)
                logger.info("Wake listening armed.")

                # --- Wait for a wake event or a stop ---
                woke = self._wake_pending.wait(timeout=0.5)
                self._wake_pending.clear()
                if self._stop_event.is_set():
                    break
                if not woke:
                    continue

                # --- Service exactly one command, then loop to re-arm ---
                self._command_cycle()

                # Allow hardware to settle after playback before re-arming the mic.
                if not self._stop_event.is_set():
                    time.sleep(MIC_SETTLE_SECONDS)

            # Normal shutdown path.
            self._set_state(STATE_STOPPED)
        except Exception:  # noqa: BLE001
            logger.exception("Voice assistant worker crashed.")
            self._last_error = "Unexpected voice assistant error."
            self._set_state(STATE_ERROR)
            # Best-effort cleanup so the mic is not left locked.
            self._cleanup_audio()
            try:
                get_wake_listener().stop()
            except Exception:  # noqa: BLE001
                logger.exception("Cleanup wake-listener stop failed")
            # Settle into stopped so the assistant can be restarted cleanly.
            self._set_state(STATE_STOPPED)

    def _arm_wake_listener(self) -> bool:
        """Ensure the wake listener is running. Returns False on failure."""
        try:
            listener = get_wake_listener()
            if not listener.is_running:
                # The wake listener releases the mic on detection, so we simply
                # (re)arm it with our prompt callback each cycle.
                listener.start(on_wake=self._on_wake)
            return True
        except (WakeListenerSetupError, WakeListenerError) as err:
            self._last_error = str(err)
            logger.error("Failed to arm wake listener: %s", err)
            return False
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Unexpected error arming wake listener: {err}"
            logger.exception("Unexpected error arming wake listener")
            return False


    # -- one command cycle --------------------------------------------------

    def _command_cycle(self) -> None:
        """Run a single wake -> ack -> record -> transcribe -> think -> speak.

        The wake listener has already released the microphone by the time this
        runs. Stop/cancel are checked before every stage; a stopped or cancelled
        cycle returns without starting the next stage.
        """
        cycle_id = f"voice-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 10000:04d}"
        timing = VoiceCycleTiming(
            cycle_id=cycle_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        t_cycle_start = time.perf_counter()
        timing.wake_detected_time = getattr(self, "_last_wake_monotonic", t_cycle_start)
        timing.command_cycle_start_time = t_cycle_start

        try:
            # 1. Acknowledge ("Yes?"). Non-fatal if TTS is unavailable.
            if self._stop_event.is_set() or self._cancel_event.is_set():
                return
            self._set_state(STATE_ACKNOWLEDGING)
            t_ack_start = time.perf_counter()

            ack_data, synth_ms = self._get_or_synthesize_ack()
            timing.ack_synth_ms = synth_ms

            t_ack_play_start = time.perf_counter()
            timing.wake_to_ack_start_ms = max(
                0.0, (t_ack_play_start - timing.wake_detected_time) * 1000.0
            )

            if ack_data is not None and not (self._stop_event.is_set() or self._cancel_event.is_set()):
                _, sample_rate, data = ack_data
                self._play_blocking(data, sample_rate, fatal=False)
            elif not (self._stop_event.is_set() or self._cancel_event.is_set()):
                # Fallback to standard speak if cache failed
                self._speak(ACK_TEXT, fatal=False)

            t_ack_end = time.perf_counter()
            timing.ack_play_ms = max(0.0, (t_ack_end - t_ack_play_start) * 1000.0)
            timing.ack_duration_ms = max(0.0, (t_ack_end - t_ack_start) * 1000.0)
            timing.settle_delay_ms = MIC_SETTLE_SECONDS * 1000.0

            # 2. Record one spoken command. The wake mic is already released; add a
            #    small settle delay to avoid a WASAPI device-busy race.
            if self._stop_event.is_set() or self._cancel_event.is_set():
                return
            time.sleep(MIC_SETTLE_SECONDS)
            self._set_state(STATE_RECORDING)
            timing.recording_start_time = time.perf_counter()

            wav_bytes = self._record_command()
            rec_meta = getattr(self, "_last_recording_meta", {})
            timing.speech_start_time = rec_meta.get("speech_start_time")
            timing.speech_end_time = rec_meta.get("speech_end_time")
            timing.recording_end_time = rec_meta.get("recording_end_time", time.perf_counter())

            if wav_bytes is None:
                # Cancelled, stopped, mic error, or no speech captured.
                return

            # 3. Transcribe via the shared Whisper helper.
            if self._stop_event.is_set() or self._cancel_event.is_set():
                return
            self._set_state(STATE_TRANSCRIBING)
            timing.whisper_start_time = time.perf_counter()
            try:
                transcript = transcribe_wav_bytes(wav_bytes)
            except (WhisperUnavailableError, WhisperTranscriptionError) as err:
                self._last_error = str(err)
                logger.error("Transcription failed: %s", err)
                self._speak("Sorry, I couldn't understand that.", fatal=False)
                self._set_state(STATE_ERROR)
                return
            except Exception as err:  # noqa: BLE001
                self._last_error = f"Unexpected transcription error: {err}"
                logger.exception("Unexpected transcription error")
                self._set_state(STATE_ERROR)
                return
            finally:
                timing.whisper_end_time = time.perf_counter()
                timing.transcript_char_count = len(transcript or "") if "transcript" in locals() else 0

            if not transcript or not transcript.strip():
                logger.info("No speech captured for this command.")
                if self._speak_on_no_speech and not self._stop_event.is_set():
                    self._speak(NO_SPEECH_TEXT, fatal=False)
                return

            # 4. Think via the existing allowlisted agent / Ollama workflow.
            if self._stop_event.is_set() or self._cancel_event.is_set():
                return
            self._set_state(STATE_THINKING)
            timing.ollama_start_time = time.perf_counter()
            try:
                result = ask_agent(transcript, return_diagnostics=True)
                reply = (result or {}).get("reply", "").strip()
            except (OllamaConnectionError, OllamaTimeoutError, OllamaHTTPError) as err:
                self._last_error = str(err)
                logger.error("Agent error: %s", err)
                self._speak("I couldn't reach my local model.", fatal=False)
                self._set_state(STATE_ERROR)
                return
            except Exception as err:  # noqa: BLE001
                self._last_error = f"Unexpected agent error: {err}"
                logger.exception("Unexpected agent error")
                self._set_state(STATE_ERROR)
                return
            finally:
                timing.ollama_end_time = time.perf_counter()

            diag = (result or {}).get("diagnostics")
            if diag:
                timing.ollama_first_token_time = diag.get("first_token_time")
                timing.ollama_time_to_first_token_ms = diag.get("time_to_first_token_ms")
                timing.ollama_load_duration_ms = diag.get("load_duration_ms", 0.0)
                timing.ollama_prompt_eval_duration_ms = diag.get("prompt_eval_duration_ms", 0.0)
                timing.ollama_eval_duration_ms = diag.get("eval_duration_ms", 0.0)
                timing.ollama_prompt_eval_count = diag.get("prompt_eval_count", 0)
                timing.ollama_eval_count = diag.get("eval_count", 0)
                timing.is_cold_start = diag.get("is_cold_start", False)
                timing.ollama_model = diag.get("model", "qwen3:8b")

            if not reply:
                logger.info("Agent returned an empty reply; nothing to speak.")
                return

            timing.reply_char_count = len(reply)

            # Stage 4B: Persist transcribed question & assistant answer into active conversation
            try:
                active_conv_id = get_active_conversation_id()
                metadata = {
                    "source": "voice",
                    "toolCall": (result or {}).get("toolCall"),
                    "cycle_id": cycle_id,
                }
                add_interaction(active_conv_id, transcript.strip(), reply, metadata=metadata)
                logger.info("Persisted voice interaction to conversation %s", active_conv_id)
            except Exception as store_err:
                logger.exception("Failed to persist voice interaction to conversation: %s", store_err)

            # 5. Speak the reply via the existing Piper TTS.
            if self._stop_event.is_set() or self._cancel_event.is_set():
                return
            self._set_state(STATE_SPEAKING)
            timing.tts_start_time = time.perf_counter()
            self._speak(reply, fatal=True)
            timing.tts_end_time = getattr(self, "_last_playback_start_perf", time.perf_counter())
            timing.playback_start_time = getattr(self, "_last_playback_start_perf", timing.tts_end_time)
            timing.tts_engine = get_last_tts_engine()
            timing.tts_fallback_reason = get_last_fallback_reason()
            if timing.tts_engine == "cli-fallback" and timing.tts_fallback_reason:
                timing.notes.append(f"TTS CLI fallback reason: {timing.tts_fallback_reason}")

            # Record and log complete diagnostic timing report
            get_diagnostics_manager().record_cycle(timing)
        finally:
            self._cancel_event.clear()


    # -- audio: recording ---------------------------------------------------

    def _resolve_device(self) -> Optional[int]:
        """Validate and return the capture device, or raise WakeListenerError.

        ``None`` -> system default input. An explicit index is range-checked and
        checked for input channels. Never assumes a specific index.
        """
        if self._device is not None:
            try:
                devices = sd.query_devices()
            except Exception as err:  # noqa: BLE001
                raise WakeListenerError(f"Could not query audio devices: {err}") from err
            if self._device < 0 or self._device >= len(devices):
                raise WakeListenerError(
                    f"Requested microphone device index {self._device} is out of "
                    f"range; {len(devices)} device(s) available."
                )
            if int(devices[self._device].get("max_input_channels", 0)) < 1:
                raise WakeListenerError(
                    f"Device index {self._device} has no input channels."
                )
            return self._device

        try:
            default_input = sd.default.device[0]
        except Exception:  # noqa: BLE001
            default_input = -1
        if default_input is None or default_input < 0:
            raise WakeListenerError("No default input microphone is available.")
        return default_input

    def _record_command(self) -> Optional[bytes]:
        """Record one command with silence/start/max-duration handling.

        Returns in-memory 16-bit/16 kHz/mono WAV bytes, or ``None`` when the
        capture was cancelled/stopped, the mic failed, or no speech was heard.
        Cancellable: stop/cancel interrupt the loop promptly.
        """
        audio_queue: "queue.Queue" = queue.Queue()
        frames: list = []
        speaking = False
        silence_time = 0.0
        start_time: Optional[float] = None
        wait_start = time.monotonic()

        def _callback(indata, frame_count, time_info, status) -> None:
            if status:
                logger.warning("Recorder stream status: %s", status)
            try:
                audio_queue.put_nowait(indata.copy())
            except queue.Full:
                pass

        try:
            device = self._resolve_device()
        except WakeListenerError as err:
            self._last_error = str(err)
            logger.error("Cannot record command: %s", err)
            return None

        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                blocksize=CHUNK_SIZE,
                device=device,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=_callback,
            )
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Failed to open microphone for recording: {err}"
            logger.exception("Failed to open microphone for recording")
            return None

        with self._lock:
            self._active_stream = stream
            self._recording_active = True
        stream.start()
        logger.info("Recording command (device=%r).", device)

        speech_start_perf: Optional[float] = None
        speech_end_perf: Optional[float] = None

        try:
            while True:
                if self._stop_event.is_set() or self._cancel_event.is_set():
                    logger.info("Recording interrupted by stop/cancel.")
                    return None
                try:
                    chunk = audio_queue.get(timeout=0.2)
                except queue.Empty:
                    # Give up if no speech has started within the timeout.
                    if not speaking and (time.monotonic() - wait_start) >= self._speech_start_timeout:
                        logger.info("No speech started within timeout.")
                        return None
                    continue

                level = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                if level > SPEECH_THRESHOLD:
                    if not speaking:
                        speaking = True
                        start_time = time.monotonic()
                        speech_start_perf = time.perf_counter()
                        logger.info("Speech detected; capturing command.")
                    silence_time = 0.0
                    speech_end_perf = time.perf_counter()
                    frames.append(chunk)
                elif speaking:
                    frames.append(chunk)
                    silence_time += CHUNK_DURATION
                    if silence_time >= self._silence_duration:
                        logger.info("Silence detected; command complete.")
                        break

                if not speaking and (time.monotonic() - wait_start) >= self._speech_start_timeout:
                    logger.info("No speech started within timeout.")
                    return None
                if speaking and start_time is not None and (time.monotonic() - start_time) >= self._max_recording:
                    logger.info("Maximum recording duration reached.")
                    break
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Microphone error during recording: {err}"
            logger.exception("Microphone error during recording")
            return None
        finally:
            recording_end_perf = time.perf_counter()
            self._last_recording_meta = {
                "speech_start_time": speech_start_perf if speaking else None,
                "speech_end_time": speech_end_perf if speaking else None,
                "recording_end_time": recording_end_perf,
            }
            # Always stop/close the capture stream and release the mic.
            try:
                if stream.active:
                    stream.stop()
            except Exception:  # noqa: BLE001
                logger.debug("Error stopping recorder stream.", exc_info=True)
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                logger.debug("Error closing recorder stream.", exc_info=True)
            with self._lock:
                self._active_stream = None
                self._recording_active = False

        if not frames:
            return None

        audio = np.concatenate(frames, axis=0)
        buffer = io.BytesIO()
        wavfile.write(buffer, SAMPLE_RATE, audio)
        return buffer.getvalue()


    # -- audio: playback ----------------------------------------------------

    def _speak(self, text: str, *, fatal: bool) -> None:
        """Synthesise *text* with Piper and play it, allowing interruption.

        On failure: if ``fatal`` is False (e.g. the "Yes?" ack or an error
        notice) the failure is logged and swallowed so the loop continues; if
        ``fatal`` is True (speaking the assistant's reply) the error is recorded
        and the state moves to ``error`` for recovery. Playback is skipped
        entirely if a stop/cancel was requested.
        """
        if self._stop_event.is_set() or self._cancel_event.is_set():
            return
        try:
            wav_bytes = synthesise_to_wav(text)
        except (PiperUnavailableError, PiperSynthesisError, ValueError) as err:
            self._last_error = str(err)
            if fatal:
                logger.error("TTS failed while speaking reply: %s", err)
                self._set_state(STATE_ERROR)
            else:
                logger.warning("TTS unavailable for prompt: %s", err)
            return
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Unexpected TTS error: {err}"
            if fatal:
                logger.exception("Unexpected TTS error while speaking reply")
                self._set_state(STATE_ERROR)
            else:
                logger.warning("Unexpected TTS error for prompt: %s", err)
            return

        # Decode the WAV bytes and play through the default output device,
        # polling so a stop/cancel can interrupt mid-playback.
        try:
            sample_rate, audio = wavfile.read(io.BytesIO(wav_bytes))
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Failed to decode synthesised audio: {err}"
            logger.exception("Failed to decode synthesised audio")
            if fatal:
                self._set_state(STATE_ERROR)
            return

        self._play_blocking(audio, sample_rate, fatal=fatal)

    def _play_blocking(self, audio, sample_rate: int, *, fatal: bool) -> None:
        """Play *audio* and wait until playback actually completes or is interrupted.

        Uses an ``sd.OutputStream`` with an audio-feeding callback and a
        finished callback event so playback progress is tracked accurately.
        The stream is registered in ``self._active_playback`` so that
        ``stop()``, ``cancel()``, or ``_cleanup_audio()`` can abort it promptly.
        """
        if self._stop_event.is_set() or self._cancel_event.is_set():
            return

        if audio is None or len(audio) == 0:
            logger.info("Playback skipped: synthesized audio is empty.")
            return

        channels = int(audio.shape[1]) if getattr(audio, "ndim", 1) > 1 else 1
        data = np.ascontiguousarray(audio.reshape(-1, channels))
        if data.dtype == np.float64:
            data = data.astype(np.float32)

        total_frames = len(data)
        current_frame = 0
        playback_finished = threading.Event()

        def _callback(outdata, frames, time_info, status) -> None:
            nonlocal current_frame
            if status:
                logger.warning("Playback stream status: %s", status)
            chunk = data[current_frame : current_frame + frames]
            chunk_len = len(chunk)
            if chunk_len > 0:
                outdata[:chunk_len] = chunk
            if chunk_len < frames:
                outdata[chunk_len:] = 0
                raise sd.CallbackStop()
            current_frame += chunk_len

        def _finished_callback() -> None:
            playback_finished.set()

        try:
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype=data.dtype,
                callback=_callback,
                finished_callback=_finished_callback,
            )
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Failed to start audio playback: {err}"
            logger.exception("Failed to start audio playback")
            if fatal:
                self._set_state(STATE_ERROR)
            return

        with self._lock:
            self._active_playback = stream

        logger.info(
            "Playback started (sample_rate=%d, channels=%d, frames=%d).",
            sample_rate,
            channels,
            total_frames,
        )

        interrupted = False
        try:
            stream.start()
            self._last_playback_start_perf = time.perf_counter()
            while not playback_finished.wait(timeout=0.05):
                with self._lock:
                    interrupted = self._stop_event.is_set() or self._cancel_event.is_set()
                if interrupted or not stream.active:
                    break

            if interrupted:
                logger.info("Playback interrupted by stop/cancel.")
                try:
                    if stream.active:
                        stream.abort()
                except Exception:  # noqa: BLE001
                    logger.debug("Error aborting playback stream.", exc_info=True)
                try:
                    sd.stop()
                except Exception:  # noqa: BLE001
                    logger.debug("Error stopping sounddevice playback.", exc_info=True)
            else:
                logger.info("Playback completed.")
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Audio playback failed: {err}"
            logger.exception("Audio playback failed")
            if fatal:
                self._set_state(STATE_ERROR)
        finally:
            try:
                if stream.active:
                    stream.stop()
            except Exception:  # noqa: BLE001
                logger.debug("Error stopping playback stream.", exc_info=True)
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                logger.debug("Error closing playback stream.", exc_info=True)
            with self._lock:
                self._active_playback = None

    # -- cleanup helpers ----------------------------------------------------

    def _interrupt_audio(self) -> None:
        """Interrupt any in-flight capture or playback stream (best-effort)."""
        with self._lock:
            stream = self._active_stream
            playback = self._active_playback
        try:
            if stream is not None and stream.active:
                stream.stop()
        except Exception:  # noqa: BLE001
            logger.debug("Error interrupting recorder stream.", exc_info=True)
        try:
            if playback is not None and playback.active:
                playback.abort()
        except Exception:  # noqa: BLE001
            logger.debug("Error interrupting playback stream.", exc_info=True)
        try:
            sd.stop()
        except Exception:  # noqa: BLE001
            logger.debug("Error interrupting playback.", exc_info=True)

    def _cleanup_audio(self) -> None:
        """Stop and release any audio streams (best-effort, idempotent)."""
        self._interrupt_audio()
        with self._lock:
            stream = self._active_stream
            playback = self._active_playback
            self._active_stream = None
            self._active_playback = None
            self._recording_active = False
        try:
            if stream is not None:
                stream.close()
        except Exception:  # noqa: BLE001
            logger.debug("Error closing recorder stream during cleanup.", exc_info=True)
        try:
            if playback is not None:
                playback.close()
        except Exception:  # noqa: BLE001
            logger.debug("Error closing playback stream during cleanup.", exc_info=True)


# ---------------------------------------------------------------------------
# Process-wide singleton (prevents duplicate sessions / microphone streams)
# ---------------------------------------------------------------------------

_shared_assistant: Optional[VoiceAssistant] = None
_shared_lock = threading.Lock()


def get_voice_assistant(**kwargs) -> VoiceAssistant:
    """Return the single shared :class:`VoiceAssistant` instance.

    The first call constructs it; subsequent calls return the same object, so at
    most one voice-assistant session (and one microphone owner) exists. ``kwargs``
    apply only on first construction.
    """
    global _shared_assistant
    with _shared_lock:
        if _shared_assistant is None:
            _shared_assistant = VoiceAssistant(**kwargs)
        return _shared_assistant
