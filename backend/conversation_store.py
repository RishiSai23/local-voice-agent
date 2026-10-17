"""
backend/conversation_store.py
-----------------------------
Persistent SQLite-backed conversation and message storage for J.A.R.V.I.S.

Provides thread-safe storage for:
- Conversations: id, title, created_at, updated_at
- Messages: id, conversation_id, role, content, timestamp, metadata (JSON)

Maintains an active conversation pointer for seamless voice and UI synchronization.
Reuses the local project SQLite database (memory.db).
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Resolve path to memory.db in project root
try:
    from memory import DB_PATH
except ImportError:
    DB_PATH = Path(__file__).resolve().parents[1] / "memory.db"

# Thread-safe lock for database access and active conversation tracking
_db_lock = threading.Lock()
_active_conversation_id: Optional[str] = None


def _get_connection() -> sqlite3.Connection:
    """Return an optimized, thread-safe connection to the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging (WAL) for concurrent reads/writes and enforce foreign keys
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_conversation_db() -> None:
    """Initialize conversation and message tables and indexes safely."""
    with _db_lock, _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
            ON messages(conversation_id);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
            ON conversations(updated_at DESC);
        """)
    logger.info("Conversation database initialized at %s.", DB_PATH)


def generate_title(first_message: str) -> str:
    """Generate a concise, sensible conversation title from the first message."""
    cleaned = " ".join(first_message.strip().split())
    if not cleaned:
        return "New Chat"
    if len(cleaned) <= 36:
        return cleaned
    truncated = cleaned[:36]
    last_space = truncated.rfind(" ")
    if last_space > 18:
        truncated = truncated[:last_space]
    return truncated.rstrip(",.!?;:") + "..."


def get_active_conversation_id() -> str:
    """Return the active conversation ID, auto-creating or selecting the latest if needed."""
    global _active_conversation_id
    with _db_lock:
        if _active_conversation_id:
            # Verify it still exists in the database
            with _get_connection() as conn:
                row = conn.execute(
                    "SELECT id FROM conversations WHERE id = ?",
                    (_active_conversation_id,),
                ).fetchone()
                if row:
                    return _active_conversation_id

        # Check for the most recently updated conversation
        with _get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if row:
                _active_conversation_id = row["id"]
                return _active_conversation_id

    # If no conversations exist, create a new one outside the lock to avoid re-entrancy
    new_conv = create_conversation(title="New Chat")
    return new_conv["id"]


def peek_active_conversation_id() -> Optional[str]:
    """Return the active conversation ID without auto-creating one.

    Returns None if no conversations exist. Use this from read-only list endpoints
    so the UI receives a clean null rather than triggering an implicit new chat.
    """
    global _active_conversation_id
    with _db_lock:
        if _active_conversation_id:
            with _get_connection() as conn:
                row = conn.execute(
                    "SELECT id FROM conversations WHERE id = ?",
                    (_active_conversation_id,),
                ).fetchone()
                if row:
                    return _active_conversation_id
                _active_conversation_id = None

        with _get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if row:
                _active_conversation_id = row["id"]
                return _active_conversation_id

    return None


def set_active_conversation_id(conversation_id: str) -> bool:
    """Set the active conversation ID. Returns False if conversation does not exist."""
    global _active_conversation_id
    with _db_lock:
        with _get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                return False
        _active_conversation_id = conversation_id
        return True



def create_conversation(title: Optional[str] = None) -> Dict[str, Any]:
    """Create a new conversation, set it as active, and return its summary."""
    global _active_conversation_id
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    final_title = title.strip() if title and title.strip() else "New Chat"

    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (conv_id, final_title, now_iso, now_iso),
        )
        _active_conversation_id = conv_id

    logger.info("Created conversation %s with title '%s'.", conv_id, final_title)
    return {
        "id": conv_id,
        "title": final_title,
        "created_at": now_iso,
        "updated_at": now_iso,
        "message_count": 0,
    }


def list_conversations(search: Optional[str] = None) -> List[Dict[str, Any]]:
    """List conversations ordered by most recently updated, with optional search."""
    with _db_lock, _get_connection() as conn:
        if search and search.strip():
            query_pattern = f"%{search.strip().lower()}%"
            # Match title or message content
            cursor = conn.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN messages m ON c.id = m.conversation_id
                WHERE LOWER(c.title) LIKE ?
                   OR c.id IN (
                       SELECT conversation_id FROM messages WHERE LOWER(content) LIKE ?
                   )
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                """,
                (query_pattern, query_pattern),
            )
        else:
            cursor = conn.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN messages m ON c.id = m.conversation_id
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                """
            )

        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": row["message_count"],
            })
        return results


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve conversation details by ID, or None if not found."""
    with _db_lock, _get_connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (conversation_id,),
        ).fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": row["message_count"],
        }


