"""
backend/task_store.py
---------------------
Persistent SQLite storage layer for J.A.R.V.I.S. tasks, reminders, and scheduling.

Reuses the project-wide SQLite database (memory.db) with thread-safe locking,
Write-Ahead Logging (WAL), parameterized queries, and UTC ISO-8601 timestamps.
Supports one-time reminders and practical recurrence patterns ('daily', 'weekly', 'weekday').
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("backend.task_store")

# Resolve DB_PATH from project memory module or fallback
try:
    from memory import DB_PATH
except ImportError:
    DB_PATH = Path(__file__).resolve().parents[1] / "memory.db"

_db_lock = threading.Lock()

VALID_STATUSES = {"pending", "completed", "cancelled"}
VALID_RECURRENCES = {"daily", "weekly", "weekday"}


def get_local_tz():
    """Return the system's local timezone."""
    return datetime.now().astimezone().tzinfo


def _get_connection() -> sqlite3.Connection:
    """Return an optimized, thread-safe connection to the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_task_db() -> None:
    """Initialize task and reminder tables and indexes idempotently."""
    with _db_lock, _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                due_at TEXT,
                reminder_at TEXT,
                recurrence TEXT,
                last_fired_at TEXT,
                completed_at TEXT,
                cancelled_at TEXT,
                conversation_id TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminder_logs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_reminder_at ON tasks(reminder_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reminder_logs_task ON reminder_logs(task_id);")

    logger.info("Task and reminder database initialized at %s.", DB_PATH)


def parse_datetime_to_utc_iso(dt_input: Optional[str]) -> Optional[str]:
    """
    Parse a datetime string (ISO format or common strings) and return
    a standard UTC ISO-8601 string ending in 'Z'.
    If dt_input has no timezone info, it is assumed to be in the local system timezone.
    """
    if not dt_input or not dt_input.strip():
        return None

    clean = dt_input.strip()
    # Normalize trailing Z to +00:00 for fromisoformat if needed
    iso_clean = clean.replace("Z", "+00:00") if clean.endswith("Z") else clean

    try:
        dt = datetime.fromisoformat(iso_clean)
    except ValueError:
        # Fallback formats
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(clean, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Cannot parse datetime '{dt_input}'. Expected ISO-8601 format.")

    if dt.tzinfo is None:
        # Naive datetime: interpret in local timezone
        local_tz = get_local_tz()
        dt = dt.replace(tzinfo=local_tz)

    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_utc_to_local_str(utc_iso: Optional[str]) -> Optional[str]:
    """Convert a UTC ISO string to a human-readable local time string."""
    if not utc_iso:
        return None
    try:
        clean = utc_iso.replace("Z", "+00:00") if utc_iso.endswith("Z") else utc_iso
        dt = datetime.fromisoformat(clean)
        local_dt = dt.astimezone(get_local_tz())
        return local_dt.strftime("%Y-%m-%d %I:%M %p")
    except Exception:
        return utc_iso


def compute_next_occurrence(current_dt_utc: datetime, recurrence: str) -> Optional[datetime]:
    """
    Compute next occurrence after current_dt_utc according to recurrence pattern.
    Recurrence is evaluated in system local time to preserve local wall-clock hour/minute.
    """
    local_tz = get_local_tz()
    local_dt = current_dt_utc.astimezone(local_tz)

    rec = recurrence.lower().strip()
    if rec == "daily":
        next_local = local_dt + timedelta(days=1)
    elif rec == "weekly":
        next_local = local_dt + timedelta(days=7)
    elif rec == "weekday":
        # Monday is 0, Friday is 4, Saturday is 5, Sunday is 6
        weekday = local_dt.weekday()
        if weekday == 4:  # Friday -> Monday
            next_local = local_dt + timedelta(days=3)
        elif weekday == 5:  # Saturday -> Monday
            next_local = local_dt + timedelta(days=2)
        else:  # Sun-Thu -> next day
            next_local = local_dt + timedelta(days=1)
    else:
        return None

    return next_local.astimezone(timezone.utc)


def create_task(
    title: str,
    description: Optional[str] = None,
    due_at: Optional[str] = None,
    reminder_at: Optional[str] = None,
    recurrence: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new persistent task."""
    title_clean = title.strip()
    if not title_clean:
        raise ValueError("Task title cannot be empty.")

    desc_clean = description.strip() if description else None

    # Validate recurrence
    rec_clean = None
    if recurrence:
        rec_clean = recurrence.strip().lower()
        if rec_clean not in VALID_RECURRENCES:
            raise ValueError(
                f"Unsupported recurrence '{recurrence}'. Must be one of: {', '.join(sorted(VALID_RECURRENCES))}."
            )

    due_utc = parse_datetime_to_utc_iso(due_at)
    reminder_utc = parse_datetime_to_utc_iso(reminder_at)

    task_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, description, status, created_at, updated_at,
                due_at, reminder_at, recurrence, conversation_id
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                title_clean,
                desc_clean,
                now_utc,
                now_utc,
                due_utc,
                reminder_utc,
                rec_clean,
                conversation_id,
            ),
        )

    return get_task(task_id)  # type: ignore[return-value]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a task by ID."""
    with _db_lock, _get_connection() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return dict(row)


