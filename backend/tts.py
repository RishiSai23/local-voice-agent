import atexit
import collections
import inspect
import io
import json
import os
import secrets
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import wave
from pathlib import Path
import sys
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Ensure singleton identity across both 'backend.tts' and 'tts' imports
_current_module = sys.modules.get(__name__)
if _current_module is not None:
    sys.modules["backend.tts"] = _current_module
    sys.modules["tts"] = _current_module

from voice_agent import PIPER, VOICE  # noqa: E402

try:
    from backend.sanitizer import clean_markdown
except ImportError:
    from sanitizer import clean_markdown

# Optional import of Piper Python API if available directly in the current environment
try:
    from piper.voice import PiperVoice
    from piper.config import SynthesisConfig
    _HAS_PIPER_PACKAGE = True
except ImportError:
    try:
        from piper.voice import PiperVoice
        SynthesisConfig = None
        _HAS_PIPER_PACKAGE = True
    except ImportError:
        PiperVoice = None
        SynthesisConfig = None
        _HAS_PIPER_PACKAGE = False

# Absolute path to the .onnx model file sitting at the project root.
_ONNX_PATH = _PROJECT_ROOT / f"{VOICE}.onnx"

# Default path to the global Python installation that contains piper-tts
_DEFAULT_GLOBAL_PYTHON = r"C:\Users\rishi\AppData\Local\Programs\Python\Python310\python.exe"


def resolve_piper_python_exe() -> str:
    """Resolve Python executable for running the Piper worker.

    Checks candidate paths in priority order:
    1. PIPER_WORKER_PYTHON or PIPER_PYTHON_EXE environment variables.
    2. Python directory paired with PIPER executable (e.g. Python310 if PIPER is in Python310\\Scripts).
    3. Configured _DEFAULT_GLOBAL_PYTHON path.
    4. LocalAppData Python installations (%LOCALAPPDATA%\\Programs\\Python\\Python3*\\python.exe).
    5. PATH lookup for python / python3.
    6. Current running Python interpreter (sys.executable).
    """
    for env_var in ("PIPER_WORKER_PYTHON", "PIPER_PYTHON_EXE"):
        val = os.environ.get(env_var)
        if val and Path(val).is_file():
            return val

    if Path(PIPER).is_file():
        try:
            candidate = Path(PIPER).resolve().parent.parent / "python.exe"
            if candidate.is_file():
                return str(candidate)
        except Exception:
            pass

    if Path(_DEFAULT_GLOBAL_PYTHON).is_file():
        return _DEFAULT_GLOBAL_PYTHON

    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        py_dir = Path(local_app) / "Programs" / "Python"
        if py_dir.is_dir():
            try:
                for p in sorted(py_dir.glob("Python3*/python.exe"), reverse=True):
                    if p.is_file():
                        return str(p)
            except Exception:
                pass

    try:
        import shutil
        which_py = shutil.which("python") or shutil.which("python3")
        if which_py and Path(which_py).is_file():
            return which_py
    except Exception:
        pass

    if Path(sys.executable).is_file():
        return sys.executable

    return _DEFAULT_GLOBAL_PYTHON


class PiperUnavailableError(RuntimeError):
    """Raised when the Piper executable or voice model cannot be found."""


class PiperSynthesisError(RuntimeError):
    """Raised when Piper exits with a non-zero return code or synthesis fails."""


def _sanitise_text(text: str) -> str:
    """Sanitise text for Piper TTS input, stripping Markdown and unsupported characters."""
    text = clean_markdown(text)
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = "".join(
        char
        for char in text
        if unicodedata.category(char) not in {"Cs", "So", "Sk", "Cf"}
    ).strip()
    return text


def _resolve_model_arg() -> str:
    """Return the best --model argument for the Piper CLI."""
    if _ONNX_PATH.is_file():
        return str(_ONNX_PATH)
    return VOICE


# Mutex strictly serializing Piper synthesis across worker threads.
_tts_lock = threading.Lock()

# Cached path validations
_cached_model_arg: Optional[str] = None
_cached_piper_checked: bool = False

# Last engine used during synthesis ("direct-in-memory", "persistent-worker", "cli-fallback", or "none")
_last_engine_used: str = "none"
_last_fallback_reason: Optional[str] = None


def get_last_tts_engine() -> str:
    """Return the TTS engine used for the most recent synthesis call."""
    return _last_engine_used


def get_last_fallback_reason() -> Optional[str]:
    """Return the diagnostic failure reason if the most recent synthesis fell back to CLI."""
    return _last_fallback_reason