def get_conversation_messages(conversation_id: str) -> Optional[List[Dict[str, Any]]]:
    """Retrieve all messages for a conversation ordered chronologically."""
    with _db_lock, _get_connection() as conn:
        # First verify conversation exists
        exists = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not exists:
            return None

        cursor = conn.execute(
            """
            SELECT id, conversation_id, role, content, timestamp, metadata
            FROM messages
            WHERE conversation_id = ?
            ORDER BY rowid ASC
            """,
            (conversation_id,),
        )

        messages = []
        for row in cursor.fetchall():
            meta = None
            if row["metadata"]:
                try:
                    meta = json.loads(row["metadata"])
                except Exception:
                    meta = None

            messages.append({
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "metadata": meta,
            })
        return messages


def rename_conversation(conversation_id: str, new_title: str) -> Optional[Dict[str, Any]]:
    """Rename a conversation. Returns updated conversation or None if not found."""
    trimmed = new_title.strip()
    if not trimmed:
        raise ValueError("Conversation title cannot be empty.")

    now_iso = datetime.now(timezone.utc).isoformat()
    with _db_lock, _get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE conversations
            SET title = ?, updated_at = ?
            WHERE id = ?
            """,
            (trimmed, now_iso, conversation_id),
        )
        if cursor.rowcount == 0:
            return None

    return get_conversation(conversation_id)


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and its messages. Returns True if deleted, False if not found."""
    global _active_conversation_id
    with _db_lock, _get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        if cursor.rowcount == 0:
            return False

        if _active_conversation_id == conversation_id:
            _active_conversation_id = None

    logger.info("Deleted conversation %s.", conversation_id)
    return True


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a single message to a conversation and update its timestamp."""
    msg_id = message_id or f"msg_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata) if metadata else None

    with _db_lock, _get_connection() as conn:
        conv_row = conn.execute(
            "SELECT id, title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not conv_row:
            raise KeyError(f"Conversation '{conversation_id}' does not exist.")

        # If it's the first user message and title is "New Chat", generate title
        if role == "user" and conv_row["title"] == "New Chat":
            new_title = generate_title(content)
            conn.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_title, now_iso, conversation_id),
            )
        else:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now_iso, conversation_id),
            )

        conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (msg_id, conversation_id, role, content, now_iso, meta_json),
        )

    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "timestamp": now_iso,
        "metadata": metadata,
    }


def add_interaction(
    conversation_id: Optional[str],
    user_text: str,
    assistant_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Append a user message and assistant reply pair atomically to the conversation.
    If conversation_id is None, appends to the active conversation.
    """
    target_conv_id = conversation_id or get_active_conversation_id()

    user_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    ast_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    user_meta = None
    ast_meta_json = json.dumps(metadata) if metadata else None

    with _db_lock, _get_connection() as conn:
        conv_row = conn.execute(
            "SELECT id, title FROM conversations WHERE id = ?", (target_conv_id,)
        ).fetchone()
        if not conv_row:
            # Auto-create if somehow missing
            target_conv_id = f"conv_{uuid.uuid4().hex[:12]}"
            final_title = generate_title(user_text)
            conn.execute(
                """
                INSERT INTO conversations (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (target_conv_id, final_title, now_iso, now_iso),
            )
        else:
            if conv_row["title"] == "New Chat":
                new_title = generate_title(user_text)
                conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (new_title, now_iso, target_conv_id),
                )
            else:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now_iso, target_conv_id),
                )

        conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_msg_id, target_conv_id, "user", user_text, now_iso, user_meta),
        )
        conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ast_msg_id, target_conv_id, "assistant", assistant_text, now_iso, ast_meta_json),
        )

    logger.info("Saved interaction to conversation %s.", target_conv_id)
    return {
        "conversation_id": target_conv_id,
        "user_message": {
            "id": user_msg_id,
            "role": "user",
            "content": user_text,
            "timestamp": now_iso,
        },
        "assistant_message": {
            "id": ast_msg_id,
            "role": "assistant",
            "content": assistant_text,
            "timestamp": now_iso,
            "metadata": metadata,
        },
    }


# Auto-initialize tables on module import
init_conversation_db()
