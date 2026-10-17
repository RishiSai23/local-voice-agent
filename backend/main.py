import asyncio
import logging
import struct
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import re

import requests
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
        check_piper_available,
        get_last_tts_engine,
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
        check_piper_available,
        get_last_tts_engine,
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

# Shared Whisper transcription helper (used by /transcribe and the voice loop).
try:
    from backend.voice_agent import (
        OLLAMA_KEEP_ALIVE,
        OLLAMA_MODEL,
        OLLAMA_URL,
        WHISPER_MODEL,
        WhisperTranscriptionError,
        WhisperUnavailableError,
        transcribe_wav_bytes,
    )
except ImportError:
    from voice_agent import (
        OLLAMA_KEEP_ALIVE,
        OLLAMA_MODEL,
        OLLAMA_URL,
        WHISPER_MODEL,
        WhisperTranscriptionError,
        WhisperUnavailableError,
        transcribe_wav_bytes,
    )

# Voice latency diagnostics (Stage 4C).
try:
    from backend.diagnostics import get_diagnostics_manager
except ImportError:
    from diagnostics import get_diagnostics_manager

# Hands-free voice-assistant orchestrator (Stage 3B).
try:
    from backend.voice_loop import get_voice_assistant
except ImportError:
    from voice_loop import get_voice_assistant

# Persistent conversation store (Stage 4B).
try:
    from backend.conversation_store import (
        init_conversation_db,
        create_conversation,
        get_active_conversation_id,
        peek_active_conversation_id,
        set_active_conversation_id,
        list_conversations,
        get_conversation,
        get_conversation_messages,
        rename_conversation,
        delete_conversation,
        add_message,
        add_interaction,
    )
except ImportError:
    from conversation_store import (
        init_conversation_db,
        create_conversation,
        get_active_conversation_id,
        peek_active_conversation_id,
        set_active_conversation_id,
        list_conversations,
        get_conversation,
        get_conversation_messages,
        rename_conversation,
        delete_conversation,
        add_message,
        add_interaction,
    )

logger = logging.getLogger("backend.main")

# Persistent task & reminder store (Stage 5)
try:
    from backend.task_store import (
        init_task_db,
        create_task,
        get_task,
        list_tasks,
        update_task,
        complete_task,
        cancel_task,
        delete_task,
        snooze_reminder,
        dismiss_reminder,
        list_upcoming_reminders,
        format_utc_to_local_str,
    )
    from backend.scheduler import get_task_scheduler
    from backend.notifier import (
        get_notification_settings,
        update_notification_settings,
        send_windows_notification_diagnostic,
    )
except ImportError:
    from task_store import (
        init_task_db,
        create_task,
        get_task,
        list_tasks,
        update_task,
        complete_task,
        cancel_task,
        delete_task,
        snooze_reminder,
        dismiss_reminder,
        list_upcoming_reminders,
        format_utc_to_local_str,
    )
    from scheduler import get_task_scheduler
    from notifier import (
        get_notification_settings,
        update_notification_settings,
        send_windows_notification_diagnostic,
    )

# Initialize conversation and task database tables on module load
init_conversation_db()
init_task_db()

app = FastAPI(
    title="Local Voice Agent API",
    description="JARVIS local assistant engine FastAPI backend",
    version="1.0.0",
)

@app.on_event("startup")
def startup_event():
    """Start background services including the task reminder scheduler."""
    logger.info("FastAPI backend startup: starting background task scheduler.")
    scheduler = get_task_scheduler()
    scheduler.start()

@app.on_event("shutdown")
def shutdown_event():
    """Cleanly terminate background services on server shutdown."""
    logger.info("FastAPI backend shutdown: stopping background task scheduler.")
    scheduler = get_task_scheduler()
    scheduler.stop()

# CORS configuration restricted strictly to local Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message text")
    conversation_id: Optional[str] = Field(None, description="Optional conversation ID to link interaction")

    @field_validator("message")
    @classmethod
    def validate_message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message cannot be empty or whitespace only.")
        return v.strip()


