"""
backend/feedback_store.py
--------------------------
Persistent SQLite-backed storage layer for J.A.R.V.I.S. hackathon feedback.

Provides thread-safe storage for feedback records captured locally from the
voice assistant. Each record keeps:

- A unique id
- The original transcript (never replaced)
- An optional cleaned-up summary
- An optional category
- An optional event / project name
- Optional user-provided tags (stored as JSON)
- A UTC ISO-8601 creation timestamp

Reuses the project-wide SQLite database (memory.db) with thread-safe locking,
Write-Ahead Logging (WAL), parameterized queries, and UTC ISO-8601 timestamps.
All processing stays local; no cloud APIs or external data transmission.
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("backend.feedback_store")

# Resolve DB_PATH from project memory module or fallback
try:
    from memory import DB_PATH
except ImportError:
    DB_PATH = Path(__file__).resolve().parents[1] / "memory.db"

_db_lock = threading.Lock()

# Reasonable length caps to keep feedback records bounded.
MAX_TRANSCRIPT_LENGTH = 10000
MAX_SUMMARY_LENGTH = 5000
MAX_CATEGORY_LENGTH = 200
MAX_EVENT_NAME_LENGTH = 200
MAX_TAGS_LENGTH = 1000


def _get_connection() -> sqlite3.Connection:
    """Return an optimized, thread-safe connection to the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging (WAL) for concurrent reads/writes and
    # enforce foreign keys.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_feedback_db() -> None:
    """Initialize the feedback table and indexes idempotently.

    Safe to call repeatedly; existing tables and data are preserved.
    """
    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                transcript TEXT NOT NULL,
                summary TEXT,
                category TEXT,
                event_name TEXT,
                tags TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_created_at "
            "ON feedback(created_at DESC);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_category "
            "ON feedback(category);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_event_name "
            "ON feedback(event_name);"
        )

    logger.info("Feedback database initialized at %s.", DB_PATH)

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Convert a feedback row to a plain dict, parsing tags JSON."""
    if row is None:
        return None
    data = dict(row)
    tags_raw = data.get("tags")
    if tags_raw:
        try:
            data["tags"] = json.loads(tags_raw)
        except (json.JSONDecodeError, TypeError):
            data["tags"] = None
    else:
        data["tags"] = None
    return data

def _validate_feedback_input(
    transcript: str,
    summary: Optional[str],
    category: Optional[str],
    event_name: Optional[str],
    tags: Optional[List[str]],
) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[List[str]]]:
    """Validate and normalize feedback creation input.

    Returns a tuple of sanitized values. Raises ValueError on invalid input.
    """
    if not transcript or not transcript.strip():
        raise ValueError("transcript cannot be empty or whitespace only.")
    transcript = transcript.strip()
    if len(transcript) > MAX_TRANSCRIPT_LENGTH:
        raise ValueError(
            f"transcript exceeds maximum length of {MAX_TRANSCRIPT_LENGTH} characters."
        )

    if summary is not None:
        summary = summary.strip()
        if summary == "":
            summary = None
        elif len(summary) > MAX_SUMMARY_LENGTH:
            raise ValueError(
                f"summary exceeds maximum length of {MAX_SUMMARY_LENGTH} characters."
            )

    if category is not None:
        category = category.strip()
        if category == "":
            category = None
        elif len(category) > MAX_CATEGORY_LENGTH:
            raise ValueError(
                f"category exceeds maximum length of {MAX_CATEGORY_LENGTH} characters."
            )

    if event_name is not None:
        event_name = event_name.strip()
        if event_name == "":
            event_name = None
        elif len(event_name) > MAX_EVENT_NAME_LENGTH:
            raise ValueError(
                f"event_name exceeds maximum length of {MAX_EVENT_NAME_LENGTH} characters."
            )

    if tags is not None:
        if not isinstance(tags, list):
            raise ValueError("tags must be a list of strings.")
        cleaned_tags: List[str] = []
        for tag in tags:
            if not isinstance(tag, str):
                raise ValueError("each tag must be a string.")
            tag = tag.strip()
            if tag:
                cleaned_tags.append(tag)
        if not cleaned_tags:
            tags = None
        else:
            tags = cleaned_tags

    return transcript, summary, category, event_name, tags


def create_feedback(
    transcript: str,
    summary: Optional[str] = None,
    category: Optional[str] = None,
    event_name: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create and persist a new feedback record.

    The original transcript is always preserved; an optional summary does not
    replace it. Returns the created record as a dict.
    """
    transcript, summary, category, event_name, tags = _validate_feedback_input(
        transcript, summary, category, event_name, tags
    )

    feedback_id = f"fb_{uuid.uuid4().hex[:12]}"
    now_iso = _now_iso()
    tags_json = json.dumps(tags, ensure_ascii=False) if tags else None

    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO feedback (id, transcript, summary, category, event_name, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (feedback_id, transcript, summary, category, event_name, tags_json, now_iso),
        )

    record = {
        "id": feedback_id,
        "transcript": transcript,
        "summary": summary,
        "category": category,
        "event_name": event_name,
        "tags": tags,
        "created_at": now_iso,
    }
    logger.info("Created feedback record %s.", feedback_id)
    return record


