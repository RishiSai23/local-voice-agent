import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure both backend directory and project root are in sys.path
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[1]

for path_entry in (str(BACKEND_DIR), str(PROJECT_ROOT)):
    if path_entry not in sys.path:
        sys.path.insert(0, path_entry)

from fastapi import FastAPI, HTTPException, status
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
except ImportError:
    from agent import (
        OllamaConnectionError,
        OllamaHTTPError,
        OllamaTimeoutError,
        ask_agent,
        get_health,
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


if __name__ == "__main__":
    import uvicorn
    # Localhost only: never bind to 0.0.0.0 by default
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)