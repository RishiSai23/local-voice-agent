"""
backend/notifier.py
-------------------
Notification service for J.A.R.V.I.S. reminders and task events.

Provides clean delivery through:
1. Windows Desktop Toast / Balloon Notifications via in-process Win32 C APIs
   (shell32.dll:Shell_NotifyIconW with fallback to user32.dll:MessageBoxTimeoutW).
   - Eliminates all subprocess spawning and PowerShell scripts, preventing antivirus
     heuristics or McAfee AMSI blocks (e.g. WinError 225).
   - Zero external dependencies: implemented purely via Python standard library ctypes.
   - Dispatches native Windows Action Center toasts and notification area balloons.
   - Dedicated message pump on a daemon thread for safe display and automatic disposal.
2. Spoken voice announcements via local Piper TTS (sounddevice playback), with sequential safety
   guarantees to ensure mic recording or active conversations are never interrupted.
3. Durable logging of all delivery attempts and outcomes in SQLite (reminder_logs).
"""

import ctypes
import io
import logging
import threading
import time
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

try:
    from backend.task_store import log_reminder_delivery
except ImportError:
    from task_store import log_reminder_delivery

logger = logging.getLogger("backend.notifier")

# =============================================================================
# In-Process Win32 Shell & User API Definitions (Zero Subprocess / Zero Script)
# =============================================================================

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# Win32 Constants
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_NONE = 0x00000000
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003
NIIF_LARGE_ICON = 0x00000020
NOTIFYICON_VERSION_4 = 4

WM_USER = 0x0400
PM_REMOVE = 0x0001


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


# Window Procedure callback type
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


def _def_window_proc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# Keep permanent module-level reference so Python GC never collects the callback pointer
_GLOBAL_WNDPROC = WNDPROC(_def_window_proc)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


# Configure 64-bit argument and return types
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong

user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM

user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL

user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_longlong

user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

try:
    user32.MessageBoxTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
        wintypes.WORD,
        wintypes.DWORD,
    ]
    user32.MessageBoxTimeoutW.restype = ctypes.c_int
except AttributeError:
    pass


def _post_shell_notify_icon(title: str, message: str, duration: float = 6.0) -> Dict[str, Any]:
    """
    Display a native Windows desktop balloon / Action Center toast via in-process Shell_NotifyIconW.
    Does NOT spawn any subprocess or execute PowerShell, completely avoiding antivirus WinError 225.
    """
    hinst = kernel32.GetModuleHandleW(None)
    class_name = f"JARVIS_Notify_{uuid.uuid4().hex[:8]}"

    wndclass = WNDCLASSEXW()
    wndclass.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wndclass.style = 0
    wndclass.lpfnWndProc = _GLOBAL_WNDPROC
    wndclass.cbClsExtra = 0
    wndclass.cbWndExtra = 0
    wndclass.hInstance = hinst
    wndclass.hIcon = None
    wndclass.hCursor = None
    wndclass.hbrBackground = None
    wndclass.lpszMenuName = None
    wndclass.lpszClassName = class_name
    wndclass.hIconSm = None

    atom = user32.RegisterClassExW(ctypes.byref(wndclass))
    if not atom:
        err = kernel32.GetLastError()
        return {"success": False, "error": f"RegisterClassExW failed with Win32 code {err}"}

    hwnd = user32.CreateWindowExW(
        0,
        class_name,
        "JARVIS_Notification_Window",
        0,
        0,
        0,
        0,
        0,
        None,
        None,
        hinst,
        None,
    )
    if not hwnd:
        err = kernel32.GetLastError()
        user32.UnregisterClassW(class_name, hinst)
        return {"success": False, "error": f"CreateWindowExW failed with Win32 code {err}"}

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1001
    nid.uCallbackMessage = WM_USER + 20
    nid.szTip = "J.A.R.V.I.S. Reminder"[:127]
    nid.szInfo = message[:255]
    nid.szInfoTitle = title[:63]
    nid.dwInfoFlags = NIIF_INFO | NIIF_LARGE_ICON

    # Load standard Windows system information icon (IDI_INFORMATION = 32516)
    h_icon = user32.LoadIconW(None, ctypes.cast(32516, wintypes.LPCWSTR))
    if h_icon:
        nid.hIcon = h_icon
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
    else:
        nid.uFlags = NIF_MESSAGE | NIF_TIP | NIF_INFO

    res = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    if not res:
        err = kernel32.GetLastError()
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(class_name, hinst)
        return {"success": False, "error": f"Shell_NotifyIconW(NIM_ADD) returned 0 with Win32 code {err}"}

    # Set modern notification version (NOTIFYICON_VERSION_4) so Windows routes to Action Center/Toast
    try:
        nid.uTimeoutOrVersion = NOTIFYICON_VERSION_4
        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
    except Exception:
        pass

    # Start message pump in daemon thread to dispatch and safely clean up
    def _pump_messages_and_cleanup():
        msg = wintypes.MSG()
        start_t = time.time()
        while time.time() - start_t < duration:
            while user32.PeekMessageW(ctypes.byref(msg), hwnd, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.05)
        # Clean up
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass
        try:
            user32.DestroyWindow(hwnd)
        except Exception:
            pass
        try:
            user32.UnregisterClassW(class_name, hinst)
        except Exception:
            pass

    cleanup_thread = threading.Thread(
        target=_pump_messages_and_cleanup,
        name=f"JarvisTrayCleanup_{uuid.uuid4().hex[:6]}",
        daemon=True,
    )
    cleanup_thread.start()

    return {
        "success": True,
        "method": "win32_shell_notify_icon",
        "delivery_channel": "in-process Win32 API (shell32.dll:Shell_NotifyIconW)",
        "details": "Native Windows Action Center notification / balloon tip dispatched via Shell_NotifyIconW without subprocess.",
    }


