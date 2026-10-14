"""
backend/tts_worker.py
---------------------
Persistent standalone Piper TTS worker process.

Designed to run under the global Python environment (where piper-tts, onnxruntime,
and piper-phonemize are installed) independently of the backend virtual environment.

Key architecture points:
- Binds strictly to 127.0.0.1 (never 0.0.0.0).
- Loads the ONNX voice model once into memory on startup.
- Synthesizes subsequent utterances entirely in RAM via io.BytesIO() and
  PiperVoice.synthesize_wav().
- Requires a per-process shared authentication token via the X-TTS-Token header.
- Serializes synthesis requests with a threading lock.
- Emits diagnostic logs with timing and character counts without logging user text.
- Provides a clean HTTP interface:
    POST /synthesize  -> Synthesise text into raw WAV bytes
    GET  /health      -> Check worker readiness and model status
    POST /shutdown    -> Gracefully terminate the worker server
"""

import argparse
import io
import json
import os
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

try:
    from piper.voice import PiperVoice
    from piper.config import SynthesisConfig
    _HAS_PIPER = True
except ImportError:
    try:
        from piper.voice import PiperVoice
        SynthesisConfig = None
        _HAS_PIPER = True
    except ImportError:
        PiperVoice = None
        SynthesisConfig = None
        _HAS_PIPER = False

# Global worker state
_voice: Optional[Any] = None
_syn_config: Optional[Any] = None
_silence_applied: bool = False
_auth_token: str = ""
_model_path_str: str = ""
_synthesis_lock = threading.Lock()
_server_instance: Optional[HTTPServer] = None

_MAX_BODY_BYTES: int = 32 * 1024   # 32 KB — generous upper bound for TTS input
_MAX_TEXT_CHARS: int = 2000         # Maximum characters per synthesis request


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


class PiperWorkerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the localhost-only Piper worker."""

    server_version = "PiperTTSWorker/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default Apache-style access logging to keep stdout clean for IPC."""
        # Diagnostic messages are emitted explicitly by route handlers.
        pass

    def _check_auth(self) -> bool:
        """Validate the X-TTS-Token authentication header."""
        client_token = self.headers.get("X-TTS-Token", "")
        if not _auth_token or client_token != _auth_token:
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Forbidden: invalid authentication token"}')
            return False
        return True

    def do_GET(self) -> None:
        """Handle health check endpoint."""
        if self.path == "/health":
            if not self._check_auth():
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "status": "ready" if _voice is not None else "uninitialized",
                "model": _model_path_str,
                "loaded": _voice is not None,
                "silence_applied": _silence_applied,
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "Not found"}')

    def do_POST(self) -> None:
        """Handle synthesis and shutdown endpoints."""
        if self.path == "/shutdown":
            if not self._check_auth():
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "shutting_down"}')

            # Schedule server shutdown asynchronously so the response can finish
            def _shutdown() -> None:
                time.sleep(0.1)
                if _server_instance is not None:
                    _server_instance.shutdown()

            threading.Thread(target=_shutdown, daemon=True).start()
            return

        if self.path == "/synthesize":
            if not self._check_auth():
                return

            try:
                content_length = int(self.headers.get("Content-Length", 0))
            except (ValueError, TypeError):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Invalid Content-Length header"}')
                return

            if content_length <= 0:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Empty request body"}')
                return

            if content_length > _MAX_BODY_BYTES:
                self.send_response(413)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": f"Request body exceeds {_MAX_BODY_BYTES} bytes"}).encode("utf-8")
                )
                return

            try:
                body = self.rfile.read(content_length).decode("utf-8")
            except (UnicodeDecodeError, OSError):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Failed to read or decode request body"}')
                return

            try:
                data = json.loads(body)
                text = data.get("text", "")
            except Exception:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Invalid JSON payload"}')
                return

            if not isinstance(text, str):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Field \\"text\\" must be a string"}')
                return

            if not text or not text.strip():
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Text cannot be empty"}')
                return

            if len(text) > _MAX_TEXT_CHARS:
                self.send_response(413)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": f"Text exceeds maximum of {_MAX_TEXT_CHARS} characters"}).encode("utf-8")
                )
                return

            if _voice is None:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Voice model not loaded"}')
                return

            # Synthesize directly in-memory under synthesis lock
            try:
                t_synth_start = time.perf_counter()
                with _synthesis_lock:
                    buf = io.BytesIO()
                    with wave.open(buf, "wb") as wav:
                        if _syn_config is not None:
                            try:
                                _voice.synthesize_wav(text, wav, syn_config=_syn_config)
                            except TypeError:
                                print(
                                    "[TTS Worker] synthesize_wav does not accept syn_config; "
                                    "retrying without silence configuration.",
                                    flush=True,
                                )
                                _voice.synthesize_wav(text, wav)
                        else:
                            _voice.synthesize_wav(text, wav)

                    wav_bytes = buf.getvalue()
                    synth_duration = time.perf_counter() - t_synth_start

                # Strict audio validation
                if len(wav_bytes) <= 44 or not (wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE"):
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "Generated audio has invalid WAV format"}')
                    return

                # Success: return binary audio/wav
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav_bytes)))
                self.send_header("X-Synthesis-Ms", f"{synth_duration * 1000:.1f}")
                self.end_headers()
                self.wfile.write(wav_bytes)

                # Diagnostic logging without logging user text
                try:
                    print(
                        f"[TTS Worker] Synthesized {len(text)} chars in {synth_duration * 1000:.1f}ms "
                        f"({len(wav_bytes)} bytes)",
                        flush=True,
                    )
                except Exception:
                    pass

            except Exception:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Synthesis failed"}')
            return

        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "Not found"}')


