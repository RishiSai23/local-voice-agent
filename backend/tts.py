"""
backend/tts.py
--------------
Local Piper TTS helper used by the /speak FastAPI endpoint.

Key design decisions
- Reuses the PIPER executable path and VOICE model name from voice_agent.py
  so there is a single source of truth for those constants.
- Text is sanitised exactly as the existing speak() function does it
  (curly-quotes -> straight, Unicode category filtering) so output is
  consistent regardless of which caller invokes Piper.
- A fresh NamedTemporaryFile is used for every request so concurrent
  requests never collide on a single response.wav.  The file is always
  cleaned up whether synthesis succeeds or fails.
- shell=True is never used.  The executable and each argument are passed
  as separate list items, preventing command injection.
- Piper is given a 60-second timeout, matching voice_agent.py.
"""

import subprocess
import tempfile
import unicodedata
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from voice_agent import PIPER, VOICE  # noqa: E402

try:
    from backend.sanitizer import clean_markdown
except ImportError:
    from sanitizer import clean_markdown

# Absolute path to the .onnx model file sitting at the project root.
# Piper accepts either a bare voice name (if models are installed globally)
# or an explicit path to a .onnx file. We prefer the explicit path so the
# endpoint works even if Piper's model search path is not configured.
_ONNX_PATH = _PROJECT_ROOT / f"{VOICE}.onnx"


class PiperUnavailableError(RuntimeError):
    """Raised when the Piper executable or voice model cannot be found."""


class PiperSynthesisError(RuntimeError):
    """Raised when Piper exits with a non-zero return code."""


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
    """Return the best --model argument for the Piper CLI.

    Prefer the absolute .onnx path (works without global model installation).
    Fall back to the bare voice name so existing installations keep working.
    """
    if _ONNX_PATH.is_file():
        return str(_ONNX_PATH)
    return VOICE


def check_piper_available() -> dict:
    """Return a dict describing Piper availability (used by /health-like checks)."""
    exe_ok = Path(PIPER).is_file()
    model_arg = _resolve_model_arg()
    model_ok = Path(model_arg).is_file() if model_arg != VOICE else False
    return {
        "executable": PIPER,
        "executable_found": exe_ok,
        "voice": VOICE,
        "model_path": str(_ONNX_PATH),
        "model_found": _ONNX_PATH.is_file(),
        "ready": exe_ok and (model_ok or model_arg == VOICE),
    }


def synthesise_to_wav(text: str) -> bytes:
    """
    Run Piper TTS on *text* and return the raw WAV bytes.

    Raises
    ------
    PiperUnavailableError
        If the Piper executable or voice model file cannot be found.
    PiperSynthesisError
        If Piper exits with a non-zero return code or times out.
    ValueError
        If *text* is empty after sanitisation.
    """
    if not Path(PIPER).is_file():
        raise PiperUnavailableError(
            f"Piper executable not found at '{PIPER}'. "
            "Ensure Piper is installed at the configured path."
        )

    model_arg = _resolve_model_arg()
    if model_arg != VOICE and not Path(model_arg).is_file():
        raise PiperUnavailableError(
            f"Piper voice model not found at '{model_arg}'. "
            "Ensure the .onnx voice model is present in the project root."
        )

    clean = _sanitise_text(text)
    if not clean:
        raise ValueError("Text is empty after sanitisation; nothing to synthesise.")

    # Use a NamedTemporaryFile so concurrent requests never share a file.
    # delete=False: we manage deletion ourselves so we can read after Piper writes.
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
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

        wav_bytes = tmp_path.read_bytes()
        return wav_bytes

    except subprocess.TimeoutExpired as exc:
        raise PiperSynthesisError("Piper TTS timed out after 60 seconds.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise PiperSynthesisError(f"Piper subprocess error: {exc}") from exc
    finally:
        # Always clean up, even on failure.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