# In-process engine state (active only if piper-tts is installed in the host interpreter)
_in_memory_voice: Optional[Any] = None
_in_memory_init_failed: bool = False
_in_memory_init_error: Optional[str] = None
_syn_config: Optional[Any] = None
_syn_config_silence_applied: bool = False


def _get_validated_model_arg() -> str:
    """Return the resolved model path, validating existence on first use."""
    global _cached_model_arg
    if _cached_model_arg is None:
        model_arg = _resolve_model_arg()
        if model_arg != VOICE and not Path(model_arg).is_file():
            raise PiperUnavailableError(
                f"Piper voice model not found at '{model_arg}'. "
                "Ensure the .onnx voice model is present in the project root."
            )
        _cached_model_arg = model_arg
    return _cached_model_arg


def _check_piper_executable() -> None:
    """Verify Piper executable presence on first use."""
    global _cached_piper_checked
    if not _cached_piper_checked:
        if not Path(PIPER).is_file():
            raise PiperUnavailableError(
                f"Piper executable not found at '{PIPER}'. "
                "Ensure Piper is installed at the configured path."
            )
        _cached_piper_checked = True


def _create_synthesis_config(sentence_silence: float = 0.35) -> tuple[Optional[Any], bool]:
    """Instantiate SynthesisConfig with sentence_silence if supported.

    Returns (config_or_none, was_silence_applied).
    """
    if SynthesisConfig is None:
        return None, False

    # Attempt 1: Direct keyword argument in constructor
    try:
        return SynthesisConfig(sentence_silence=sentence_silence), True
    except (TypeError, AttributeError):
        pass

    # Attempt 2: Default constructor + attribute check
    try:
        cfg = SynthesisConfig()
        if hasattr(cfg, "sentence_silence"):
            setattr(cfg, "sentence_silence", sentence_silence)
            return cfg, True
        return cfg, False
    except Exception:
        pass

    return None, False


def _get_in_memory_voice() -> Optional[Any]:
    """Lazily initialize and return the in-process PiperVoice instance if available."""
    global _in_memory_voice, _in_memory_init_failed, _in_memory_init_error, _syn_config, _syn_config_silence_applied
    if not _HAS_PIPER_PACKAGE or _in_memory_init_failed:
        return None

    if _in_memory_voice is not None:
        return _in_memory_voice

    with _tts_lock:
        if _in_memory_voice is not None or _in_memory_init_failed:
            return _in_memory_voice

        if not _ONNX_PATH.is_file():
            _in_memory_init_failed = True
            _in_memory_init_error = f"Model file not found at {_ONNX_PATH}"
            return None

        try:
            t_load_start = time.perf_counter()
            _in_memory_voice = PiperVoice.load(str(_ONNX_PATH))
            load_duration = time.perf_counter() - t_load_start
            _syn_config, _syn_config_silence_applied = _create_synthesis_config(sentence_silence=0.35)
            silence_status = "silence=0.35s" if _syn_config_silence_applied else "silence=unsupported_omitted"
            print(
                f"[TTS Engine] Direct in-memory voice model loaded in {load_duration * 1000:.1f}ms "
                f"({_ONNX_PATH.name}, {silence_status})"
            )
            return _in_memory_voice
        except Exception as exc:
            _in_memory_init_failed = True
            _in_memory_init_error = str(exc)
            print(f"[TTS Engine] Failed to load direct in-memory Piper voice: {exc}")
            return None


def _get_voice_audio_params(voice: Any) -> tuple[int, int, int]:
    """Return (channels, sample_width, sample_rate) appropriate for the loaded Piper voice model."""
    cfg = getattr(voice, "config", None)
    sample_rate = 22050
    if cfg is not None:
        sr = getattr(cfg, "sample_rate", None)
        if isinstance(sr, int) and sr > 0:
            sample_rate = sr
    return 1, 2, sample_rate


def _create_wav_writer(
    buf: io.BytesIO,
    sample_rate: int,
    channels: int = 1,
    sample_width: int = 2,
) -> wave.Wave_write:
    """Create and return a wave.Wave_write instance with all required WAV headers initialized."""
    wav = wave.open(buf, "wb")
    wav.setnchannels(channels)
    wav.setsampwidth(sample_width)
    wav.setframerate(sample_rate)
    return wav


