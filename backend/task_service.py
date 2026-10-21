"""
backend/task_service.py
-----------------------
Natural language task & reminder routing, parsing, and execution service for J.A.R.V.I.S.

Provides deterministic parsing and execution of task commands:
- "Remind me tomorrow at 9 AM to submit the assignment."
- "Remind me in 30 minutes to call home."
- "What tasks do I have today?"
- "What reminders are coming up?"
- "Mark the assignment task as completed."
- "Cancel my 6 PM reminder."
- "Remind me every weekday at 8 AM to check my schedule."
- "Snooze that reminder for 10 minutes."
- "Dismiss that reminder."

Guarantees:
- Never executes arbitrary code or SQL.
- Validates all dates against local system timezone.
- Asks for clarification when timing or target task is ambiguous.
- Emits structured toolCall metadata for telemetry and UI synchronization.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from backend.task_store import (
        cancel_task,
        complete_task,
        create_task,
        dismiss_reminder,
        format_utc_to_local_str,
        get_local_tz,
        list_tasks,
        list_upcoming_reminders,
        snooze_reminder,
    )
except ImportError:
    from task_store import (
        cancel_task,
        complete_task,
        create_task,
        dismiss_reminder,
        format_utc_to_local_str,
        get_local_tz,
        list_tasks,
        list_upcoming_reminders,
        snooze_reminder,
    )

logger = logging.getLogger("backend.task_service")


def _clean_input(text: str) -> str:
    """Strip common conversational prefixes and normalize whitespace."""
    lower = text.strip().lower()
    cleaned = re.sub(
        r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis[,\s]+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?(?:please\s+)?",
        "",
        lower,
    ).strip()
    return re.sub(r"\s+", " ", cleaned).strip(".!? ")


def _parse_time_offset(text: str) -> Optional[timedelta]:
    """Parse relative expressions like 'in 30 minutes', 'in 1 hour', 'in 45 mins'."""
    match = re.search(r"\bin\s+(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", text)
    if not match:
        return None
    val = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("sec"):
        return timedelta(seconds=val)
    elif unit.startswith("min"):
        return timedelta(minutes=val)
    elif unit.startswith("hour") or unit.startswith("hr"):
        return timedelta(hours=val)
    return None


def _parse_time_of_day(text: str) -> Optional[Tuple[int, int]]:
    """Parse time of day like 'at 9 am', 'at 9:30 PM', 'at 18:00', 'at 6pm'."""
    # Pattern 1: 'at 9:30 am', 'at 9 am', 'at 9pm'
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        meridiem = match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return (hour, minute)

    # Pattern 2: '9 am' or '6:30 pm' without 'at'
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        meridiem = match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return (hour, minute)

    # Pattern 3: 'tonight' defaults to 8:00 PM if no time specified
    if "tonight" in text:
        return (20, 0)
    # Pattern 4: 'noon'
    if "noon" in text:
        return (12, 0)
    # Pattern 5: 'midnight'
    if "midnight" in text:
        return (0, 0)

    return None


def _parse_schedule(text: str) -> Tuple[Optional[datetime], Optional[str], Optional[str]]:
    """
    Parse date/time and recurrence from natural language.
    Returns: (scheduled_datetime_local, recurrence_str, error_or_clarification_question)
    """
    local_tz = get_local_tz()
    now_local = datetime.now(local_tz)

    # Check recurrence
    recurrence = None
    if re.search(r"\bevery\s+weekday\b", text):
        recurrence = "weekday"
    elif re.search(r"\b(?:every\s+day|daily)\b", text):
        recurrence = "daily"
    elif re.search(r"\b(?:every\s+week|weekly)\b", text):
        recurrence = "weekly"

    # Check relative offset ('in X minutes')
    offset = _parse_time_offset(text)
    if offset:
        target_dt = now_local + offset
        return (target_dt, recurrence, None)

    time_of_day = _parse_time_of_day(text)

    # Check day target
    is_tomorrow = "tomorrow" in text
    is_today = "today" in text or "tonight" in text

    if recurrence:
        # For recurring weekday/daily/weekly, default hour is parsed or 9 AM if not specified
        hour, minute = time_of_day if time_of_day else (9, 0)
        target_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_dt <= now_local:
            target_dt += timedelta(days=1)
        return (target_dt, recurrence, None)

    if time_of_day:
        hour, minute = time_of_day
        if is_tomorrow:
            base_date = (now_local + timedelta(days=1)).date()
            target_dt = datetime(base_date.year, base_date.month, base_date.day, hour, minute, tzinfo=local_tz)
            return (target_dt, None, None)
        else:
            # If today was specified or no day specified:
            target_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_dt <= now_local and not is_today:
                # If time already passed today and user didn't explicitly say 'today', assume tomorrow
                target_dt += timedelta(days=1)
            elif target_dt <= now_local and is_today:
                return (None, None, f"That time ({hour:02d}:{minute:02d}) has already passed today. Did you mean tomorrow?")
            return (target_dt, None, None)

    if is_tomorrow:
        # Default to 9:00 AM tomorrow if no hour given
        base_date = (now_local + timedelta(days=1)).date()
        target_dt = datetime(base_date.year, base_date.month, base_date.day, 9, 0, tzinfo=local_tz)
        return (target_dt, None, None)

    # Could not determine schedule
    return (None, None, "ambiguous_time")


def handle_task_command(user_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Evaluate user text against task & reminder intents.
    Returns: (reply_text, tool_call_dict) if matched, or None if message is not a task command.
    """
    clean = _clean_input(user_text)

    # -------------------------------------------------------------------------
    # 1. Snooze Reminder
    # -------------------------------------------------------------------------
    snooze_match = re.match(
        r"^(?:snooze\s+(?:that\s+|the\s+)?reminder|snooze)(?:\s+(?:for\s+)?(\d+)\s*(?:min|mins|minutes?))?",
        clean,
    )
    if snooze_match:
        mins_str = snooze_match.group(1)
        minutes = int(mins_str) if mins_str else 10

        # Find most recently fired or pending reminder due soonest
        tasks = list_tasks(status="pending", limit=10)
        if not tasks:
            return (
                "There are no active reminders to snooze.",
                {"name": "tasks.snooze_reminder", "details": "No active tasks", "status": "failed"},
            )

        # Prefer task with last_fired_at or earliest reminder_at
        target = next((t for t in tasks if t.get("last_fired_at")), tasks[0])
        snoozed = snooze_reminder(target["id"], minutes=minutes)
        if snoozed:
            return (
                f"I've snoozed the reminder for '{snoozed['title']}' for {minutes} minutes.",
                {
                    "name": "tasks.snooze_reminder",
                    "details": f"Snoozed {snoozed['title']} for {minutes}m",
                    "status": "success",
                },
            )
        return (
            "I couldn't snooze that reminder.",
            {"name": "tasks.snooze_reminder", "details": "Snooze failed", "status": "failed"},
        )

    # -------------------------------------------------------------------------
    # 2. Dismiss Reminder
    # -------------------------------------------------------------------------
    if re.match(r"^dismiss\s+(?:that\s+|the\s+)?reminder$", clean) or clean == "dismiss":
        tasks = list_tasks(status="pending", limit=10)
        if not tasks:
            return (
                "There are no active reminders to dismiss.",
                {"name": "tasks.dismiss_reminder", "details": "No active tasks", "status": "failed"},
            )
        target = next((t for t in tasks if t.get("last_fired_at")), tasks[0])
        dismissed = dismiss_reminder(target["id"])
        if dismissed:
            return (
                f"I've dismissed the reminder for '{dismissed['title']}'.",
                {
                    "name": "tasks.dismiss_reminder",
                    "details": f"Dismissed {dismissed['title']}",
                    "status": "success",
                },
            )
        return (
            "I couldn't dismiss that reminder.",
            {"name": "tasks.dismiss_reminder", "details": "Dismiss failed", "status": "failed"},
        )

    # -------------------------------------------------------------------------
    # 3. List / Query Tasks
    # -------------------------------------------------------------------------
    if re.search(r"\b(?:what\s+tasks|list\s+tasks|show\s+tasks|my\s+tasks|any\s+tasks)\b", clean):
        tasks = list_tasks(status="pending", limit=10)
        if not tasks:
            return (
                "You have no pending tasks right now.",
                {"name": "tasks.list_tasks", "details": "0 pending tasks", "status": "success"},
            )

        items_str = []
        for i, t in enumerate(tasks[:5], 1):
            due = format_utc_to_local_str(t.get("due_at")) or format_utc_to_local_str(t.get("reminder_at"))
            due_str = f" (due: {due})" if due else ""
            items_str.append(f"{i}. {t['title']}{due_str}")

        count_phrase = f"You have {len(tasks)} pending task{'s' if len(tasks) > 1 else ''}:"
        reply = count_phrase + " " + "; ".join(items_str) + "."
        return (
            reply,
            {"name": "tasks.list_tasks", "details": f"{len(tasks)} tasks", "status": "success"},
        )

    # -------------------------------------------------------------------------
    # 4. List / Query Reminders
    # -------------------------------------------------------------------------
    if re.search(r"\b(?:what\s+reminders|list\s+reminders|show\s+reminders|upcoming\s+reminders)\b", clean):
        reminders = list_upcoming_reminders(limit=5, window_hours=72)
        if not reminders:
            return (
                "You have no upcoming reminders scheduled over the next few days.",
                {"name": "tasks.list_reminders", "details": "0 upcoming reminders", "status": "success"},
            )

        items_str = []
        for i, r in enumerate(reminders, 1):
            rem_time = format_utc_to_local_str(r.get("reminder_at"))
            rec = f" [{r['recurrence']}]" if r.get("recurrence") else ""
            items_str.append(f"{i}. {r['title']} at {rem_time}{rec}")

        reply = f"Here are your upcoming reminders: " + "; ".join(items_str) + "."
        return (
            reply,
            {"name": "tasks.list_reminders", "details": f"{len(reminders)} reminders", "status": "success"},
        )

    # -------------------------------------------------------------------------
    # 5. Complete Task
    # -------------------------------------------------------------------------
    comp_match = re.search(
        r"^(?:mark\s+(?:the\s+)?(.+?)\s+as\s+(?:completed|complete|done)|complete\s+(?:the\s+)?(.+?)(?:\s+task)?|mark\s+(.+?)\s+(?:done|completed|complete))$",
        clean,
    )
    if comp_match:
        target_name = (comp_match.group(1) or comp_match.group(2) or comp_match.group(3)).strip()
        target_name = re.sub(r"\btask\b", "", target_name).strip()

        tasks = list_tasks(status="pending", limit=20)
        matching = [t for t in tasks if target_name.lower() in t["title"].lower()]

        if not matching:
            return (
                f"I couldn't find an open task matching '{target_name}'.",
                {"name": "tasks.complete_task", "details": f"No match for '{target_name}'", "status": "failed"},
            )
        if len(matching) > 1:
            match_titles = ", ".join(f"'{t['title']}'" for t in matching)
            return (
                f"I found multiple open tasks matching '{target_name}': {match_titles}. Which one would you like to complete?",
                {"name": "tasks.complete_task", "details": "Ambiguous match", "status": "failed"},
            )

        completed = complete_task(matching[0]["id"])
        return (
            f"I've marked '{matching[0]['title']}' as completed.",
            {"name": "tasks.complete_task", "details": f"Completed {matching[0]['title']}", "status": "success"},
        )

    # -------------------------------------------------------------------------
    # 6. Cancel Task / Reminder
    # -------------------------------------------------------------------------
    cancel_match = re.search(
        r"^cancel\s+(?:the\s+|my\s+)?(.+?)(?:\s+reminder|\s+task)?$",
        clean,
    )
    if cancel_match:
        target_name = cancel_match.group(1).strip()
        tasks = list_tasks(status="pending", limit=20)

        # If user said e.g. "cancel my 6 PM reminder"
        time_spec = _parse_time_of_day(target_name)
        matching = []
        if time_spec:
            hour, minute = time_spec
            for t in tasks:
                rem_iso = t.get("reminder_at")
                if rem_iso:
                    local_rem = format_utc_to_local_str(rem_iso)
                    if f"{hour % 12 or 12}:" in str(local_rem) or f"{hour}:" in str(local_rem):
                        matching.append(t)
        else:
            matching = [t for t in tasks if target_name.lower() in t["title"].lower()]

        if not matching:
            return (
                f"I couldn't find an active reminder or task matching '{target_name}'.",
                {"name": "tasks.cancel_task", "details": f"No match for '{target_name}'", "status": "failed"},
            )
        if len(matching) > 1:
            match_titles = ", ".join(f"'{t['title']}'" for t in matching)
            return (
                f"I found multiple matching tasks: {match_titles}. Which one would you like to cancel?",
                {"name": "tasks.cancel_task", "details": "Ambiguous match", "status": "failed"},
            )

        cancelled = cancel_task(matching[0]["id"])
        return (
            f"I've cancelled the task '{matching[0]['title']}'.",
            {"name": "tasks.cancel_task", "details": f"Cancelled {matching[0]['title']}", "status": "success"},
        )

    # -------------------------------------------------------------------------
    # 7. Create Task / Reminder
    # -------------------------------------------------------------------------
    remind_match = re.search(r"^(?:remind\s+me|create\s+(?:a\s+)?task|add\s+(?:a\s+)?task)\b(.*)$", clean)
    if remind_match:
        rest = remind_match.group(1).strip()

        # Check if user forgot to provide a subject or time
        if not rest:
            return (
                "What would you like me to remind you about, and when?",
                {"name": "tasks.create_task", "details": "Missing parameters", "status": "failed"},
            )

        # Extract title and timing
        # Case A: "remind me to [title] [time/schedule]"
        # Case B: "remind me [time/schedule] to [title]"
        title = None
        timing_expr = None

        case_b_match = re.match(
            r"^(tomorrow|today|tonight|in\s+\d+\s*\w+|every\s+weekday|every\s+day|every\s+week)(?:\s+at\s+[\d:apm\s]+)?\s+(?:to\s+)?(.+)$",
            rest,
        )
        if case_b_match:
            timing_expr = rest[: case_b_match.start(2)].strip()
            title = case_b_match.group(2).strip()
        else:
            # Case A: look for timing keywords
            # Keywords: "tomorrow", "today", "tonight", "in \d+", "at \d+", "every weekday", "every day"
            keyword_match = re.search(
                r"\b(tomorrow|today|tonight|in\s+\d+\s*\w+|every\s+weekday|every\s+day|every\s+week|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
                rest,
            )
            if keyword_match:
                title = rest[: keyword_match.start()].strip()
                timing_expr = rest[keyword_match.start() :].strip()
            else:
                # No timing expression found at all
                title = re.sub(r"^to\s+", "", rest).strip()
                return (
                    f"When would you like me to remind you to {title}?",
                    {"name": "tasks.create_task", "details": f"Ambiguous schedule for '{title}'", "status": "failed"},
                )

        title = re.sub(r"^to\s+", "", title).strip()
        if not title:
            return (
                "What task or reminder would you like me to set?",
                {"name": "tasks.create_task", "details": "Missing task title", "status": "failed"},
            )

        # Parse timing
        dt_local, recurrence, err = _parse_schedule(timing_expr or "")
        if err == "ambiguous_time" or not dt_local:
            return (
                f"I'm not sure when you want the reminder for '{title}'. Please specify a time, such as 'tomorrow at 9 AM' or 'in 30 minutes'.",
                {"name": "tasks.create_task", "details": "Ambiguous time", "status": "failed"},
            )
        elif err:
            return (
                err,
                {"name": "tasks.create_task", "details": err, "status": "failed"},
            )

        # Convert local target to UTC ISO for database
        dt_utc = dt_local.astimezone(timezone.utc)
        dt_utc_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        created = create_task(
            title=title,
            due_at=dt_utc_iso,
            reminder_at=dt_utc_iso,
            recurrence=recurrence,
        )

        formatted_time = dt_local.strftime("%A at %I:%M %p") if (dt_local.date() != datetime.now(get_local_tz()).date()) else dt_local.strftime("today at %I:%M %p")
        if recurrence:
            reply = f"I've scheduled a recurring reminder to {title} every {recurrence} starting {formatted_time}."
        else:
            reply = f"I've scheduled a reminder to {title} for {formatted_time}."

        return (
            reply,
            {
                "name": "tasks.create_task",
                "details": f"Created '{title}' scheduled for {formatted_time}",
                "status": "success",
            },
        )

    return None