def list_feedback(limit: int = 100) -> List[Dict[str, Any]]:
    """List feedback records newest first (by created_at descending)."""
    if limit < 1:
        limit = 100
    with _db_lock, _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, transcript, summary, category, event_name, tags, created_at
            FROM feedback
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_feedback(feedback_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single feedback record by ID, or None if not found."""
    with _db_lock, _get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, transcript, summary, category, event_name, tags, created_at
            FROM feedback
            WHERE id = ?
            """,
            (feedback_id,),
        ).fetchone()
    return _row_to_dict(row)


def search_feedback(query: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Search feedback across transcript, summary, category, event name, and tags.

    Matching is case-insensitive and uses a LIKE pattern over the raw text.
    Tags are stored as JSON text, so a tag substring match searches that text.
    """
    if not query or not query.strip():
        return []
    if limit < 1:
        limit = 100
    pattern = f"%{query.strip()}%"
    with _db_lock, _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, transcript, summary, category, event_name, tags, created_at
            FROM feedback
            WHERE lower(transcript) LIKE lower(?)
               OR lower(summary) LIKE lower(?)
               OR lower(category) LIKE lower(?)
               OR lower(event_name) LIKE lower(?)
               OR lower(tags) LIKE lower(?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def delete_feedback(feedback_id: str) -> bool:
    """Delete a feedback record by ID. Returns True if a record was deleted."""
    with _db_lock, _get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM feedback WHERE id = ?",
            (feedback_id,),
        )
    deleted = cursor.rowcount > 0
    if deleted:
        logger.info("Deleted feedback record %s.", feedback_id)
    return deleted


# Columns that callers are permitted to update.  transcript, id, and
# created_at are intentionally absent — they are write-once fields.
_UPDATABLE_FIELDS: Dict[str, Optional[int]] = {
    "summary": MAX_SUMMARY_LENGTH,
    "category": MAX_CATEGORY_LENGTH,
    "event_name": MAX_EVENT_NAME_LENGTH,
    "tags": None,  # tags are validated as a list, not by raw length
}


def update_feedback(
    feedback_id: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Update mutable metadata fields of an existing feedback record.

    Only ``summary``, ``category``, ``event_name``, and ``tags`` may be
    changed.  ``transcript``, ``id``, and ``created_at`` are immutable and
    are silently ignored if callers attempt to include them — callers that
    pass those keys will receive a ValueError via the unknown-field check
    before any SQL is executed.

    Parameters
    ----------
    feedback_id:
        ID of the record to update (e.g. ``"fb_abc123456789"``).
    updates:
        Mapping of field name → new value.  Only the keys listed in
        ``_UPDATABLE_FIELDS`` are accepted.  Passing an empty dict or a
        dict containing only unknown keys raises ``ValueError``.

    Returns
    -------
    Dict[str, Any]
        The complete refreshed record after the update, via the same
        ``_row_to_dict`` path used by every other read function.
    None
        When no record with ``feedback_id`` exists in the database.

    Raises
    ------
    ValueError
        For an empty update dict, unknown field names, or any field-level
        validation failure (type errors, values exceeding length caps, etc.).
    """
    if not updates:
        raise ValueError("updates must not be empty.")

    # --- 1. Reject unknown fields up-front, before any DB access ----------
    unknown = set(updates.keys()) - set(_UPDATABLE_FIELDS.keys())
    if unknown:
        raise ValueError(
            f"Unknown or immutable field(s): {sorted(unknown)}. "
            f"Updatable fields are: {sorted(_UPDATABLE_FIELDS.keys())}."
        )

    # --- 2. Validate and normalise each supplied field --------------------
    # Reuse the same rules as _validate_feedback_input so behaviour is
    # identical whether a field is set at creation time or updated later.

    validated: Dict[str, Any] = {}

    if "summary" in updates:
        val = updates["summary"]
        if val is not None:
            if not isinstance(val, str):
                raise ValueError("summary must be a string or None.")
            val = val.strip()
            if val == "":
                val = None
            elif len(val) > MAX_SUMMARY_LENGTH:
                raise ValueError(
                    f"summary exceeds maximum length of {MAX_SUMMARY_LENGTH} characters."
                )
        validated["summary"] = val

    if "category" in updates:
        val = updates["category"]
        if val is not None:
            if not isinstance(val, str):
                raise ValueError("category must be a string or None.")
            val = val.strip()
            if val == "":
                val = None
            elif len(val) > MAX_CATEGORY_LENGTH:
                raise ValueError(
                    f"category exceeds maximum length of {MAX_CATEGORY_LENGTH} characters."
                )
        validated["category"] = val

    if "event_name" in updates:
        val = updates["event_name"]
        if val is not None:
            if not isinstance(val, str):
                raise ValueError("event_name must be a string or None.")
            val = val.strip()
            if val == "":
                val = None
            elif len(val) > MAX_EVENT_NAME_LENGTH:
                raise ValueError(
                    f"event_name exceeds maximum length of {MAX_EVENT_NAME_LENGTH} characters."
                )
        validated["event_name"] = val

    if "tags" in updates:
        val = updates["tags"]
        if val is not None:
            if not isinstance(val, list):
                raise ValueError("tags must be a list of strings or None.")
            cleaned: List[str] = []
            for tag in val:
                if not isinstance(tag, str):
                    raise ValueError("Each tag must be a string.")
                tag = tag.strip()
                if tag:
                    cleaned.append(tag)
            val = cleaned if cleaned else None
        validated["tags"] = val

    if not validated:
        # All supplied keys were present in _UPDATABLE_FIELDS but produced
        # no validated output (should not happen given the checks above,
        # but guard defensively).
        raise ValueError("updates resolved to no valid fields after validation.")

    # --- 3. Serialise tags for storage ------------------------------------
    # Use the same json.dumps call as create_feedback (ensure_ascii=False).
    sql_values: Dict[str, Any] = {}
    for field, value in validated.items():
        if field == "tags":
            sql_values["tags"] = (
                json.dumps(value, ensure_ascii=False) if value is not None else None
            )
        else:
            sql_values[field] = value

    # --- 4. Build a parameterized UPDATE statement -----------------------
    # Column names come exclusively from _UPDATABLE_FIELDS (an allowlist);
    # they are never derived from user-supplied strings, so there is no
    # risk of SQL injection from the column-name segment of the query.
    set_clause = ", ".join(f"{col} = ?" for col in sql_values)
    params = list(sql_values.values()) + [feedback_id]

    with _db_lock, _get_connection() as conn:
        cursor = conn.execute(
            f"UPDATE feedback SET {set_clause} WHERE id = ?",  # noqa: S608
            params,
        )
        if cursor.rowcount == 0:
            # No rows matched → record does not exist.
            return None

    logger.info(
        "Updated feedback record %s: fields=%s.",
        feedback_id,
        sorted(sql_values.keys()),
    )

    # --- 5. Return the refreshed record via the standard read path -------
    return get_feedback(feedback_id)


def get_feedback_store_health() -> Dict[str, Any]:
    """Return a small health/diagnostic indication for the feedback store.

    Fits the existing per-subsystem health conventions (see agent.check_health
    and backend/diagnostics.py). All checks are local and read-only.
    """
    try:
        with _get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM feedback"
            ).fetchone()
            count = row["count"] if row else 0
            db_path = str(DB_PATH)
        return {
            "status": "healthy",
            "database": db_path,
            "table": "feedback",
            "record_count": count,
        }
    except Exception as err:  # noqa: BLE001
        return {
            "status": f"unhealthy: {err}",
            "database": str(DB_PATH),
            "table": "feedback",
            "record_count": 0,
        }