def list_tasks(
    status: Optional[str] = None,
    due_before: Optional[str] = None,
    due_after: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List tasks with optional status and due date filters."""
    query = "SELECT * FROM tasks WHERE 1=1"
    params: List[Any] = []

    if status:
        st_clean = status.strip().lower()
        if st_clean in VALID_STATUSES:
            query += " AND status = ?"
            params.append(st_clean)

    if due_before:
        due_before_utc = parse_datetime_to_utc_iso(due_before)
        if due_before_utc:
            query += " AND due_at IS NOT NULL AND due_at <= ?"
            params.append(due_before_utc)

    if due_after:
        due_after_utc = parse_datetime_to_utc_iso(due_after)
        if due_after_utc:
            query += " AND due_at IS NOT NULL AND due_at >= ?"
            params.append(due_after_utc)

    # Order by due_at ascending (nulls last), then created_at descending
    query += " ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at ASC, created_at DESC LIMIT ?"
    params.append(limit)

    with _db_lock, _get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def list_upcoming_reminders(limit: int = 20, window_hours: int = 72) -> List[Dict[str, Any]]:
    """List pending tasks that have an upcoming reminder scheduled."""
    now_utc = datetime.now(timezone.utc)
    window_end = now_utc + timedelta(hours=window_hours)
    window_end_str = window_end.strftime("%Y-%m-%dT%H:%M:%SZ")

    with _db_lock, _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
              AND reminder_at IS NOT NULL
              AND reminder_at <= ?
            ORDER BY reminder_at ASC
            LIMIT ?
            """,
            (window_end_str, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def update_task(task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update fields on an existing task with validation."""
    existing = get_task(task_id)
    if not existing:
        return None

    allowed_fields = {"title", "description", "status", "due_at", "reminder_at", "recurrence"}
    set_clauses = []
    params: List[Any] = []

    if "title" in updates:
        t = str(updates["title"]).strip()
        if not t:
            raise ValueError("Task title cannot be empty.")
        set_clauses.append("title = ?")
        params.append(t)

    if "description" in updates:
        d = updates["description"]
        d_clean = str(d).strip() if d else None
        set_clauses.append("description = ?")
        params.append(d_clean)

    if "status" in updates:
        s = str(updates["status"]).strip().lower()
        if s not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{s}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}.")
        set_clauses.append("status = ?")
        params.append(s)

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if s == "completed":
            set_clauses.append("completed_at = ?")
            params.append(now_utc)
        elif s == "cancelled":
            set_clauses.append("cancelled_at = ?")
            params.append(now_utc)

    if "due_at" in updates:
        due = parse_datetime_to_utc_iso(updates["due_at"])
        set_clauses.append("due_at = ?")
        params.append(due)

    if "reminder_at" in updates:
        rem = parse_datetime_to_utc_iso(updates["reminder_at"])
        set_clauses.append("reminder_at = ?")
        params.append(rem)

    if "recurrence" in updates:
        rec = updates["recurrence"]
        if rec:
            rec_clean = str(rec).strip().lower()
            if rec_clean not in VALID_RECURRENCES:
                raise ValueError(
                    f"Unsupported recurrence '{rec}'. Must be one of: {', '.join(sorted(VALID_RECURRENCES))}."
                )
            set_clauses.append("recurrence = ?")
            params.append(rec_clean)
        else:
            set_clauses.append("recurrence = NULL")

    if not set_clauses:
        return existing

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_clauses.append("updated_at = ?")
    params.append(now_utc)

    params.append(task_id)
    query = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ?"

    with _db_lock, _get_connection() as conn:
        conn.execute(query, params)

    return get_task(task_id)


def complete_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Mark a task as completed."""
    return update_task(task_id, {"status": "completed"})


def cancel_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Cancel a task."""
    return update_task(task_id, {"status": "cancelled"})


def delete_task(task_id: str) -> bool:
    """Safely delete a task by ID. Returns True if deleted, False if not found."""
    with _db_lock, _get_connection() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


def snooze_reminder(task_id: str, minutes: int = 10) -> Optional[Dict[str, Any]]:
    """Snooze a task reminder by the specified number of minutes from now."""
    if minutes <= 0:
        minutes = 10

    existing = get_task(task_id)
    if not existing:
        return None

    if existing["status"] != "pending":
        raise ValueError(f"Cannot snooze {existing['status']} task '{task_id}'.")

    now_utc = datetime.now(timezone.utc)
    new_reminder_dt = now_utc + timedelta(minutes=minutes)
    new_reminder_iso = new_reminder_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET reminder_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_reminder_iso, now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), task_id),
        )

    return get_task(task_id)


def dismiss_reminder(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Dismiss a due reminder.
    If the task has a recurrence rule, advances reminder_at to the next occurrence.
    If one-time, clears reminder_at (sets to NULL) so it will not fire again.
    """
    existing = get_task(task_id)
    if not existing:
        return None

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = existing.get("recurrence")

    next_reminder_iso = None
    if rec and rec in VALID_RECURRENCES:
        # Calculate next occurrence based on original reminder_at (or now if overdue)
        base_str = existing.get("reminder_at") or now_iso
        try:
            base_dt = datetime.fromisoformat(base_str.replace("Z", "+00:00"))
        except Exception:
            base_dt = now_utc

        # Keep advancing until strictly in the future
        next_dt = compute_next_occurrence(base_dt, rec)
        while next_dt and next_dt <= now_utc:
            next_dt = compute_next_occurrence(next_dt, rec)

        if next_dt:
            next_reminder_iso = next_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET reminder_at = ?, last_fired_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_reminder_iso, now_iso, now_iso, task_id),
        )

    return get_task(task_id)


def claim_and_advance_due_reminders(before_utc_iso: str) -> List[Dict[str, Any]]:
    """
    Atomically claim all pending reminders scheduled at or before before_utc_iso.
    To prevent duplicate firing, this updates last_fired_at and advances or clears
    reminder_at immediately within an atomic transaction before returning the claimed tasks.
    """
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    claimed_tasks: List[Dict[str, Any]] = []

    with _db_lock, _get_connection() as conn:
        # Atomic query for due reminders
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
              AND reminder_at IS NOT NULL
              AND reminder_at <= ?
            ORDER BY reminder_at ASC
            """,
            (before_utc_iso,),
        ).fetchall()

        for r in rows:
            task = dict(r)
            task_id = task["id"]
            rec = task.get("recurrence")
            original_reminder = task.get("reminder_at")

            next_reminder_iso = None
            if rec and rec in VALID_RECURRENCES and original_reminder:
                try:
                    orig_dt = datetime.fromisoformat(original_reminder.replace("Z", "+00:00"))
                except Exception:
                    orig_dt = now_utc

                next_dt = compute_next_occurrence(orig_dt, rec)
                while next_dt and next_dt <= now_utc:
                    next_dt = compute_next_occurrence(next_dt, rec)

                if next_dt:
                    next_reminder_iso = next_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Update atomically: update reminder_at and set last_fired_at
            conn.execute(
                """
                UPDATE tasks
                SET reminder_at = ?, last_fired_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_reminder_iso, now_iso, now_iso, task_id),
            )

            # Attach metadata to claimed task for delivery logger
            task["claimed_scheduled_for"] = original_reminder
            claimed_tasks.append(task)

    return claimed_tasks


def log_reminder_delivery(
    task_id: str,
    scheduled_for: str,
    channel: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Record an outcome in reminder_logs table."""
    log_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO reminder_logs (id, task_id, scheduled_for, delivered_at, channel, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (log_id, task_id, scheduled_for, now_iso, channel, status, error),
        )