class ChatResponse(BaseModel):
    reply: str
    toolCall: Optional[Dict[str, Any]] = None
    conversation_id: Optional[str] = None


class CreateConversationRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=200, description="Optional conversation title")


class RenameConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="New title for the conversation")

    @field_validator("title")
    @classmethod
    def validate_title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Title cannot be empty or whitespace only.")
        return v.strip()


class SetActiveConversationRequest(BaseModel):
    conversation_id: str = Field(..., description="Conversation ID to set active")


class AppendInteractionRequest(BaseModel):
    user_message: str = Field(..., description="User question / message")
    assistant_reply: str = Field(..., description="Assistant response")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Optional interaction metadata")

    @field_validator("user_message", "assistant_reply")
    @classmethod
    def validate_text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Text cannot be empty or whitespace only.")
        return v.strip()


class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500, description="Title of the task")
    description: Optional[str] = Field(None, max_length=2000, description="Optional description / notes")
    due_at: Optional[str] = Field(None, description="Optional ISO-8601 or common datetime string for due date")
    reminder_at: Optional[str] = Field(None, description="Optional ISO-8601 or common datetime string for reminder")
    recurrence: Optional[str] = Field(None, description="Optional recurrence: 'daily', 'weekly', or 'weekday'")
    conversation_id: Optional[str] = Field(None, description="Optional conversation ID to link task")

    @field_validator("title")
    @classmethod
    def validate_title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Task title cannot be empty or whitespace only.")
        return v.strip()


class UpdateTaskRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, description="'pending', 'completed', or 'cancelled'")
    due_at: Optional[str] = None
    reminder_at: Optional[str] = None
    recurrence: Optional[str] = None


class SnoozeReminderRequest(BaseModel):
    minutes: int = Field(10, ge=1, le=1440, description="Minutes to snooze (1 to 1440)")


class NotificationSettingsRequest(BaseModel):
    windows_notifications_enabled: Optional[bool] = None
    spoken_reminders_enabled: Optional[bool] = None


class TestNotificationRequest(BaseModel):
    title: Optional[str] = Field("J.A.R.V.I.S. Notification Test", max_length=200)
    message: Optional[str] = Field("Windows desktop notifications are functioning correctly.", max_length=1000)
    test_speech: Optional[bool] = Field(False, description="Whether to also test spoken announcement")


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
            "wake_word_start": "/wake-word/start",
            "wake_word_stop": "/wake-word/stop",
            "wake_word_status": "/wake-word/status",
            "voice_assistant_start": "/voice-assistant/start",
            "voice_assistant_stop": "/voice-assistant/stop",
            "voice_assistant_cancel": "/voice-assistant/cancel",
            "voice_assistant_status": "/voice-assistant/status",
            "conversations": "/conversations",
            "active_conversation": "/conversations/active",
            "tasks": "/tasks",
            "upcoming_reminders": "/reminders/upcoming",
            "notification_settings": "/settings/notifications",
            "notification_test": "/notifications/test",
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
        conv_id = request.conversation_id or get_active_conversation_id()
        try:
            add_interaction(
                conv_id,
                request.message,
                result["reply"],
                metadata={"toolCall": result.get("toolCall"), "source": "chat_endpoint"},
            )
        except Exception as store_err:
            logger.warning("Failed to persist chat interaction: %s", store_err)

        return {
            "reply": result["reply"],
            "toolCall": result.get("toolCall"),
            "conversation_id": conv_id,
        }
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
            "X-TTS-Engine": get_last_tts_engine(),
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
    # --- 1. Read the upload asynchronously in chunks with a hard byte-count cap ---
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

    # --- 2. Validate WAV header (signature + PCM/mono/16-bit/16kHz) ---
    _validate_wav_header(audio_bytes)

    # --- 3. Check duration ---
    duration = _duration_from_wav_header(audio_bytes)
    if duration > _TRANSCRIBE_MAX_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Recording duration ({duration:.1f}s) exceeds the {_TRANSCRIBE_MAX_SECONDS}s limit.",
        )

    # --- 4. Run the shared Whisper helper in a thread pool so the event loop
    #        stays free. The helper owns temp-file handling and cleanup. ---
    try:
        transcript = await asyncio.to_thread(transcribe_wav_bytes, audio_bytes)
    except WhisperUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        ) from err
    except WhisperTranscriptionError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(err),
        ) from err
    except Exception as err:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Speech recognition failed unexpectedly: {err}",
        ) from err

    if not transcript:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No speech detected in the recording. Please speak clearly and try again.",
        )

    return {"transcript": transcript}


