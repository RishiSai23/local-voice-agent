"""
backend/scheduler.py
--------------------
Background task scheduler and reminder execution engine for J.A.R.V.I.S.

Features:
- Seamless FastAPI application lifespan integration (start on startup, clean stop on shutdown).
- Singleton instance prevention (strictly one scheduler loop per process).
- Pure SQLite state recovery: pending reminders survive backend restarts and resume seamlessly.
- Duplicate-delivery prevention: atomic transactional claiming advances recurrence or clears
  reminder_at in SQLite before delivery dispatch.
- Overdue reminder handling: missed reminders during server downtime are delivered upon restart.
- Non-blocking notification dispatch: delivery callbacks execute on separate threads.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

try:
    from backend.notifier import deliver_reminder
    from backend.task_store import claim_and_advance_due_reminders, init_task_db
except ImportError:
    from notifier import deliver_reminder
    from task_store import claim_and_advance_due_reminders, init_task_db

logger = logging.getLogger("backend.scheduler")

_scheduler_instance: Optional["TaskScheduler"] = None
_instance_lock = threading.Lock()


class TaskScheduler:
    """Thread-safe background scheduler polling SQLite for due tasks and reminders."""

    def __init__(self, poll_interval_seconds: float = 5.0) -> None:
        self.poll_interval = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        """Start the scheduler background daemon thread if not already running."""
        with self._lock:
            if self._running:
                logger.info("TaskScheduler is already running.")
                return

            # Ensure task schema exists
            init_task_db()

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="JarvisTaskSchedulerThread",
                daemon=True,
            )
            self._running = True
            self._thread.start()
            logger.info("TaskScheduler started with poll interval of %.1fs.", self.poll_interval)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background loop to stop and wait for thread termination."""
        with self._lock:
            if not self._running:
                return
            logger.info("Stopping TaskScheduler...")
            self._stop_event.set()
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            logger.info("TaskScheduler stopped cleanly.")

    def trigger_check(self) -> int:
        """Manually trigger a check for due reminders right now. Returns count of claimed tasks."""
        return self._check_and_dispatch()

    def _run_loop(self) -> None:
        """Main loop executed by the background thread."""
        logger.info("TaskScheduler background loop started.")
        # Perform an initial check immediately upon boot to handle overdue items from downtime
        try:
            self._check_and_dispatch()
        except Exception as exc:
            logger.error("Initial scheduler check encountered error: %s", exc)

        while not self._stop_event.is_set():
            # Wait for poll_interval or until stop is requested
            if self._stop_event.wait(timeout=self.poll_interval):
                break

            try:
                self._check_and_dispatch()
            except Exception as exc:
                logger.error("Scheduler loop encountered error: %s", exc)

        logger.info("TaskScheduler background loop exited.")

    def _check_and_dispatch(self) -> int:
        """Claim due reminders atomically and dispatch delivery non-blockingly."""
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Atomically claim all due reminders up to right now
        claimed_tasks = claim_and_advance_due_reminders(now_iso)
        if not claimed_tasks:
            return 0

        logger.info("Claimed %d due reminder(s) for delivery.", len(claimed_tasks))
        for task in claimed_tasks:
            # Dispatch each reminder delivery on a separate worker thread to avoid blocking the scheduler loop
            worker = threading.Thread(
                target=self._dispatch_task,
                args=(task,),
                name=f"ReminderDelivery-{task['id'][:8]}",
                daemon=True,
            )
            worker.start()

        return len(claimed_tasks)

    def _dispatch_task(self, task: dict) -> None:
        """Worker thread entrypoint for delivering a single reminder."""
        try:
            deliver_reminder(task)
        except Exception as exc:
            logger.error("Unexpected error in reminder delivery for task %s: %s", task.get("id"), exc)


def get_task_scheduler() -> TaskScheduler:
    """Return the global TaskScheduler singleton."""
    global _scheduler_instance
    with _instance_lock:
        if _scheduler_instance is None:
            _scheduler_instance = TaskScheduler(poll_interval_seconds=5.0)
        return _scheduler_instance
