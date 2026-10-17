"""
desktop_companion.py
--------------------
Native Windows Desktop Companion Runner for JARVIS Local Voice Agent.

Provides a lightweight native Windows floating desktop companion shell:
- Registers a genuine system-wide Alt+J global shortcut using Windows User32 APIs (RegisterHotKey).
- Displays a floating companion window above other applications (topmost).
- Dedicated draggable title bar with minimize, maximize/restore, and close buttons.
- Pressing Alt+J shows and focuses the companion; if visible and focused, hides it without interrupting recording.
- Closing the window hides it without terminating the background voice assistant or FastAPI backend.
- Power button stops the wake listener and releases the microphone via FastAPI API.
- Fully local-first, zero cloud dependencies.

Usage:
    python desktop_companion.py [--url http://localhost:5173] [--backend http://localhost:8000]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("desktop_companion")

# Windows Constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_J = 0x4A
HOTKEY_ID_ALT_J = 1001
WM_HOTKEY = 0x0312
WM_CLOSE = 0x0010

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class CompanionDesktopBridge:
    """JS-to-Python bridge exposed to the webview window via window.pywebview.api or __JARVIS_DESKTOP__."""

    def __init__(self, runner: "DesktopCompanionRunner") -> None:
        self._runner = runner

    def show_window(self) -> None:
        self._runner.show_window()

    def hide_window(self) -> None:
        self._runner.hide_window()

    def toggle_window(self) -> None:
        self._runner.toggle_window()

    def minimize(self) -> None:
        self._runner.minimize()

    def toggle_maximize(self) -> None:
        self._runner.toggle_maximize()

    def is_maximized(self) -> bool:
        return self._runner.is_maximized()

    def close_window(self) -> None:
        # Hide instead of exit so the background voice assistant keeps running
        self._runner.hide_window()

    def start_dragging(self) -> None:
        self._runner.start_dragging()


class DesktopCompanionRunner:
    """Manages the native Windows window and OS global hotkey registration."""

    def __init__(
        self,
        frontend_url: str = "http://localhost:5173",
        backend_url: str = "http://localhost:8000",
    ) -> None:
        self.frontend_url = frontend_url
        self.backend_url = backend_url
        self.window = None
        self._visible = True
        self._maximized = False
        self._hotkey_thread: Optional[threading.Thread] = None
        self._stop_hotkey = threading.Event()
        self._lock = threading.Lock()

    def start_hotkey_listener(self) -> None:
        """Register OS-level Alt+J hotkey in a dedicated background thread with message loop."""

        def _hotkey_worker() -> None:
            # Register Alt+J (MOD_ALT | MOD_NOREPEAT, 'J')
            thread_id = kernel32.GetCurrentThreadId()
            res = user32.RegisterHotKey(
                None, HOTKEY_ID_ALT_J, MOD_ALT | MOD_NOREPEAT, VK_J
            )
            if not res:
                # If MOD_NOREPEAT not supported on older versions, retry without it
                res = user32.RegisterHotKey(None, HOTKEY_ID_ALT_J, MOD_ALT, VK_J)

            if res:
                logger.info("Registered genuine system-wide Alt+J shortcut.")
            else:
                logger.warning(
                    "Could not register system-wide Alt+J shortcut (code: %s).",
                    kernel32.GetLastError(),
                )
                return

            msg = wintypes.MSG()
            try:
                while not self._stop_hotkey.is_set():
                    # Check for messages with timeout via PeekMessage / GetMessage
                    b_ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if b_ret in (0, -1):
                        break

                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_ALT_J:
                        logger.info("System-wide Alt+J triggered.")
                        self._on_hotkey_pressed()

                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                user32.UnregisterHotKey(None, HOTKEY_ID_ALT_J)
                logger.info("Unregistered system-wide Alt+J shortcut.")

        self._hotkey_thread = threading.Thread(
            target=_hotkey_worker, name="jarvis-global-hotkey", daemon=True
        )
        self._hotkey_thread.start()

    def _on_hotkey_pressed(self) -> None:
        """Handle Alt+J: if visible and active, hide it; otherwise show and focus."""
        if not self.window:
            return

        try:
            # Query backend assistant status to avoid hiding while actively capturing
            status_resp = requests.get(
                f"{self.backend_url}/voice-assistant/status", timeout=0.8
            )
            if status_resp.status_code == 200:
                state = status_resp.json().get("state", "")
                if state in ("acknowledging", "recording_command") and self._visible:
                    logger.info("Recording in progress; ignoring toggle-to-hide.")
                    return
        except Exception:
            pass

        self.toggle_window()

    def show_window(self) -> None:
        if self.window:
            try:
                self.window.show()
                self._visible = True
            except Exception as err:
                logger.debug("Error showing window: %s", err)

    def hide_window(self) -> None:
        if self.window:
            try:
                self.window.hide()
                self._visible = False
            except Exception as err:
                logger.debug("Error hiding window: %s", err)

    def toggle_window(self) -> None:
        if self._visible:
            self.hide_window()
        else:
            self.show_window()

    def minimize(self) -> None:
        if self.window:
            try:
                self.window.minimize()
            except Exception as err:
                logger.debug("Error minimizing window: %s", err)

    def toggle_maximize(self) -> None:
        if self.window:
            try:
                if self._maximized:
                    self.window.restore()
                    self._maximized = False
                else:
                    self.window.maximize()
                    self._maximized = True
            except Exception as err:
                logger.debug("Error toggling maximize: %s", err)

    def is_maximized(self) -> bool:
        return self._maximized

    def start_dragging(self) -> None:
        """Trigger native window drag on Windows."""
        try:
            # Send WM_NCLBUTTONDOWN with HTCAPTION (2) to active window handle
            hwnd = user32.GetActiveWindow()
            if hwnd:
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, 0x00A1, 2, 0)
        except Exception as err:
            logger.debug("Error starting window drag: %s", err)

    def run(self) -> None:
        """Launch webview with native controls."""
        try:
            import webview
        except ImportError:
            logger.error(
                "pywebview is not installed. To run the companion desktop shell with Python, "
                "install pywebview: pip install pywebview\n"
                "Or build/run the Tauri desktop shell using 'cargo build' inside src-tauri."
            )
            return

        self.start_hotkey_listener()
        bridge = CompanionDesktopBridge(self)

        self.window = webview.create_window(
            title="J.A.R.V.I.S. Companion",
            url=self.frontend_url,
            js_api=bridge,
            width=420,
            height=680,
            min_size=(340, 460),
            on_top=True,
            frameless=True,
            easy_drag=False,  # Managed by our dedicated titlebar
            transparent=False,
            background_color="#050b18",
        )

        def on_closing():
            # Intercept close event: hide window instead of exiting
            # so that the backend assistant loop continues to operate
            self.hide_window()
            return False  # Prevent destruction

        self.window.events.closing += on_closing

        logger.info("Starting J.A.R.V.I.S. Companion Desktop Window...")
        try:
            webview.start(debug=False)
        finally:
            self._stop_hotkey.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. Native Desktop Companion")
    parser.add_argument("--url", default="http://localhost:5173", help="Frontend URL")
    parser.add_argument(
        "--backend", default="http://localhost:8000", help="Backend FastAPI URL"
    )
    args = parser.parse_args()

    runner = DesktopCompanionRunner(frontend_url=args.url, backend_url=args.backend)
    runner.run()


if __name__ == "__main__":
    main()