def run_worker() -> None:
    """Parse command line flags, load voice model, and start localhost HTTP server."""
    global _voice, _syn_config, _auth_token, _model_path_str, _server_instance

    parser = argparse.ArgumentParser(description="Persistent Piper TTS Worker")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (must be 127.0.0.1)")
    parser.add_argument("--port", type=int, default=50055, help="Port to bind (0 for ephemeral)")
    parser.add_argument("--token", required=True, help="Authentication token for IPC")
    parser.add_argument("--model", required=True, help="Path to .onnx voice model")
    parser.add_argument("--silence", type=float, default=0.35, help="Sentence silence in seconds")
    args = parser.parse_args()

    # Enforce strict localhost binding
    if args.host not in ("127.0.0.1", "localhost"):
        sys.stderr.write("FATAL: Piper TTS Worker can only bind to 127.0.0.1.\n")
        sys.exit(1)

    if not _HAS_PIPER:
        sys.stderr.write("FATAL: piper-tts package is not available in this Python environment.\n")
        sys.exit(1)

    model_path = Path(args.model).resolve()
    if not model_path.is_file():
        sys.stderr.write(f"FATAL: Voice model file not found at: {model_path}\n")
        sys.exit(1)

    _auth_token = args.token
    _model_path_str = str(model_path)

    # 1. Load Piper voice model once into memory
    try:
        t_load_start = time.perf_counter()
        _voice = PiperVoice.load(_model_path_str)
        load_duration = time.perf_counter() - t_load_start
        _syn_config, _silence_applied = _create_synthesis_config(args.silence)
        silence_status = f"silence={args.silence}s" if _silence_applied else "silence=unsupported_omitted"
        print(
            f"[TTS Worker] Piper voice model loaded in {load_duration * 1000:.1f}ms "
            f"({model_path.name}, {silence_status})",
            flush=True,
        )
    except Exception as exc:
        sys.stderr.write(f"FATAL: Failed to load Piper model: {exc}\n")
        sys.exit(1)

    # 2. Start HTTP Server
    try:
        try:
            _server_instance = HTTPServer((args.host, args.port), PiperWorkerHandler)
        except OSError as err:
            if args.port != 0:
                sys.stderr.write(f"[TTS Worker] Port {args.port} unavailable ({err}), falling back to port 0\n")
                _server_instance = HTTPServer((args.host, 0), PiperWorkerHandler)
            else:
                raise

        bound_host, bound_port = _server_instance.server_address

        # Handshake line emitted to stdout for parent process
        print(f"[TTS_WORKER_READY] host={bound_host} port={bound_port}", flush=True)

        _server_instance.serve_forever()
    except Exception as exc:
        sys.stderr.write(f"FATAL: Server failed on {args.host}:{args.port}: {exc}\n")
        sys.exit(1)
    finally:
        if _server_instance is not None:
            _server_instance.server_close()
        print("[TTS Worker] Server terminated cleanly.", flush=True)


if __name__ == "__main__":
    run_worker()