# ---------------------------------------------------------------------------
# Wake-word lifecycle (local, opt-in; Phase 8.3A)
# ---------------------------------------------------------------------------
# The backend owns the wake-listener lifecycle so JARVIS can run as a Windows
# background assistant without the React site open. The microphone is opened
# ONLY by an explicit POST /wake-word/start -- never on startup and never
# because a client merely connects. All endpoints stay localhost-only, matching
# the existing CORS/deployment posture (the app binds to 127.0.0.1).

# The wake phrase detected by the loaded hey_jarvis model.
WAKE_PHRASE = "Hey Jarvis"

logger = logging.getLogger(__name__)


class _WakeLifecycle:
    """Owns the wake-listener lifecycle and thread-safe wake-event state.

    A single shared instance (``_wake_lifecycle``) backs all routes. ``start``
    and ``stop`` are serialised by ``_lock`` so concurrent requests cannot race
    the listener lifecycle. The most-recent wake event is written by the
    listener's capture-worker thread (via the ``on_wake`` callback) and read by
    the status route, so it is guarded by ``_event_lock`` and holds only plain,
    copyable values.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._occurred = False
        self._timestamp: Optional[str] = None
        self._phrase: Optional[str] = None
        self._last_error: Optional[str] = None

    def _on_wake(self) -> None:
        """Callback run on the capture worker thread after a detection.

        Records the event only. It must NOT open a microphone or trigger
        transcription/LLM -- those belong to a later stage.
        """
        stamp = datetime.now(timezone.utc).isoformat()
        with self._event_lock:
            self._occurred = True
            self._timestamp = stamp
            self._phrase = WAKE_PHRASE
        logger.info("Wake phrase detected and recorded: %s", WAKE_PHRASE)

    def start(self) -> Dict[str, Any]:
        """Start the shared listener; idempotent. Raises HTTPException on error."""
        with self._lock:
            try:
                listener = get_wake_listener()
            except (WakeListenerSetupError, WakeListenerError) as err:
                self._last_error = str(err)
                logger.warning("Wake listener unavailable: %s", err)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=self._last_error,
                ) from err
            except Exception as err:
                self._last_error = "Unexpected error preparing the wake-word listener."
                logger.exception("Unexpected error preparing wake listener")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=self._last_error,
                ) from err

            if listener.is_running:
                return {"status": "already_running", "running": True}

            try:
                listener.start(on_wake=self._on_wake)
            except WakeListenerError as err:
                self._last_error = str(err)
                logger.warning("Failed to start wake listener: %s", err)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=self._last_error,
                ) from err
            except Exception as err:
                self._last_error = "Unexpected error starting the wake-word listener."
                logger.exception("Unexpected error starting wake listener")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=self._last_error,
                ) from err

            self._last_error = None
            logger.info("Wake-word listener started.")
            return {"status": "started", "running": True}

    def stop(self) -> Dict[str, Any]:
        """Stop the listener and release the microphone; safe to call repeatedly."""
        with self._lock:
            try:
                listener = get_wake_listener()
            except (WakeListenerSetupError, WakeListenerError):
                # Never constructed / unusable -> nothing is running.
                return {"status": "not_running", "running": False}
            except Exception:
                return {"status": "not_running", "running": False}

            try:
                listener.stop()
            except Exception as err:
                self._last_error = "Unexpected error stopping the wake-word listener."
                logger.exception("Unexpected error stopping wake listener")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=self._last_error,
                ) from err

            logger.info("Wake-word listener stopped.")
            return {"status": "stopped", "running": False}

    def status(self) -> Dict[str, Any]:
        """Report running state and the most recent wake event (traceback-free)."""
        running = False
        error: Optional[str] = None
        try:
            listener = get_wake_listener()
            running = listener.is_running
        except (WakeListenerSetupError, WakeListenerError) as err:
            error = str(err)
        except Exception:
            error = "Unexpected error accessing the wake-word listener."

        with self._event_lock:
            occurred = self._occurred
            timestamp = self._timestamp
            phrase = self._phrase

        if error is None:
            error = self._last_error

        last_event = (
            {"phrase": phrase, "timestamp": timestamp} if occurred else None
        )
        return {
            "running": running,
            "wake_occurred": occurred,
            "last_event": last_event,
            "error": error,
        }


# Shared lifecycle instance backing all wake-word routes.
_wake_lifecycle = _WakeLifecycle()


# ---------------------------------------------------------------------------
# Wake-word control endpoints (local-only)
# ---------------------------------------------------------------------------

@app.post("/wake-word/start")
def wake_word_start() -> Dict[str, Any]:
    """
    Start the shared wake-word listener and register the event-recording callback.

    Opens the microphone via the existing singleton listener. Does NOT start
    command recording or trigger an LLM response. Idempotent: calling again
    while already running returns status "already_running".

    Responses
    ---------
    200  {"status": "started" | "already_running", "running": true}
    409  a voice-assistant session is active and owns the microphone
    503  listener unavailable (missing model files or device error)
    500  unexpected failure (generic detail, no stack trace)
    """
    # Prevent conflicting microphone ownership with an active voice session.
    if get_voice_assistant().is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A voice-assistant session is active and owns the microphone. "
                "Stop it via POST /voice-assistant/stop first."
            ),
        )
    return _wake_lifecycle.start()


@app.post("/wake-word/stop")
def wake_word_stop() -> Dict[str, Any]:
    """
    Stop the wake-word listener and release the microphone. Safe to call
    repeatedly; returns "not_running" if it was never started.

    Responses
    ---------
    200  {"status": "stopped" | "not_running", "running": false}
    409  a voice-assistant session is active and owns the microphone
    500  unexpected failure while stopping (generic detail)
    """
    # Stopping the wake listener directly would fight an active voice session.
    if get_voice_assistant().is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A voice-assistant session is active and owns the microphone. "
                "Stop it via POST /voice-assistant/stop first."
            ),
        )
    return _wake_lifecycle.stop()


@app.get("/wake-word/status")
def wake_word_status() -> Dict[str, Any]:
    """
    Report listener state and the most recent wake event.

    Responses
    ---------
    200  {
            "running": bool,
            "wake_occurred": bool,
            "last_event": {"phrase": str, "timestamp": str} | null,
            "error": str | null
         }
    """
    return _wake_lifecycle.status()

# ---------------------------------------------------------------------------
# Voice-assistant control endpoints (local-only, hands-free loop)
# ---------------------------------------------------------------------------

@app.post("/voice-assistant/start")
def voice_assistant_start() -> Dict[str, Any]:
    """
    Start the full hands-free voice-assistant loop.

    Arms "Hey Jarvis" wake listening and, on each detection, plays an
    acknowledgement, records one command, transcribes it, processes it with the
    local agent, and speaks the reply -- then resumes wake listening. Does NOT
    auto-run on backend startup; the microphone opens only when this is called.
    Idempotent: calling again while running returns "already_running".

    Responses
    ---------
    200  {"status": "started" | "already_running", "state": "<state>"}
    503  wake listener unavailable (missing model files or device error)
    500  unexpected failure (generic detail, no stack trace)
    """
    try:
        result = get_voice_assistant().start()
    except (WakeListenerSetupError, WakeListenerError) as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        ) from err
    except Exception as err:  # noqa: BLE001
        logger.exception("Unexpected error starting voice assistant")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error starting the voice assistant.",
        ) from err
    return result


@app.post("/voice-assistant/stop")
def voice_assistant_stop() -> Dict[str, Any]:
    """
    Stop the full voice-assistant loop from any state.

    Interrupts any active recording or playback, releases the microphone, and
    stops the worker thread. Safe to call repeatedly; returns "not_running" if
    it was never started.

    Responses
    ---------
    200  {"status": "stopped" | "not_running", "state": "stopped"}
    500  unexpected failure while stopping (generic detail)
    """
    try:
        return get_voice_assistant().stop()
    except Exception as err:  # noqa: BLE001
        logger.exception("Unexpected error stopping voice assistant")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error stopping the voice assistant.",
        ) from err


@app.post("/voice-assistant/cancel")
def voice_assistant_cancel() -> Dict[str, Any]:
    """
    Cancel the active command capture and return to wake listening.

    Only acts while acknowledging or recording a command; otherwise reports
    "nothing_to_cancel". The session keeps running.

    Responses
    ---------
    200  {"status": "cancelling" | "nothing_to_cancel", "state": "<state>"}
    500  unexpected failure (generic detail)
    """
    try:
        return get_voice_assistant().cancel()
    except Exception as err:  # noqa: BLE001
        logger.exception("Unexpected error cancelling command capture")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error cancelling the command.",
        ) from err


@app.get("/voice-assistant/status")
def voice_assistant_status() -> Dict[str, Any]:
    """
    Report the current voice-assistant lifecycle state.

    Responses
    ---------
    200  {
            "state": "stopped" | "wake_listening" | "acknowledging"
                   | "recording_command" | "transcribing" | "thinking"
                   | "speaking" | "error",
            "running": bool,
            "error": str | null
         }
    """
    return get_voice_assistant().status()


@app.get("/voice-assistant/diagnostics")
def voice_assistant_diagnostics(limit: int = 20) -> Dict[str, Any]:
    """Retrieve structured latency diagnostics for previous voice interaction cycles (Stage 4C)."""
    manager = get_diagnostics_manager()
    return {
        "latest": manager.get_latest(),
        "history": manager.get_history(limit=limit),
    }


@app.get("/voice-assistant/model-status")
def voice_assistant_model_status() -> Dict[str, Any]:
    """Inspect whether Ollama, Whisper, and Piper models are currently loaded into memory."""
    # Check Ollama resident models via /api/ps (ground-truth residency detection)
    base_url = re.sub(r"/api/.*$", "", OLLAMA_URL)
    candidates = [f"{base_url}/api/ps"]
    if "localhost" in base_url:
        candidates.append(base_url.replace("localhost", "127.0.0.1") + "/api/ps")
    elif "127.0.0.1" in base_url:
        candidates.append(base_url.replace("127.0.0.1", "localhost") + "/api/ps")

    ollama_loaded = False
    ollama_details: Dict[str, Any] = {}
    last_error: Optional[str] = None
    all_loaded_models: list[str] = []

    for url in candidates:
        try:
            resp = requests.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                target = OLLAMA_MODEL.lower()
                target_base = target.split(":")[0]

                for m in models:
                    m_name = (m.get("name") or m.get("model") or "").lower()
                    if m_name:
                        all_loaded_models.append(m_name)
                    if (
                        target in m_name
                        or m_name in target
                        or (target_base and target_base in m_name)
                    ):
                        ollama_loaded = True
                        ollama_details = dict(m)
                        ollama_details["endpoint_queried"] = url
                        ollama_details["configured_keep_alive"] = OLLAMA_KEEP_ALIVE
                        break

                if not ollama_loaded:
                    ollama_details = {
                        "status": "not_resident",
                        "loaded_models": all_loaded_models,
                        "configured_model": OLLAMA_MODEL,
                        "configured_keep_alive": OLLAMA_KEEP_ALIVE,
                        "endpoint_queried": url,
                    }
                last_error = None
                break
            else:
                last_error = f"HTTP {resp.status_code} from {url}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"

    if last_error is not None and not ollama_loaded:
        ollama_details = {
            "status": "unreachable",
            "error": last_error,
            "configured_model": OLLAMA_MODEL,
            "configured_keep_alive": OLLAMA_KEEP_ALIVE,
        }

    # Piper status
    piper_status = check_piper_available()

    # Whisper status
    whisper_model_path = Path(WHISPER_MODEL)
    whisper_status = {
        "engine": "whisper-cli",
        "configured_model": WHISPER_MODEL,
        "model_exists": whisper_model_path.is_file(),
        "model_size_bytes": whisper_model_path.stat().st_size if whisper_model_path.is_file() else 0,
    }

    return {
        "ollama": {
            "model": OLLAMA_MODEL,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "resident_in_memory": ollama_loaded,
            "details": ollama_details,
        },
        "piper": piper_status,
        "whisper": whisper_status,
    }


@app.post("/voice-assistant/warmup")
def voice_assistant_warmup() -> Dict[str, Any]:
    """Controlled, non-blocking warm-up for Ollama and Piper worker, reporting initialization costs."""
    t0 = time.perf_counter()
    results: Dict[str, Any] = {}

    # 1. Warm up Piper worker
    t_piper = time.perf_counter()
    try:
        from backend.tts import _worker_client
        piper_ready = _worker_client.ensure_started()
        try:
            from backend.voice_loop import get_voice_assistant
            get_voice_assistant()._prewarm_ack()
        except Exception:
            pass
        results["piper"] = {
            "ready": piper_ready,
            "cost_ms": (time.perf_counter() - t_piper) * 1000.0,
        }
    except Exception as exc:  # noqa: BLE001
        results["piper"] = {"ready": False, "error": str(exc)}

    # 2. Warm up Ollama model
    t_ollama = time.perf_counter()
    base_url = re.sub(r"/api/.*$", "", OLLAMA_URL)
    generate_url = f"{base_url}/api/generate"
    try:
        resp = requests.post(
            generate_url,
            json={
                "model": OLLAMA_MODEL,
                "prompt": "",
                "keep_alive": OLLAMA_KEEP_ALIVE,
            },
            timeout=45.0,
        )
        data = resp.json() if resp.status_code == 200 else {}
        load_dur_ns = data.get("load_duration", 0)
        results["ollama"] = {
            "status": "loaded" if resp.status_code == 200 else "failed",
            "load_duration_ms": load_dur_ns / 1_000_000.0,
            "cost_ms": (time.perf_counter() - t_ollama) * 1000.0,
            "model": OLLAMA_MODEL,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }
    except Exception as exc:  # noqa: BLE001
        results["ollama"] = {"status": "error", "error": str(exc)}

    results["total_cost_ms"] = (time.perf_counter() - t0) * 1000.0
    return results


# ---------------------------------------------------------------------------
# Conversation CRUD & Message Endpoints (Stage 4B)
# ---------------------------------------------------------------------------

@app.get("/conversations")
def get_conversations() -> Dict[str, Any]:
    """List all stored conversations ordered by updated_at descending."""
    conversations = list_conversations()
    # Use peek (non-creating) so an empty database returns null rather than
    # triggering an implicit "New Chat" creation on first page load.
    active_id = peek_active_conversation_id()
    return {
        "conversations": conversations,
        "active_conversation_id": active_id,
    }


@app.post("/conversations", status_code=status.HTTP_201_CREATED)
def post_create_conversation(request: Optional[CreateConversationRequest] = None) -> Dict[str, Any]:
    """Create a new conversation, set it as active, and return its details."""
    title = request.title if request and request.title else "New Chat"
    conv = create_conversation(title=title)
    set_active_conversation_id(conv["id"])
    return {
        "conversation": conv,
        "active_conversation_id": conv["id"],
    }


@app.get("/conversations/active")
def get_active_conversation() -> Dict[str, Any]:
    """Return the currently active conversation and its details."""
    active_id = get_active_conversation_id()
    conv = get_conversation(active_id)
    return {
        "active_conversation_id": active_id,
        "conversation": conv,
    }


@app.post("/conversations/active")
def post_set_active_conversation(request: SetActiveConversationRequest) -> Dict[str, Any]:
    """Set the active conversation ID. Fails with 404 if conversation does not exist."""
    conv = get_conversation(request.conversation_id)
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{request.conversation_id}' not found.",
        )
    set_active_conversation_id(request.conversation_id)
    return {
        "active_conversation_id": request.conversation_id,
        "conversation": conv,
    }


@app.get("/conversations/{conversation_id}")
def get_single_conversation(conversation_id: str) -> Dict[str, Any]:
    """Retrieve metadata for a single conversation."""
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    return {"conversation": conv}


@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str) -> Dict[str, Any]:
    """Retrieve all ordered messages for a conversation."""
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    messages = get_conversation_messages(conversation_id)
    return {
        "conversation_id": conversation_id,
        "messages": messages,
    }


@app.patch("/conversations/{conversation_id}")
def patch_rename_conversation(conversation_id: str, request: RenameConversationRequest) -> Dict[str, Any]:
    """Rename a conversation title."""
    conv = rename_conversation(conversation_id, request.title)
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    return {"conversation": conv}


@app.delete("/conversations/{conversation_id}")
def delete_single_conversation(conversation_id: str) -> Dict[str, Any]:
    """Delete a conversation and all its messages."""
    success = delete_conversation(conversation_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    active_id = get_active_conversation_id()
    return {
        "status": "deleted",
        "conversation_id": conversation_id,
        "active_conversation_id": active_id,
    }


@app.post("/conversations/active/messages", status_code=status.HTTP_201_CREATED)
def post_append_active_message(request: AppendInteractionRequest) -> Dict[str, Any]:
    """Append a user message and assistant reply pair to the active conversation."""
    active_id = get_active_conversation_id()
    result = add_interaction(
        active_id,
        request.user_message,
        request.assistant_reply,
        metadata=request.metadata,
    )
    return {
        "conversation_id": result["conversation_id"],
        "messages": [result["user_message"], result["assistant_message"]],
    }


@app.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
def post_append_conversation_message(conversation_id: str, request: AppendInteractionRequest) -> Dict[str, Any]:
    """Append a user message and assistant reply pair to the specified conversation."""
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    result = add_interaction(
        conversation_id,
        request.user_message,
        request.assistant_reply,
        metadata=request.metadata,
    )
    return {
        "conversation_id": result["conversation_id"],
        "messages": [result["user_message"], result["assistant_message"]],
    }



# =============================================================================
# Stage 5 — Task & Reminder API Endpoints
# =============================================================================

@app.get("/tasks")
def api_list_tasks(
    status: Optional[str] = None,
    due_before: Optional[str] = None,
    due_after: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """List tasks with optional status and due date filters."""
    tasks = list_tasks(status=status, due_before=due_before, due_after=due_after, limit=limit)
    return {"tasks": tasks, "total": len(tasks)}


@app.post("/tasks", status_code=status.HTTP_201_CREATED)
def api_create_task(request: CreateTaskRequest) -> Dict[str, Any]:
    """Create a new task with optional reminder and recurrence."""
    try:
        task = create_task(
            title=request.title,
            description=request.description,
            due_at=request.due_at,
            reminder_at=request.reminder_at,
            recurrence=request.recurrence,
            conversation_id=request.conversation_id,
        )
        return {"task": task}
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@app.get("/tasks/{task_id}")
def api_get_task(task_id: str) -> Dict[str, Any]:
    """Retrieve a single task by ID."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return {"task": task}