def _show_fallback_message_box(title: str, message: str, timeout_seconds: int = 5) -> Dict[str, Any]:
    """
    Fallback: Display an auto-closing Windows information alert using user32.MessageBoxTimeoutW.
    """
    flags = 0x00000040 | 0x00040000 | 0x00010000  # MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
    try:
        def _mb_worker():
            try:
                user32.MessageBoxTimeoutW(0, message, title, flags, 0, timeout_seconds * 1000)
            except Exception as exc:
                logger.debug("MessageBoxTimeout worker error: %s", exc)

        t = threading.Thread(target=_mb_worker, daemon=True, name="JarvisMsgBoxNotify")
        t.start()
        return {
            "success": True,
            "method": "win32_message_box_timeout",
            "delivery_channel": "in-process Win32 API (user32.dll:MessageBoxTimeoutW)",
            "details": f"Topmost notification dialog displayed (auto-dismisses in {timeout_seconds}s).",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# =============================================================================
# Public Notification Settings & Service
# =============================================================================

_settings_lock = threading.Lock()
_notification_settings = {
    "windows_notifications_enabled": True,
    "spoken_reminders_enabled": True,
}


def get_notification_settings() -> Dict[str, bool]:
    """Retrieve current notification settings."""
    with _settings_lock:
        return dict(_notification_settings)


def update_notification_settings(updates: Dict[str, bool]) -> Dict[str, bool]:
    """Update notification settings in-memory."""
    with _settings_lock:
        for k in ("windows_notifications_enabled", "spoken_reminders_enabled"):
            if k in updates and isinstance(updates[k], bool):
                _notification_settings[k] = updates[k]
        return dict(_notification_settings)


def send_windows_notification_diagnostic(title: str, message: str) -> Dict[str, Any]:
    """
    Deliver a Windows desktop notification using in-process Win32 APIs and return rich diagnostics.
    Does NOT spawn any subprocess or execute PowerShell, eliminating antivirus blocks (WinError 225).
    """
    clean_title = "".join(c for c in (title or "J.A.R.V.I.S. Reminder") if c >= " " or c in "\t\n\r").strip()
    clean_msg = "".join(c for c in (message or "") if c >= " " or c in "\t\n\r").strip()

    errors = []

    # 1. Primary: In-process Win32 Shell_NotifyIconW
    try:
        res = _post_shell_notify_icon(clean_title, clean_msg, duration=6.0)
        if res.get("success"):
            logger.info("Windows desktop notification displayed via in-process Shell_NotifyIconW: %s", clean_title)
            return {
                "success": True,
                "method": res["method"],
                "delivery_channel": res["delivery_channel"],
                "title": clean_title,
                "message": clean_msg,
                "errors": [],
                "details": res["details"],
            }
        else:
            errors.append(res.get("error", "Shell_NotifyIconW failed"))
    except Exception as exc:
        errors.append(f"Shell_NotifyIconW exception: {exc}")

    # 2. Secondary fallback: In-process MessageBoxTimeoutW
    try:
        mb_res = _show_fallback_message_box(clean_title, clean_msg, timeout_seconds=5)
        if mb_res.get("success"):
            logger.info("Windows notification displayed via MessageBoxTimeoutW fallback: %s", clean_title)
            return {
                "success": True,
                "method": mb_res["method"],
                "delivery_channel": mb_res["delivery_channel"],
                "title": clean_title,
                "message": clean_msg,
                "errors": errors,
                "details": mb_res["details"],
            }
        else:
            errors.append(mb_res.get("error", "MessageBoxTimeoutW failed"))
    except Exception as exc:
        errors.append(f"MessageBoxTimeoutW exception: {exc}")

    logger.warning("Windows desktop notification failed: %s", errors)
    return {
        "success": False,
        "method": "none",
        "delivery_channel": "none",
        "title": clean_title,
        "message": clean_msg,
        "errors": errors,
        "details": "All in-process Win32 notification mechanisms failed.",
    }


def send_windows_notification(title: str, message: str) -> bool:
    """
    Deliver a Windows desktop notification.
    Preserves existing bool signature for scheduler and task callers.
    """
    diag = send_windows_notification_diagnostic(title, message)
    return bool(diag.get("success", False))


def _safe_speak_reminder(text: str) -> bool:
    """
    Safely synthesize and speak a reminder text using Piper TTS and sounddevice.
    Checks VoiceAssistant state: if recording, transcribing, or speaking, defers/skips
    to prevent audio device collision or interruption of user dialogue.
    """
    try:
        from backend.voice_loop import (
            STATE_ACKNOWLEDGING,
            STATE_RECORDING,
            STATE_SPEAKING,
            STATE_THINKING,
            STATE_TRANSCRIBING,
            get_voice_assistant,
        )
        assistant = get_voice_assistant()
        state = assistant.state
        if state in (
            STATE_RECORDING,
            STATE_TRANSCRIBING,
            STATE_THINKING,
            STATE_SPEAKING,
            STATE_ACKNOWLEDGING,
        ):
            logger.info("Voice assistant is currently active (%s). Skipping spoken reminder.", state)
            return False
    except Exception as exc:
        logger.debug("Could not verify voice assistant state: %s", exc)

    # Synthesize audio bytes using existing Piper infrastructure
    try:
        from backend.tts import synthesise_to_wav
        wav_bytes = synthesise_to_wav(text)
        if not wav_bytes:
            return False

        # Read WAV data and play safely
        samplerate, audio_data = wavfile.read(io.BytesIO(wav_bytes))
        if audio_data.dtype == np.int16:
            audio_data = audio_data.astype(np.float32) / 32768.0

        sd.play(audio_data, samplerate=samplerate)
        sd.wait()
        return True
    except Exception as exc:
        logger.warning("Spoken reminder delivery failed: %s", exc)
        return False


def deliver_reminder(task: Dict[str, Any]) -> None:
    """
    Deliver a due reminder across enabled channels (Windows toast and/or spoken voice).
    Durable outcome is recorded in SQLite reminder_logs.
    """
    task_id = task["id"]
    title = task.get("title", "Reminder")
    desc = task.get("description") or ""
    scheduled_for = task.get("claimed_scheduled_for") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    settings = get_notification_settings()
    notif_enabled = settings["windows_notifications_enabled"]
    speech_enabled = settings["spoken_reminders_enabled"]

    windows_success = False
    speech_success = False
    errors = []

    # 1. Windows Notification (In-Process Win32)
    if notif_enabled:
        msg = desc if desc else "Reminder from J.A.R.V.I.S."
        diag = send_windows_notification_diagnostic(f"J.A.R.V.I.S. Reminder: {title}", msg)
        windows_success = bool(diag.get("success", False))
        if not windows_success:
            err_details = ", ".join(diag.get("errors", [])) or "Windows notification failed"
            errors.append(f"Windows notification failed ({err_details})")
    else:
        logger.info("Windows notifications disabled in settings. Skipping for task %s.", task_id)

    # 2. Spoken Announcement
    if speech_enabled:
        spoken_text = f"Pardon the interruption. You have a reminder: {title}."
        speech_success = _safe_speak_reminder(spoken_text)
        if not speech_success:
            errors.append("Spoken announcement skipped or failed")

    # Record log
    final_status = "delivered" if (windows_success or speech_success) else ("skipped" if not notif_enabled and not speech_enabled else "failed")
    error_summary = "; ".join(errors) if errors else None

    try:
        log_reminder_delivery(
            task_id=task_id,
            scheduled_for=scheduled_for,
            channel="all",
            status=final_status,
            error=error_summary,
        )
    except Exception as log_exc:
        logger.error("Failed to log reminder delivery: %s", log_exc)