def _populate_existing_wav_writer(wav: wave.Wave_write, wav_bytes: bytes) -> None:
    """Helper to populate an externally-supplied wave.Wave_write instance with frames from raw WAV bytes."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        if not getattr(wav, "_nchannels", 0):
            wav.setnchannels(r.getnchannels())
        if not getattr(wav, "_sampwidth", 0):
            wav.setsampwidth(r.getsampwidth())
        if not getattr(wav, "_framerate", 0):
            wav.setframerate(r.getframerate())
        wav.writeframes(r.readframes(r.getnframes()))


def _synthesize_voice(
    voice: Any,
    text: str,
    wav: Optional[wave.Wave_write] = None,
    silence: float = 0.35,
) -> bytes:
    """Synthesize text into WAV bytes across Piper Python library API versions."""
    global _syn_config
    channels, sample_width, sample_rate = _get_voice_audio_params(voice)
    last_err: Optional[Exception] = None

    # 1. Primary Installed API: voice.synthesize_wav(text, wav_file, syn_config=...)
    if hasattr(voice, "synthesize_wav") and callable(getattr(voice, "synthesize_wav")):
        if _syn_config is not None:
            try:
                buf = io.BytesIO()
                with _create_wav_writer(buf, sample_rate, channels, sample_width) as w:
                    voice.synthesize_wav(text, w, syn_config=_syn_config)
                wav_bytes = buf.getvalue()
                if len(wav_bytes) > 44 and wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE":
                    if wav is not None:
                        _populate_existing_wav_writer(wav, wav_bytes)
                    return wav_bytes
            except TypeError:
                _syn_config = None
            except Exception as exc:
                last_err = exc

        try:
            buf = io.BytesIO()
            with _create_wav_writer(buf, sample_rate, channels, sample_width) as w:
                voice.synthesize_wav(text, w)
            wav_bytes = buf.getvalue()
            if len(wav_bytes) > 44 and wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE":
                if wav is not None:
                    _populate_existing_wav_writer(wav, wav_bytes)
                return wav_bytes
        except Exception as exc:
            last_err = exc

    # 2. Modern Generator API: voice.synthesize(text, ...) -> Iterable[AudioChunk]
    if hasattr(voice, "synthesize") and callable(getattr(voice, "synthesize")):
        try:
            chunks = None
            if _syn_config is not None:
                try:
                    chunks = voice.synthesize(text, syn_config=_syn_config)
                except TypeError:
                    pass
            if chunks is None:
                chunks = voice.synthesize(text)

            if hasattr(chunks, "__iter__"):
                buf = io.BytesIO()
                with _create_wav_writer(buf, sample_rate, channels, sample_width) as w:
                    has_frames = False
                    for chunk in chunks:
                        if hasattr(chunk, "audio_int16_bytes") and chunk.audio_int16_bytes:
                            w.writeframes(chunk.audio_int16_bytes)
                            has_frames = True
                        elif hasattr(chunk, "audio_float_array") and chunk.audio_float_array is not None:
                            import numpy as np
                            pcm = (np.clip(chunk.audio_float_array, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                            w.writeframes(pcm)
                            has_frames = True
                        elif isinstance(chunk, (bytes, bytearray)):
                            w.writeframes(chunk)
                            has_frames = True

                wav_bytes = buf.getvalue()
                if has_frames and len(wav_bytes) > 44 and wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE":
                    if wav is not None:
                        _populate_existing_wav_writer(wav, wav_bytes)
                    return wav_bytes
        except Exception as exc:
            last_err = exc

    # 3. Legacy Piper C/Python API: voice.synthesize(text, wav_file, ...)
    if hasattr(voice, "synthesize") and callable(getattr(voice, "synthesize")):
        if not inspect.isgeneratorfunction(getattr(voice, "synthesize")):
            try:
                buf = io.BytesIO()
                with _create_wav_writer(buf, sample_rate, channels, sample_width) as w:
                    voice.synthesize(text, w, sentence_silence=silence)
                wav_bytes = buf.getvalue()
                if len(wav_bytes) > 44 and wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE":
                    if wav is not None:
                        _populate_existing_wav_writer(wav, wav_bytes)
                    return wav_bytes
            except TypeError:
                pass
            except Exception as exc:
                last_err = exc

            try:
                buf = io.BytesIO()
                with _create_wav_writer(buf, sample_rate, channels, sample_width) as w:
                    voice.synthesize(text, w)
                wav_bytes = buf.getvalue()
                if len(wav_bytes) > 44 and wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE":
                    if wav is not None:
                        _populate_existing_wav_writer(wav, wav_bytes)
                    return wav_bytes
            except TypeError:
                pass
            except Exception as exc:
                last_err = exc

    # 4. Direct callable voice instance fallback: voice(text, wav_file)
    if callable(voice):
        try:
            buf = io.BytesIO()
            with _create_wav_writer(buf, sample_rate, channels, sample_width) as w:
                voice(text, w)
            wav_bytes = buf.getvalue()
            if len(wav_bytes) > 44 and wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE":
                if wav is not None:
                    _populate_existing_wav_writer(wav, wav_bytes)
                return wav_bytes
        except Exception as exc:
            last_err = exc

    err_suffix = f": {type(last_err).__name__}: {last_err}" if last_err is not None else ""
    raise AttributeError(
        f"Piper voice object of type '{type(voice).__name__}' could not synthesize text across supported APIs{err_suffix}"
    )


def _synthesise_in_memory(voice: Any, text: str) -> bytes:
    """Synthesise text to WAV bytes directly in-memory."""
    wav_bytes = _synthesize_voice(voice, text, silence=0.35)
    if len(wav_bytes) <= 44 or not (wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE"):
        raise PiperSynthesisError(
            f"Piper direct in-memory synthesis produced invalid WAV header or empty data ({len(wav_bytes)} bytes)."
        )
    return wav_bytes


class _PiperWorkerClient:
    """Manages the lifecycle and HTTP IPC communication with the persistent Piper worker."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._host: str = os.environ.get("PIPER_WORKER_HOST", "127.0.0.1")
        self._configured_port: int = int(os.environ.get("PIPER_WORKER_PORT", "50055"))
        self._port: Optional[int] = None
        self._token: str = os.environ.get("PIPER_WORKER_AUTH_TOKEN", "")
        self._failed: bool = False
        self._last_failure_time: float = 0.0
        self._failure_reason: Optional[str] = None
        self._startup_timeout: float = float(os.environ.get("PIPER_WORKER_STARTUP_TIMEOUT", "15.0"))
        self._synthesis_timeout: float = float(os.environ.get("PIPER_SYNTHESIS_TIMEOUT", "30.0"))
        self._python_exe: str = resolve_piper_python_exe()
        self._ready_event = threading.Event()
        self._drain_thread: Optional[threading.Thread] = None
        self._log_history: collections.deque = collections.deque(maxlen=100)

    def is_alive(self) -> bool:
        """Check if worker child process is currently running or adopted on localhost."""
        if self._process is not None:
            return self._process.poll() is None
        return self._port is not None

    def _send_shutdown_http(self, port: int, token: str) -> None:
        """Send HTTP POST /shutdown to a running worker process."""
        try:
            url = f"http://{self._host}:{port}/shutdown"
            req = urllib.request.Request(
                url,
                data=b"{}",
                headers={"X-TTS-Token": token, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                resp.read()
        except Exception:
            pass

    def is_healthy(self, timeout: float = 3.0, check_version: bool = False) -> bool:
        """Check if worker process is alive AND responding to authenticated HTTP /health."""
        if not self.is_alive() or self._port is None:
            return False
        try:
            url = f"http://{self._host}:{self._port}/health"
            req = urllib.request.Request(
                url,
                headers={"X-TTS-Token": self._token},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") != "ready":
                        return False
                    if check_version and data.get("version") != "2.0":
                        return False
                    return True
        except Exception:
            pass
        return False

    def ensure_started(self) -> bool:
        """Ensure the persistent worker process is running, ready, and responsive.

        Returns True if the worker is operational, False otherwise.
        """
        # If already running and healthy, return immediately
        if self.is_alive() and self._port is not None:
            if not self._failed or self.is_healthy(timeout=1.0):
                self._failed = False
                return True

        # Check circuit-breaker: if an existing worker has become healthy, clear the breaker
        if self._failed and (time.time() - self._last_failure_time < 60.0):
            if self.is_healthy(timeout=1.0):
                self._failed = False
                self._failure_reason = None
                return True
            return False

        with self._lock:
            if self.is_alive() and self._port is not None:
                return True

            # Clean up any dead previous process before launching or adopting a new one
            if self._process is not None and self._process.poll() is not None:
                self._shutdown_internal()

            # Check if an existing worker is already running and healthy (e.g. from environment or another module)
            env_port_str = os.environ.get("PIPER_WORKER_PORT")
            env_token = os.environ.get("PIPER_WORKER_AUTH_TOKEN", "")
            probe_ports = []
            if env_port_str and env_port_str.isdigit():
                probe_ports.append(int(env_port_str))
            if self._configured_port not in probe_ports:
                probe_ports.append(self._configured_port)

            for probe_port in probe_ports:
                token_to_try = env_token or self._token
                if token_to_try:
                    prev_port = self._port
                    prev_token = self._token
                    self._port = probe_port
                    self._token = token_to_try
                    if self.is_healthy(timeout=1.5):
                        # Verify the worker is running v2.0 (with synthesize method fix)
                        if not self.is_healthy(timeout=1.5, check_version=True):
                            print(
                                f"[TTS Manager Diagnostic] Worker on port {self._port} is running "
                                "outdated worker script (version mismatch). Shutting down stale process to reload."
                            )
                            self._send_shutdown_http(probe_port, token_to_try)
                            time.sleep(0.5)
                            self._port = None
                            self._token = prev_token
                            break
                        self._failed = False
                        self._failure_reason = None
                        print(
                            f"[TTS Manager Diagnostic] Worker adopted running healthy instance "
                            f"(port={self._port}, v2.0)"
                        )
                        return True
                    # Revert if probe failed
                    self._port = prev_port
                    self._token = prev_token

            t_start = time.perf_counter()
            self._python_exe = resolve_piper_python_exe()
            python_path = Path(self._python_exe)
            if not python_path.is_file():
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = (
                    f"Python executable for Piper worker not found at '{self._python_exe}'. "
                    "Set PIPER_WORKER_PYTHON to the global Python 3.10 executable."
                )
                print(f"[TTS Manager Diagnostic] Worker startup failed: {self._failure_reason}")
                return False

            worker_script = Path(__file__).resolve().parent / "tts_worker.py"
            if not worker_script.is_file():
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = f"Worker script not found at '{worker_script}'"
                print(f"[TTS Manager Diagnostic] Worker startup failed: {self._failure_reason}")
                return False

            if not _ONNX_PATH.is_file():
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = f"Voice model not found at '{_ONNX_PATH}'"
                print(f"[TTS Manager Diagnostic] Worker startup failed: {self._failure_reason}")
                return False

            if not self._token:
                self._token = secrets.token_hex(16)
            os.environ["PIPER_WORKER_AUTH_TOKEN"] = self._token

            self._ready_event.clear()
            self._log_history.clear()
            self._port = None

            cmd = [
                str(python_path),
                str(worker_script),
                "--host", self._host,
                "--port", str(self._configured_port),
                "--token", self._token,
                "--model", str(_ONNX_PATH),
                "--silence", "0.35",
            ]

            try:
                # Merge stdout and stderr and pipe them to prevent OS pipe deadlocks
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as exc:
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = f"Subprocess launch failed: {type(exc).__name__}"
                print(f"[TTS Manager Diagnostic] Worker launch exception: {self._failure_reason}")
                return False

            # Dedicated daemon thread continuously drains pipe to prevent OS buffer fill deadlocks
            def _drain_output() -> None:
                proc = self._process
                if not proc or not proc.stdout:
                    return
                try:
                    for line in iter(proc.stdout.readline, ""):
                        stripped = line.strip()
                        if stripped:
                            self._log_history.append(stripped)
                            if "[TTS_WORKER_READY]" in stripped:
                                for part in stripped.split():
                                    if part.startswith("port="):
                                        try:
                                            self._port = int(part.split("=")[1])
                                        except ValueError:
                                            pass
                                self._ready_event.set()
                except Exception:
                    pass

            self._drain_thread = threading.Thread(target=_drain_output, daemon=True)
            self._drain_thread.start()

            # Bounded readiness wait that detects worker crash immediately instead of hanging
            t_wait_start = time.perf_counter()
            while (time.perf_counter() - t_wait_start) < self._startup_timeout:
                if self._ready_event.wait(timeout=0.1):
                    break
                if self._process is not None and self._process.poll() is not None:
                    # Child process terminated prematurely
                    break

            ready = self._ready_event.is_set()
            if not ready or self._port is None:
                exit_code = self._process.poll() if self._process else None
                recent_logs = " | ".join(list(self._log_history)[-5:]) if self._log_history else "none"
                self._shutdown_internal()
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = (
                    f"Worker startup failed (exit_code={exit_code}, logs: {recent_logs})"
                )
                print(f"[TTS Manager Diagnostic] Worker startup failed: {self._failure_reason}")
                return False

            # Active health check confirmation with retries to prevent false-positive teardown under load
            healthy = False
            for _ in range(3):
                if self.is_healthy(timeout=3.0):
                    healthy = True
                    break
                time.sleep(0.3)

            if not healthy:
                recent_logs = " | ".join(list(self._log_history)[-5:]) if self._log_history else "none"
                self._shutdown_internal()
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = (
                    f"Worker process started on port {self._port} but failed /health check (logs: {recent_logs})"
                )
                print(f"[TTS Manager Diagnostic] Worker health check failed: {self._failure_reason}")
                return False

            startup_ms = (time.perf_counter() - t_start) * 1000
            os.environ["PIPER_WORKER_PORT"] = str(self._port)
            if self._process is not None:
                os.environ["PIPER_WORKER_PID"] = str(self._process.pid)
            print(
                f"[TTS Manager Diagnostic] Worker process started and verified in {startup_ms:.1f}ms "
                f"(pid={self._process.pid if self._process else 'adopted'}, port={self._port})"
            )
            self._failed = False
            self._failure_reason = None
            return True

    def synthesize(self, clean_text: str) -> Optional[tuple[bytes, float, float]]:
        """Synthesise text via worker HTTP endpoint.

        Returns (wav_bytes, rtt_ms, synth_ms) on success, or None on failure.
        """
        if not self.ensure_started():
            return None

        url = f"http://{self._host}:{self._port}/synthesize"
        payload = json.dumps({"text": clean_text}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-TTS-Token": self._token,
            },
            method="POST",
        )

        t_req_start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self._synthesis_timeout) as resp:
                if resp.status != 200:
                    self._failure_reason = f"Worker returned unexpected HTTP {resp.status}"
                    print(
                        f"[TTS Manager Diagnostic] Worker returned HTTP {resp.status} "
                        f"(chars={len(clean_text)})"
                    )
                    return None

                synth_ms_hdr = resp.headers.get("X-Synthesis-Ms")
                try:
                    synth_ms = float(synth_ms_hdr) if synth_ms_hdr else 0.0
                except (ValueError, TypeError):
                    synth_ms = 0.0
                wav_bytes = resp.read()
                rtt_ms = (time.perf_counter() - t_req_start) * 1000

                # Strict validation of received WAV payload
                if len(wav_bytes) <= 44 or not (wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE"):
                    self._failure_reason = f"Worker returned invalid WAV data ({len(wav_bytes)} bytes)"
                    print(
                        f"[TTS Manager Diagnostic] Worker returned invalid WAV data "
                        f"(bytes={len(wav_bytes)}, chars={len(clean_text)})"
                    )
                    if self._process is not None and self._process.poll() is not None:
                        recent_logs = " | ".join(list(self._log_history)[-3:]) if self._log_history else "none"
                        self._failure_reason = f"Worker terminated after delivering invalid WAV (logs: {recent_logs})"
                        self._failed = True
                        self._last_failure_time = time.time()
                        self._shutdown_internal()
                    return None

                self._failure_reason = None
                return wav_bytes, rtt_ms, synth_ms

        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            self._failure_reason = f"Worker HTTP {exc.code} ({err_body or exc.reason})"
            print(
                f"[TTS Manager Diagnostic] Worker synthesis HTTP error: {self._failure_reason} "
                f"(chars={len(clean_text)})"
            )
            return None

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            err_type = type(exc).__name__
            self._failure_reason = f"Worker connection failed ({err_type})"
            print(
                f"[TTS Manager Diagnostic] Worker request failed: {err_type} "
                f"(chars={len(clean_text)})"
            )
            # Determine worker viability; avoid killing a healthy worker on transient socket timeout
            proc_alive = self.is_alive()
            if not proc_alive:
                recent_logs = " | ".join(list(self._log_history)[-3:]) if self._log_history else "none"
                self._failure_reason = f"Worker process terminated unexpectedly (logs: {recent_logs})"
                self._failed = True
                self._last_failure_time = time.time()
                self._shutdown_internal()
            else:
                # Process is still running; verify responsiveness with a resilient timeout before teardown
                time.sleep(0.3)
                if not self.is_healthy(timeout=2.5):
                    recent_logs = " | ".join(list(self._log_history)[-3:]) if self._log_history else "none"
                    self._failure_reason = f"Worker unresponsive after failed synthesis (logs: {recent_logs})"
                    self._failed = True
                    self._last_failure_time = time.time()
                    self._shutdown_internal()
                else:
                    print(f"[TTS Manager Diagnostic] Worker remains alive and healthy on port {self._port}; retaining process.")
            return None

    def shutdown(self) -> None:
        """Idempotently terminate the worker process."""
        with self._lock:
            self._shutdown_internal()

    def _shutdown_internal(self) -> None:
        """Internal shutdown helper executed without deadlock risk."""
        proc = self._process
        port = self._port
        token = self._token
        host = self._host
        self._process = None
        self._port = None

        if proc is None:
            return

        # 1. Attempt graceful HTTP shutdown first if process appears alive
        if port and proc.poll() is None:
            try:
                shutdown_url = f"http://{host}:{port}/shutdown"
                req = urllib.request.Request(
                    shutdown_url,
                    data=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "X-TTS-Token": token,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=0.8):
                    pass
            except Exception:
                pass

        # 2. Bounded wait for clean exit
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            # 3. Terminate if still running
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError):
                # 4. Force kill as last resort
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except OSError:
                    pass

        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

        print("[TTS Manager] Worker process shut down cleanly.")


# Global worker client singleton
_worker_client = _PiperWorkerClient()

# Register clean shutdown on process termination
atexit.register(_worker_client.shutdown)


def _synthesise_subprocess(clean: str, tmp_path: Path) -> bytes:
    """Synthesise text to WAV using one-shot piper.exe subprocess (fallback engine)."""
    model_arg = _get_validated_model_arg()
    result = subprocess.run(
        [
            PIPER,
            "--model", model_arg,
            "--output_file", str(tmp_path),
            "--sentence-silence", "0.35",
        ],
        input=clean,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
        # shell=True is intentionally omitted for security.
    )
    if result.returncode != 0:
        raise PiperSynthesisError(
            f"Piper exited with code {result.returncode}. "
            f"stderr: {result.stderr.strip()}"
        )
    return tmp_path.read_bytes()


def check_piper_available() -> dict:
    """Return a dict describing Piper availability and truthful component health."""
    exe_ok = Path(PIPER).is_file()
    model_arg = _resolve_model_arg()
    model_file_ok = _ONNX_PATH.is_file()
    cli_model_ok = Path(model_arg).is_file() if model_arg != VOICE else False
    cli_ready = exe_ok and (cli_model_ok or model_arg == VOICE)
    worker_alive = _worker_client.is_alive()
    worker_healthy = _worker_client.is_healthy(timeout=1.5) if worker_alive else False
    worker_pid = _worker_client._process.pid if (worker_alive and _worker_client._process) else None

    # Truthful active engine and status assessment:
    # Do not claim persistent-worker is active unless it is genuinely running and responding.
    if worker_healthy:
        active_engine = "persistent-worker"
        worker_status = "running"
    elif worker_alive:
        active_engine = "persistent-worker"
        worker_status = "unhealthy"
    elif _worker_client._failed:
        active_engine = "cli-fallback" if cli_ready else "none"
        worker_status = "failed"
    elif _HAS_PIPER_PACKAGE and _in_memory_voice is not None:
        active_engine = "direct-in-memory"
        worker_status = "in_memory"
    elif cli_ready:
        active_engine = "cli-fallback"
        worker_status = "standby_uninitialized"
    else:
        active_engine = "none"
        worker_status = "unavailable"

    # Truthful readiness: model file must exist, and at least one execution path must be operational
    system_ready = model_file_ok and (worker_healthy or _HAS_PIPER_PACKAGE or cli_ready)

    worker_py = resolve_piper_python_exe()
    worker_py_ok = Path(worker_py).is_file() if worker_py else False

    return {
        "ready": system_ready,
        "active_engine": active_engine,
        "primary_engine": "persistent-worker" if (worker_py_ok and model_file_ok) else ("cli-fallback" if cli_ready else "none"),
        "last_engine_used": _last_engine_used,
        "voice": VOICE,
        "model_path": str(_ONNX_PATH),
        "model_found": model_file_ok,
        "cli_available": cli_ready,
        "executable": PIPER,
        "executable_found": exe_ok,
        "direct_package_available": _HAS_PIPER_PACKAGE,
        "direct_model_loaded": _in_memory_voice is not None,
        "worker_status": worker_status,
        "worker_alive": worker_alive,
        "worker_healthy": worker_healthy,
        "worker_port": _worker_client._port,
        "worker_pid": worker_pid,
        "worker_failed": _worker_client._failed,
        "worker_failure_reason": _worker_client._failure_reason,
        "worker_python": worker_py,
        "worker_python_exists": worker_py_ok,
    }


def synthesise_to_wav(text: str) -> bytes:
    """
    Run Piper TTS on *text* and return the raw WAV bytes.

    Execution precedence:
    1. Direct in-memory engine (if piper-tts is importable in current interpreter).
    2. Persistent local Piper worker (runs under global Python, communicates over localhost HTTP).
    3. One-shot piper.exe subprocess (fallback if worker fails or is unavailable).

    Raises
    ------
    PiperUnavailableError
        If both worker and CLI fallback executable / model cannot be found.
    PiperSynthesisError
        If synthesis fails or exits with a non-zero return code / times out.
    ValueError
        If *text* is empty after sanitisation.
    """
    global _last_engine_used

    t_total_start = time.perf_counter()
    wait_duration = 0.0
    synth_duration = 0.0
    engine_used = "none"

    clean = _sanitise_text(text)
    if not clean:
        raise ValueError("Text is empty after sanitisation; nothing to synthesise.")

    # 1. Direct in-process engine (active if running under global Python)
    voice = _get_in_memory_voice()
    if voice is not None:
        t_wait_start = time.perf_counter()
        in_memory_failed = False
        try:
            with _tts_lock:
                wait_duration = time.perf_counter() - t_wait_start
                t_synth_start = time.perf_counter()
                wav_bytes = _synthesise_in_memory(voice, clean)
                synth_duration = time.perf_counter() - t_synth_start
                engine_used = "direct-in-memory"
                _last_engine_used = "direct-in-memory"
                return wav_bytes
        except Exception as exc:
            in_memory_failed = True
            print(f"[TTS Engine Diagnostic] Direct in-memory synthesis failed ({type(exc).__name__}); trying worker.")
        finally:
            if not in_memory_failed and engine_used == "direct-in-memory":
                total_duration = time.perf_counter() - t_total_start
                try:
                    print(
                        f"[TTS] total={total_duration * 1000:.1f}ms "
                        f"(engine=direct-in-memory, synth={synth_duration * 1000:.1f}ms, "
                        f"wait={wait_duration * 1000:.1f}ms, chars={len(clean)})"
                    )
                except Exception:
                    pass

    # 2. Persistent Local Piper Worker (localhost HTTP IPC)
    t_wait_start = time.perf_counter()
    with _tts_lock:
        wait_duration = time.perf_counter() - t_wait_start
        worker_result = _worker_client.synthesize(clean)
        if worker_result is not None:
            wav_bytes, rtt_ms, synth_ms = worker_result
            total_duration = time.perf_counter() - t_total_start
            engine_used = "persistent-worker"
            _last_engine_used = "persistent-worker"
            _last_fallback_reason = None
            print(
                f"[TTS Engine Diagnostic] Persistent worker synthesis succeeded: chars={len(clean)}, "
                f"synth={synth_ms:.1f}ms, rtt={rtt_ms:.1f}ms, wait={wait_duration * 1000:.1f}ms"
            )
            print(
                f"[TTS] total={total_duration * 1000:.1f}ms "
                f"(engine=persistent-worker, synth={synth_ms:.1f}ms, rtt={rtt_ms:.1f}ms, "
                f"wait={wait_duration * 1000:.1f}ms, chars={len(clean)})"
            )
            return wav_bytes

    # 3. Fallback: One-Shot CLI Subprocess
    fallback_reason = _worker_client._failure_reason or "worker unavailable"
    _last_fallback_reason = fallback_reason
    print(
        f"[TTS Engine Diagnostic] CLI fallback invoked: reason={fallback_reason}, chars={len(clean)}"
    )
    _check_piper_executable()
    _get_validated_model_arg()

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        t_wait_start = time.perf_counter()
        with _tts_lock:
            wait_duration = time.perf_counter() - t_wait_start
            t_proc_start = time.perf_counter()
            wav_bytes = _synthesise_subprocess(clean, tmp_path)
            synth_duration = time.perf_counter() - t_proc_start
            engine_used = "cli-fallback"
            _last_engine_used = "cli-fallback"

        return wav_bytes

    except subprocess.TimeoutExpired as exc:
        _last_engine_used = "failed"
        raise PiperSynthesisError("Piper TTS timed out after 60 seconds.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        _last_engine_used = "failed"
        raise PiperSynthesisError(f"Piper subprocess error: {exc}") from exc
    except Exception:
        _last_engine_used = "failed"
        raise
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

        total_duration = time.perf_counter() - t_total_start
        try:
            print(
                f"[TTS] total={total_duration * 1000:.1f}ms "
                f"(engine={engine_used}, proc={synth_duration * 1000:.1f}ms, "
                f"wait={wait_duration * 1000:.1f}ms, chars={len(clean)})"
            )
        except Exception:
            pass