@app.patch("/tasks/{task_id}")
def api_update_task(task_id: str, request: UpdateTaskRequest) -> Dict[str, Any]:
    """Update title, description, status, due date, reminder, or recurrence."""
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        task = get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task '{task_id}' not found.",
            )
        return {"task": task}

    try:
        updated = update_task(task_id, updates)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task '{task_id}' not found.",
            )
        return {"task": updated}
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@app.post("/tasks/{task_id}/complete")
def api_complete_task(task_id: str) -> Dict[str, Any]:
    """Mark a task as completed."""
    completed = complete_task(task_id)
    if not completed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return {"task": completed}


@app.post("/tasks/{task_id}/cancel")
def api_cancel_task(task_id: str) -> Dict[str, Any]:
    """Cancel a task."""
    cancelled = cancel_task(task_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return {"task": cancelled}


@app.delete("/tasks/{task_id}")
def api_delete_task(task_id: str) -> Dict[str, Any]:
    """Delete a task and its associated reminder logs permanently."""
    success = delete_task(task_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return {"status": "deleted", "task_id": task_id}


@app.post("/tasks/{task_id}/snooze")
def api_snooze_reminder(task_id: str, request: SnoozeReminderRequest) -> Dict[str, Any]:
    """Snooze a pending reminder by N minutes."""
    try:
        snoozed = snooze_reminder(task_id, minutes=request.minutes)
        if not snoozed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task '{task_id}' not found.",
            )
        return {"task": snoozed, "snoozed_minutes": request.minutes}
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@app.post("/tasks/{task_id}/dismiss")
def api_dismiss_reminder(task_id: str) -> Dict[str, Any]:
    """Dismiss a reminder (advances recurrence or clears reminder_at)."""
    dismissed = dismiss_reminder(task_id)
    if not dismissed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return {"task": dismissed}


@app.get("/reminders/upcoming")
def api_upcoming_reminders(limit: int = 20, window_hours: int = 72) -> Dict[str, Any]:
    """List pending reminders scheduled within the given window."""
    reminders = list_upcoming_reminders(limit=limit, window_hours=window_hours)
    return {"reminders": reminders, "total": len(reminders)}


@app.get("/settings/notifications")
def api_get_notifications_settings() -> Dict[str, Any]:
    """Get Windows notification and spoken reminder preferences."""
    return {"settings": get_notification_settings()}


@app.patch("/settings/notifications")
def api_patch_notifications_settings(request: NotificationSettingsRequest) -> Dict[str, Any]:
    """Update Windows notification or spoken reminder preferences."""
    updates = request.model_dump(exclude_unset=True)
    updated = update_notification_settings(updates)
    return {"settings": updated}


@app.post("/notifications/test")
def api_test_notification(request: Optional[TestNotificationRequest] = None) -> Dict[str, Any]:
    """Test Windows desktop notification delivery and return comprehensive diagnostics."""
    req_title = request.title if request and request.title else "J.A.R.V.I.S. Notification Test"
    req_msg = request.message if request and request.message else "Windows desktop notifications are functioning correctly."
    test_speech = bool(request.test_speech) if request and request.test_speech is not None else False

    diag = send_windows_notification_diagnostic(req_title, req_msg)
    spoken_result = None
    if test_speech:
        try:
            from backend.notifier import _safe_speak_reminder
            spoken_result = _safe_speak_reminder(f"Testing notification: {req_title}")
        except Exception as exc:
            spoken_result = f"Error: {exc}"

    return {
        "status": "ok" if diag.get("success") else "failed",
        "notification": diag,
        "spoken_tested": test_speech,
        "spoken_result": spoken_result,
        "current_settings": get_notification_settings(),
    }


@app.post("/scheduler/trigger")
def api_trigger_scheduler() -> Dict[str, Any]:
    """Manually invoke a scheduler check right now for manual testing / immediate dispatch."""
    scheduler = get_task_scheduler()
    claimed_count = scheduler.trigger_check()
    return {"status": "triggered", "claimed_count": claimed_count, "scheduler_running": scheduler.is_running}


if __name__ == "__main__":
    import uvicorn
    # Localhost only: never bind to 0.0.0.0 by default
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)