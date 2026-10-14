import atexit
import collections
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


def _synthesise_in_memory(voice: Any, text: str) -> bytes:
    """Synthesise text to WAV bytes directly in-memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        if _syn_config is not None:
            try:
                voice.synthesize_wav(text, wav, syn_config=_syn_config)
            except TypeError:
                voice.synthesize_wav(text, wav)
        else:
            voice.synthesize_wav(text, wav)

    wav_bytes = buf.getvalue()
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
        self._python_exe: str = os.environ.get(
            "PIPER_WORKER_PYTHON",
            os.environ.get("PIPER_PYTHON_EXE", _DEFAULT_GLOBAL_PYTHON),
        )
        self._ready_event = threading.Event()
        self._drain_thread: Optional[threading.Thread] = None
        self._log_history: collections.deque = collections.deque(maxlen=100)

    def is_alive(self) -> bool:
        """Check if worker child process is currently running."""
        return self._process is not None and self._process.poll() is None

    def is_healthy(self) -> bool:
        """Check if worker child process is alive AND responding to authenticated HTTP /health."""
        if not self.is_alive() or self._port is None:
            return False
        try:
            url = f"http://{self._host}:{self._port}/health"
            req = urllib.request.Request(
                url,
                headers={"X-TTS-Token": self._token},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("status") == "ready"
        except Exception:
            pass
        return False

    def ensure_started(self) -> bool:
        """Ensure the persistent worker process is running, ready, and responsive.

        Returns True if the worker is operational, False otherwise.
        """
        if self.is_alive() and self._port is not None:
            return True

        # Circuit-breaker: wait at least 60 seconds before retrying a failed worker launch
        if self._failed and (time.time() - self._last_failure_time < 60.0):
            return False

        with self._lock:
            if self.is_alive() and self._port is not None:
                return True

            # Clean up any dead previous process before launching a new one
            if self._process is not None and self._process.poll() is not None:
                self._shutdown_internal()

            t_start = time.perf_counter()
            python_path = Path(self._python_exe)
            if not python_path.is_file():
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = f"Global Python executable not found at '{self._python_exe}'"
                print(f"[TTS Manager] {self._failure_reason}")
                return False

            worker_script = Path(__file__).resolve().parent / "tts_worker.py"
            if not worker_script.is_file():
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = f"Worker script not found at '{worker_script}'"
                print(f"[TTS Manager] {self._failure_reason}")
                return False

            if not _ONNX_PATH.is_file():
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = f"Voice model not found at '{_ONNX_PATH}'"
                print(f"[TTS Manager] {self._failure_reason}")
                return False

            if not self._token:
                self._token = secrets.token_hex(16)

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
                print(f"[TTS Manager] {self._failure_reason}")
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

            # Bounded readiness wait
            ready = self._ready_event.wait(timeout=self._startup_timeout)
            if not ready or self._port is None:
                exit_code = self._process.poll() if self._process else None
                recent_logs = " | ".join(list(self._log_history)[-5:]) if self._log_history else "none"
                self._shutdown_internal()
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = (
                    f"Worker startup timed out after {self._startup_timeout}s "
                    f"(exit_code={exit_code}, logs: {recent_logs})"
                )
                print(f"[TTS Manager] {self._failure_reason}")
                return False

            # Active health check confirmation
            if not self.is_healthy():
                recent_logs = " | ".join(list(self._log_history)[-5:]) if self._log_history else "none"
                self._shutdown_internal()
                self._failed = True
                self._last_failure_time = time.time()
                self._failure_reason = (
                    f"Worker process started on port {self._port} but failed /health check (logs: {recent_logs})"
                )
                print(f"[TTS Manager] {self._failure_reason}")
                return False

            startup_ms = (time.perf_counter() - t_start) * 1000
            print(
                f"[TTS Manager] Worker process started and verified in {startup_ms:.1f}ms "
                f"(pid={self._process.pid}, port={self._port})"
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
                    print(f"[TTS Manager] Worker returned HTTP {resp.status}")
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
                    print(f"[TTS Manager] Worker returned invalid WAV data ({len(wav_bytes)} bytes)")
                    # If the process has already exited, arm the circuit-breaker so the
                    # next request does not re-use a dead worker as though it were healthy.
                    if self._process is not None and self._process.poll() is not None:
                        recent_logs = " | ".join(list(self._log_history)[-3:]) if self._log_history else "none"
                        self._failure_reason = f"Worker terminated after delivering invalid WAV (logs: {recent_logs})"
                        self._failed = True
                        self._last_failure_time = time.time()
                        self._shutdown_internal()
                    return None

                return wav_bytes, rtt_ms, synth_ms

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[TTS Manager] Worker request failed: {type(exc).__name__}")
            # Determine worker viability; avoid repeated timeouts against a hung process
            proc_alive = self._process is not None and self._process.poll() is None
            if not proc_alive:
                recent_logs = " | ".join(list(self._log_history)[-3:]) if self._log_history else "none"
                self._failure_reason = f"Worker process terminated unexpectedly (logs: {recent_logs})"
                self._failed = True
                self._last_failure_time = time.time()
                self._shutdown_internal()
            elif not self.is_healthy():
                # Process alive but not responding to /health — treat as hung
                recent_logs = " | ".join(list(self._log_history)[-3:]) if self._log_history else "none"
                self._failure_reason = f"Worker unresponsive after failed synthesis (logs: {recent_logs})"
                self._failed = True
                self._last_failure_time = time.time()
                self._shutdown_internal()
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
    worker_healthy = _worker_client.is_healthy()

    # Truthful readiness: model file must exist, and at least one execution path must be operational
    system_ready = model_file_ok and (worker_healthy or _HAS_PIPER_PACKAGE or cli_ready)

    return {
        "ready": system_ready,
        "voice": VOICE,
        "model_path": str(_ONNX_PATH),
        "model_found": model_file_ok,
        "cli_available": cli_ready,
        "executable": PIPER,
        "executable_found": exe_ok,
        "direct_package_available": _HAS_PIPER_PACKAGE,
        "direct_model_loaded": _in_memory_voice is not None,
        "worker_alive": worker_alive,
        "worker_healthy": worker_healthy,
        "worker_port": _worker_client._port,
        "worker_failed": _worker_client._failed,
        "worker_failure_reason": _worker_client._failure_reason,
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
                return wav_bytes
        except Exception as exc:
            in_memory_failed = True
            print(f"[TTS Engine] Direct in-memory synthesis failed ({exc}); trying worker.")
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
            print(
                f"[TTS] total={total_duration * 1000:.1f}ms "
                f"(engine=persistent-worker, synth={synth_ms:.1f}ms, rtt={rtt_ms:.1f}ms, "
                f"wait={wait_duration * 1000:.1f}ms, chars={len(clean)})"
            )
            return wav_bytes

    # 3. Fallback: One-Shot CLI Subprocess
    fallback_reason = _worker_client._failure_reason or "worker unavailable"
    print(f"[TTS Engine] Falling back to CLI subprocess (reason: {fallback_reason}).")
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

        return wav_bytes

    except subprocess.TimeoutExpired as exc:
        raise PiperSynthesisError("Piper TTS timed out after 60 seconds.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise PiperSynthesisError(f"Piper subprocess error: {exc}") from exc
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
