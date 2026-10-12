import asyncio
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi.responses import Response

# Ensure both backend directory and project root are in sys.path
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[1]

for path_entry in (str(BACKEND_DIR), str(PROJECT_ROOT)):
    if path_entry not in sys.path:
        sys.path.insert(0, path_entry)

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

try:
    from backend.agent import (
        OllamaConnectionError,
        OllamaHTTPError,
        OllamaTimeoutError,
        ask_agent,
        get_health,
    )
    from backend.tts import (
        PiperSynthesisError,
        PiperUnavailableError,
        synthesise_to_wav,
    )
except ImportError:
    from agent import (
        OllamaConnectionError,
        OllamaHTTPError,
        OllamaTimeoutError,
        ask_agent,
        get_health,
    )
    from tts import (
        PiperSynthesisError,
        PiperUnavailableError,
        synthesise_to_wav,
    )

app = FastAPI(
    title="Local Voice Agent API",
    description="JARVIS local assistant engine FastAPI backend",
    version="1.0.0",
)

# CORS configuration restricted strictly to local Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message text")

    @field_validator("message")
    @classmethod
    def validate_message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message cannot be empty or whitespace only.")
        return v.strip()


class ChatResponse(BaseModel):
    reply: str
    toolCall: Optional[Dict[str, Any]] = None


@app.get("/")
def root():
    return {
        "status": "running",
        "agent": "Local Voice Agent",
        "endpoints": {
            "health": "/health",
            "chat": "/chat",
            "speak": "/speak",
            "transcribe": "/transcribe",
        },
    }


@app.get("/health")
def health():
    return get_health()


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Defense-in-depth validation against empty / whitespace strings
    if not request.message or not request.message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty or whitespace only.",
        )

    try:
        result = ask_agent(request.message)
        return result
    except OllamaConnectionError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        ) from err
    except OllamaTimeoutError as err:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(err),
        ) from err
    except OllamaHTTPError as err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Local assistant processing error: {err}",
        ) from err


# ---------------------------------------------------------------------------
# TTS request / response models
# ---------------------------------------------------------------------------

TTS_MAX_TEXT_LENGTH = 2000  # characters — keeps Piper latency predictable


class SpeakRequest(BaseModel):
    text: str = Field(..., description="Text to synthesise with Piper TTS")

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text cannot be empty or whitespace only.")
        stripped = v.strip()
        if len(stripped) > TTS_MAX_TEXT_LENGTH:
            raise ValueError(
                f"text exceeds maximum length of {TTS_MAX_TEXT_LENGTH} characters."
            )
        return stripped


@app.post("/speak")
def speak(request: SpeakRequest):
    """
    Synthesise *text* with the local Piper TTS engine and return a WAV file.

    The returned audio is audio/wav and can be played directly by the browser
    via an HTMLAudioElement (URL.createObjectURL on a Blob).

    HTTP error codes
    ----------------
    400  text validation failure (empty or too long)
    422  Pydantic request validation failure
    503  Piper executable or voice model not found
    500  Piper synthesis runtime error
    """
    # Defence-in-depth: validate again after Pydantic
    if not request.text or not request.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text cannot be empty or whitespace only.",
        )

    try:
        wav_bytes = synthesise_to_wav(request.text)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except PiperUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        ) from err
    except PiperSynthesisError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected TTS error: {err}",
        ) from err

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            # Prevent the browser from caching audio blobs across responses.
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Transcription constants
# ---------------------------------------------------------------------------

# Maximum raw audio upload size in bytes (2 MB).
_TRANSCRIBE_MAX_BYTES = 2 * 1024 * 1024

# Maximum recording duration in seconds. Whisper.cpp can handle longer files,
# but this keeps latency predictable and matches the front-end recording cap.
_TRANSCRIBE_MAX_SECONDS = 15

# Expected WAV audio parameters from the browser encoder.
_WAV_EXPECTED_CHANNELS = 1      # mono
_WAV_EXPECTED_SAMPLE_RATE = 16_000
_WAV_EXPECTED_BITS = 16
_WAV_PCM_FORMAT_TAG = 1         # WAVE_FORMAT_PCM


def _validate_wav_header(data: bytes) -> None:
    """
    Parse and validate the RIFF/WAVE header of *data*.

    Raises HTTPException(422) if:
    - The RIFF/WAVE signature is missing.
    - The fmt chunk cannot be found or is malformed.
    - Audio is not PCM, not mono, not 16-bit, or not 16 000 Hz.

    Does not validate the body beyond the header, which is intentional —
    Whisper.cpp performs its own deep decode.
    """
    if len(data) < 44:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio file is too small to be a valid WAV.",
        )

    # RIFF header: bytes 0-3 = 'RIFF', bytes 8-11 = 'WAVE'
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is not a valid WAV (missing RIFF/WAVE signature).",
        )

    # Locate the 'fmt ' sub-chunk (may not start at byte 12 if extra chunks exist)
    pos = 12
    fmt_data: Optional[bytes] = None
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
        if chunk_id == b"fmt ":
            fmt_data = data[pos + 8 : pos + 8 + chunk_size]
            break
        pos += 8 + chunk_size

    if fmt_data is None or len(fmt_data) < 16:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="WAV file is missing or has a malformed fmt chunk.",
        )

    audio_format, channels, sample_rate, _, _, bits_per_sample = struct.unpack_from(
        "<HHIIHH", fmt_data
    )

    errors = []
    if audio_format != _WAV_PCM_FORMAT_TAG:
        errors.append(f"audio format must be PCM (1), got {audio_format}")
    if channels != _WAV_EXPECTED_CHANNELS:
        errors.append(f"audio must be mono (1 channel), got {channels}")
    if sample_rate != _WAV_EXPECTED_SAMPLE_RATE:
        errors.append(f"sample rate must be 16000 Hz, got {sample_rate}")
    if bits_per_sample != _WAV_EXPECTED_BITS:
        errors.append(f"bit depth must be 16-bit, got {bits_per_sample}")

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio format rejected: " + "; ".join(errors) + ".",
        )


