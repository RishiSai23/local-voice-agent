import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import requests

# Ensure the project root is in sys.path so existing modules can be loaded
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory import (
    DB_PATH,
    forget_memory,
    get_memories,
    initialize_memory,
    save_memory,
)
from tools import (
    WORKSPACE,
    create_file,
    get_system_info,
    list_files,
    open_application,
    read_file,
)
from voice_agent import (
    MAX_HISTORY_MESSAGES,
    OLLAMA_MODEL,
    OLLAMA_URL,
    ask_qwen,
    handle_memory_command,
    handle_tool_command,
)


class OllamaConnectionError(Exception):
    """Raised when Ollama is offline or unreachable."""
    pass


class OllamaTimeoutError(Exception):
    """Raised when Ollama request times out."""
    pass


class OllamaHTTPError(Exception):
    """Raised when Ollama returns an HTTP error status."""
    pass


class AssistantEngine:
    """Thread-safe integration layer connecting FastAPI to existing local assistant logic."""

    def __init__(self):
        self._lock = threading.Lock()
        initialize_memory()
        self.history = [
            {
                "role": "system",
                "content": (
                    "You are a helpful local voice assistant. "
                    "Answer naturally and conversationally. "
                    "Usually use one to three short sentences. "
                    "Avoid long explanations unless asked. "
                    "Your answers will be spoken aloud. "
                    "You cannot execute tools by yourself; only the application's "
                    "explicit local command router can do that."
                ),
            }
        ]

    def reset_history(self):
        with self._lock:
            self.history = [self.history[0]]

    def _trim_history(self):
        if len(self.history) > MAX_HISTORY_MESSAGES + 1:
            self.history = [self.history[0]] + self.history[-MAX_HISTORY_MESSAGES:]

    def process_message(self, user_text: str) -> Dict[str, Any]:
        """Process a user message through existing tools, memory, or local Qwen via Ollama."""
        with self._lock:
            # 1. Exit/Status Command Handling (graceful standing-by response without breaking server)
            clean_text = re.sub(r"[^\w\s]", "", user_text.lower()).strip()
            if clean_text in {"exit", "quit", "stop", "stop assistant"}:
                return {
                    "reply": "Assistant session standing by. You can transmit another command anytime.",
                    "toolCall": None,
                }

            # 2. Deterministic local tools run before LLM
            tool_reply = handle_tool_command(user_text)
            if tool_reply is not None:
                tool_name = self._detect_tool_name(user_text)
                tool_status = self._detect_tool_status(tool_reply)
                tool_call = {
                    "name": tool_name,
                    "details": tool_reply,
                    "status": tool_status,
                }
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": tool_reply})
                self._trim_history()
                return {
                    "reply": tool_reply,
                    "toolCall": tool_call,
                }

            # 3. Deterministic memory router (SQLite)
            memory_reply = handle_memory_command(user_text)
            if memory_reply is not None:
                lower_mem = memory_reply.lower()
                is_failed = "couldn't find" in lower_mem or "don't have that fact" in lower_mem
                tool_call = {
                    "name": "memory.sqlite",
                    "details": memory_reply,
                    "status": "failed" if is_failed else "success",
                }
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": memory_reply})
                self._trim_history()
                return {
                    "reply": memory_reply,
                    "toolCall": tool_call,
                }

            # 4. LLM inference via existing ask_qwen
            try:
                memories = get_memories()
                reply = ask_qwen(user_text, self.history, memories)
            except requests.exceptions.ConnectionError as err:
                raise OllamaConnectionError(
                    f"Local AI engine (Ollama) is offline or unreachable at {OLLAMA_URL}. "
                    "Please ensure Ollama is running."
                ) from err
            except requests.exceptions.Timeout as err:
                raise OllamaTimeoutError(
                    "Local AI engine (Ollama) timed out while generating a response."
                ) from err
            except requests.exceptions.HTTPError as err:
                raise OllamaHTTPError(
                    f"Local AI engine (Ollama) returned HTTP error: {err}"
                ) from err
            except requests.exceptions.RequestException as err:
                raise OllamaConnectionError(
                    f"Network error communicating with local AI engine: {err}"
                ) from err

            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": reply})
            self._trim_history()
            return {
                "reply": reply,
                "toolCall": None,
            }

    @staticmethod
    def _detect_tool_name(user_text: str) -> str:
        clean = re.sub(
            r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis[,\s]+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?(?:please\s+)?",
            "",
            user_text.strip().strip("\"'").lower(),
        ).strip()
        if any(app in clean for app in ("notepad", "calculator", "calc", "paint")) and any(
            v in clean for v in ("open", "launch", "start", "run")
        ):
            return "tools.open_application"
        if any(k in clean for k in ("system", "computer", "hardware", "spec", "diagnostic", "specs")):
            return "tools.get_system_info"
        if re.search(r"\b(?:create|make|write|save)\b.*\bfile\b", clean):
            return "tools.create_file"
        if re.search(r"\b(?:read|display|view|print|contents)\b.*\b[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]+\b", clean):
            return "tools.read_file"
        if "file" in clean or "workspace" in clean:
            return "tools.list_files"
        return "tools.local_tool"

    @staticmethod
    def _detect_tool_status(tool_reply: str) -> str:
        failure_phrases = (
            "could not open",
            "not allowed",
            "couldn't",
            "could not",
            "file not found",
            "already exists",
            "access outside",
            "error:",
        )
        lower = tool_reply.strip().lower()
        if any(phrase in lower for phrase in failure_phrases):
            return "failed"
        return "success"

    @staticmethod
    def check_health() -> Dict[str, Any]:
        """Perform honest, local-only diagnostics of Ollama, SQLite memory, and workspace."""
        base_url = re.sub(r"/api/.*$", "", OLLAMA_URL)
        tags_url = f"{base_url}/api/tags"

        ollama_online = False
        model_available = False
        available_models = []
        ollama_error: Optional[str] = None

        try:
            # Query Ollama without launching any background process
            resp = requests.get(tags_url, timeout=2.5)
            if resp.status_code == 200:
                ollama_online = True
                data = resp.json()
                available_models = [m.get("name", "") for m in data.get("models", [])]
                model_available = any(
                    m == OLLAMA_MODEL or m.startswith(f"{OLLAMA_MODEL}:")
                    for m in available_models
                )
                if not model_available:
                    ollama_error = (
                        f"Configured model '{OLLAMA_MODEL}' was not found in Ollama. "
                        f"Available models: {available_models}"
                    )
            else:
                ollama_online = True
                ollama_error = f"Ollama returned HTTP status {resp.status_code}"
        except requests.exceptions.RequestException as err:
            ollama_online = False
            model_available = False
            ollama_error = f"Could not connect to Ollama at {base_url}: {err}"

        # SQLite memory check
        try:
            memories = get_memories()
            memory_status = "healthy"
            memory_count = len(memories)
        except Exception as err:
            memory_status = f"unhealthy: {err}"
            memory_count = 0

        # Assistant workspace check
        workspace_accessible = WORKSPACE.exists() and WORKSPACE.is_dir()
        workspace_status = "accessible" if workspace_accessible else "missing"

        # Determine overall system status
        if ollama_online and model_available and memory_status == "healthy":
            overall_status = "healthy"
        elif ollama_online and not model_available:
            overall_status = "degraded"
        else:
            overall_status = "offline" if not ollama_online else "degraded"

        return {
            "status": overall_status,
            "api": "healthy",
            "ollama": {
                "status": "online" if ollama_online else "offline",
                "endpoint": OLLAMA_URL,
                "configured_model": OLLAMA_MODEL,
                "model_available": model_available,
                "available_models": available_models,
                "error": ollama_error,
            },
            "memory": {
                "status": memory_status,
                "database": str(DB_PATH),
                "active_memories": memory_count,
            },
            "workspace": {
                "status": workspace_status,
                "path": str(WORKSPACE),
            },
        }


# Global engine instance for local assistant sessions
engine = AssistantEngine()


def ask_agent(message: str) -> Dict[str, Any]:
    return engine.process_message(message)


def get_health() -> Dict[str, Any]:
    return engine.check_health()