def _duration_from_wav_header(data: bytes) -> float:
    """
    Return the audio duration in seconds derived from the WAV header's
    byte-rate field. Returns 0.0 if the header cannot be parsed (caller
    already validated the header so this should not happen).
    """
    try:
        pos = 12
        while pos + 8 <= len(data):
            chunk_id = data[pos : pos + 4]
            chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
            if chunk_id == b"fmt ":
                fmt = data[pos + 8 : pos + 8 + chunk_size]
                byte_rate = struct.unpack_from("<I", fmt, 8)[0]
                if byte_rate == 0:
                    return 0.0
                # locate 'data' chunk for its size
                dpos = pos + 8 + chunk_size
                while dpos + 8 <= len(data):
                    did = data[dpos : dpos + 4]
                    dsz = struct.unpack_from("<I", data, dpos + 4)[0]
                    if did == b"data":
                        return dsz / byte_rate
                    dpos += 8 + dsz
                return 0.0
            pos += 8 + chunk_size
    except Exception:  # noqa: BLE001
        pass
    return 0.0


@app.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """
    Accept a mono 16-bit 16 kHz PCM WAV file upload and transcribe it with
    the local Whisper.cpp executable.

    HTTP status codes
    -----------------
    200  Transcription succeeded: ``{"transcript": "..."}``
    413  Upload exceeds the 2 MB size limit
    422  WAV format rejected, recording too long, or empty transcription
    503  Whisper executable or model file not found
    500  Whisper subprocess error
    """
    # --- Lazy import to keep startup fast and follow the project pattern ---
    try:
        from voice_agent import (  # type: ignore[import]
            WHISPER,
            WHISPER_LANGUAGE,
            WHISPER_MODEL,
            get_whisper_model,
        )
    except ImportError:
        try:
            from backend.voice_agent import (  # type: ignore[import]
                WHISPER,
                WHISPER_LANGUAGE,
                WHISPER_MODEL,
                get_whisper_model,
            )
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Whisper speech-to-text engine is not configured.",
            ) from exc

    # --- 1. Verify Whisper availability before doing any I/O ---
    if not Path(WHISPER).is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Whisper speech-to-text engine is not available. Check that whisper-cli.exe is built.",
        )
    model_to_use = get_whisper_model() if callable(get_whisper_model) else WHISPER_MODEL
    if not Path(model_to_use).is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Whisper model file is not available. Check the configured model path.",
        )

    # --- 2. Read the upload asynchronously in chunks with a hard byte-count cap ---
    chunks: list[bytes] = []
    total_bytes = 0
    chunk_size = 64 * 1024  # 64 KB chunk size

    try:
        while True:
            chunk = await audio.read(chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > _TRANSCRIBE_MAX_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Audio upload exceeds the {_TRANSCRIBE_MAX_BYTES // (1024 * 1024)} MB limit.",
                )
            chunks.append(chunk)
    finally:
        await audio.close()

    audio_bytes = b"".join(chunks)

    # --- 3. Validate WAV header (signature + PCM/mono/16-bit/16kHz) ---
    _validate_wav_header(audio_bytes)

    # --- 4. Check duration ---
    duration = _duration_from_wav_header(audio_bytes)
    if duration > _TRANSCRIBE_MAX_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Recording duration ({duration:.1f}s) exceeds the {_TRANSCRIBE_MAX_SECONDS}s limit.",
        )

    # --- 5. Write to a unique temp file; close before the subprocess opens it ---
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = Path(tmp.name)
    try:
        tmp.write(audio_bytes)
        tmp.close()  # Must be closed on Windows before subprocess can read it

        # --- 6. Run Whisper in a thread pool so the event loop stays free ---
        def _run_whisper() -> subprocess.CompletedProcess:  # type: ignore[type-arg]
            cmd = [
                WHISPER,
                "-m", model_to_use,
                "-f", str(tmp_path),
                "-l", WHISPER_LANGUAGE,
                "-nt",  # no timestamps in output
            ]
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=60,
            )

        try:
            result = await asyncio.to_thread(_run_whisper)
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Speech recognition timed out. Try a shorter recording.",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Speech recognition engine encountered an error while processing audio.",
            ) from exc

        if result.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Speech recognition failed. Ensure the model file is compatible with this build of whisper-cli.",
            )

        # --- 7. Clean and validate the transcript ---
        transcript = result.stdout.strip()

        # Whisper emits these sentinel values for empty audio.
        if not transcript or transcript.upper() in {"[BLANK_AUDIO]", "[SILENCE]", "(blank)"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No speech detected in the recording. Please speak clearly and try again.",
            )

        return {"transcript": transcript}

    finally:
        # Always remove the temp file, even if an exception was raised above.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn
    # Localhost only: never bind to 0.0.0.0 by default
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)