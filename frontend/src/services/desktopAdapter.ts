/**
 * Desktop Shell Adapter Boundary (Stage 4C)
 *
 * Defines a clean, typed abstraction boundary between the Web frontend and
 * the host desktop shell (Tauri, PyWebView, or Windows native wrapper).
 *
 * In standard browser environments:
 * - Operates safely with a browser fallback (in-page visibility state,
 *   focus management, and page keyboard shortcuts).
 * - Never attempts to fake OS-level global keyboard hooks or native window handles.
 * - Gracefully provides mock or in-page behavior for minimize/maximize/drag.
 *
 * In native desktop environments (Tauri / PyWebView / Native Shell):
 * - Delegates to native window controls: minimize, maximize/restore, hide, close.
 * - Supports window dragging through `startDragging()` / `data-tauri-drag-region`.
 * - Bridges genuine OS-level global Alt+J shortcuts.
 * - Ensures closing the companion window never stops the background assistant.
 */

export interface DesktopShellAPI {
  /** Whether the app is running inside a native desktop shell */
  isDesktopShell(): boolean;

  /** Show or restore the companion window */
  showWindow(): Promise<void>;

  /** Hide or minimize the companion window without stopping the assistant */
  hideWindow(): Promise<void>;

  /** Toggle companion window visibility */
  toggleWindow(): Promise<void>;

  /** Minimize the native desktop window */
  minimizeWindow(): Promise<void>;

  /** Toggle maximize/restore on the native desktop window */
  maximizeWindow(): Promise<void>;

  /** Check if the window is currently maximized */
  isMaximized(): Promise<boolean>;

  /** Close/dismiss companion window safely (hides without killing backend) */
  closeWindow(): Promise<void>;

  /** Initiate window dragging using the native OS title bar hook */
  startDragging(): Promise<void>;

  /** Check if the companion window is currently visible */
  isWindowVisible(): boolean;

  /** Subscribe to window visibility changes */
  onVisibilityChange(callback: (visible: boolean) => void): () => void;

  /** Subscribe to shortcut triggers (OS global or browser fallback) */
  onShortcut(callback: (shortcut: string) => void): () => void;
}

declare global {
  interface Window {
    __JARVIS_DESKTOP__?: {
      showWindow?: () => Promise<void>;
      hideWindow?: () => Promise<void>;
      toggleWindow?: () => Promise<void>;
      minimizeWindow?: () => Promise<void>;
      maximizeWindow?: () => Promise<void>;
      isMaximized?: () => Promise<boolean> | boolean;
      closeWindow?: () => Promise<void>;
      startDragging?: () => Promise<void>;
      isWindowVisible?: () => boolean;
      onVisibilityChange?: (cb: (visible: boolean) => void) => () => void;
      onGlobalShortcut?: (cb: (key: string) => void) => () => void;
    };
    __TAURI__?: {
      window?: {
        getCurrentWindow?: () => {
          show: () => Promise<void>;
          hide: () => Promise<void>;
          minimize: () => Promise<void>;
          toggleMaximize: () => Promise<void>;
          isMaximized: () => Promise<boolean>;
          isVisible: () => Promise<boolean>;
          setFocus: () => Promise<void>;
          startDragging: () => Promise<void>;
          close: () => Promise<void>;
        };
      };
      event?: {
        listen?: (event: string, handler: (e: any) => void) => Promise<() => void>;
      };
    };
    pywebview?: {
      api?: {
        show_window?: () => void;
        hide_window?: () => void;
        toggle_window?: () => void;
        minimize?: () => void;
        toggle_maximize?: () => void;
        is_maximized?: () => boolean;
        close_window?: () => void;
        start_dragging?: () => void;
      };
    };
  }
}

class DesktopAdapter implements DesktopShellAPI {
  private _visible: boolean = true;
  private _isMaximizedState: boolean = false;
  private _visibilityListeners: Set<(visible: boolean) => void> = new Set();
  private _shortcutListeners: Set<(shortcut: string) => void> = new Set();
  private _cleanupKeydown?: () => void;

  constructor() {
    this._initBrowserListeners();
    this._initNativeListeners();
  }

  public isDesktopShell(): boolean {
    if (typeof window === 'undefined') return false;
    return (
      typeof window.__JARVIS_DESKTOP__ !== 'undefined' ||
      typeof window.__TAURI__ !== 'undefined' ||
      typeof window.pywebview !== 'undefined'
    );
  }

  public async showWindow(): Promise<void> {
    if (window.__JARVIS_DESKTOP__?.showWindow) {
      await window.__JARVIS_DESKTOP__.showWindow();
      this._setVisibility(true);
      return;
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      await win.show();
      await win.setFocus();
      this._setVisibility(true);
      return;
    }

    if (window.pywebview?.api?.show_window) {
      window.pywebview.api.show_window();
      this._setVisibility(true);
      return;
    }

    this._setVisibility(true);
  }

  public async hideWindow(): Promise<void> {
    if (window.__JARVIS_DESKTOP__?.hideWindow) {
      await window.__JARVIS_DESKTOP__.hideWindow();
      this._setVisibility(false);
      return;
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      await win.hide();
      this._setVisibility(false);
      return;
    }

    if (window.pywebview?.api?.hide_window) {
      window.pywebview.api.hide_window();
      this._setVisibility(false);
      return;
    }

    this._setVisibility(false);
  }

  public async toggleWindow(): Promise<void> {
    if (window.__JARVIS_DESKTOP__?.toggleWindow) {
      await window.__JARVIS_DESKTOP__.toggleWindow();
      this._setVisibility(!this._visible);
      return;
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      const isVis = await win.isVisible();
      if (isVis) {
        await win.hide();
        this._setVisibility(false);
      } else {
        await win.show();
        await win.setFocus();
        this._setVisibility(true);
      }
      return;
    }

    if (window.pywebview?.api?.toggle_window) {
      window.pywebview.api.toggle_window();
      this._setVisibility(!this._visible);
      return;
    }

    this._setVisibility(!this._visible);
  }

  public async minimizeWindow(): Promise<void> {
    if (window.__JARVIS_DESKTOP__?.minimizeWindow) {
      await window.__JARVIS_DESKTOP__.minimizeWindow();
      return;
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      await win.minimize();
      return;
    }

    if (window.pywebview?.api?.minimize) {
      window.pywebview.api.minimize();
      return;
    }

    // In standard browser fallback: hide companion to mini trigger
    this.hideWindow();
  }

  public async maximizeWindow(): Promise<void> {
    if (window.__JARVIS_DESKTOP__?.maximizeWindow) {
      await window.__JARVIS_DESKTOP__.maximizeWindow();
      this._isMaximizedState = !this._isMaximizedState;
      return;
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      await win.toggleMaximize();
      this._isMaximizedState = await win.isMaximized();
      return;
    }

    if (window.pywebview?.api?.toggle_maximize) {
      window.pywebview.api.toggle_maximize();
      this._isMaximizedState = !this._isMaximizedState;
      return;
    }

    this._isMaximizedState = !this._isMaximizedState;
  }

  public async isMaximized(): Promise<boolean> {
    if (window.__JARVIS_DESKTOP__?.isMaximized) {
      return Boolean(await window.__JARVIS_DESKTOP__.isMaximized());
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      return await win.isMaximized();
    }

    if (window.pywebview?.api?.is_maximized) {
      return Boolean(window.pywebview.api.is_maximized());
    }

    return this._isMaximizedState;
  }

  public async closeWindow(): Promise<void> {
    // Closing companion window should NEVER stop background assistant.
    // It hides the window so voice interaction and background wake word continue uninterrupted.
    if (window.__JARVIS_DESKTOP__?.closeWindow) {
      await window.__JARVIS_DESKTOP__.closeWindow();
      this._setVisibility(false);
      return;
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      await win.hide();
      this._setVisibility(false);
      return;
    }

    if (window.pywebview?.api?.close_window) {
      window.pywebview.api.close_window();
      this._setVisibility(false);
      return;
    }

    this.hideWindow();
  }

  public async startDragging(): Promise<void> {
    if (window.__JARVIS_DESKTOP__?.startDragging) {
      await window.__JARVIS_DESKTOP__.startDragging();
      return;
    }

    if (window.__TAURI__?.window?.getCurrentWindow) {
      const win = window.__TAURI__.window.getCurrentWindow();
      await win.startDragging();
      return;
    }

    if (window.pywebview?.api?.start_dragging) {
      window.pywebview.api.start_dragging();
      return;
    }
  }

  public isWindowVisible(): boolean {
    if (window.__JARVIS_DESKTOP__?.isWindowVisible) {
      return window.__JARVIS_DESKTOP__.isWindowVisible();
    }
    return this._visible;
  }

  public onVisibilityChange(callback: (visible: boolean) => void): () => void {
    if (window.__JARVIS_DESKTOP__?.onVisibilityChange) {
      return window.__JARVIS_DESKTOP__.onVisibilityChange(callback);
    }
    this._visibilityListeners.add(callback);
    callback(this._visible);
    return () => {
      this._visibilityListeners.delete(callback);
    };
  }

  public onShortcut(callback: (shortcut: string) => void): () => void {
    if (window.__JARVIS_DESKTOP__?.onGlobalShortcut) {
      return window.__JARVIS_DESKTOP__.onGlobalShortcut(callback);
    }
    this._shortcutListeners.add(callback);
    return () => {
      this._shortcutListeners.delete(callback);
    };
  }

  private _setVisibility(next: boolean): void {
    if (this._visible === next) return;
    this._visible = next;
    for (const listener of this._visibilityListeners) {
      try {
        listener(next);
      } catch (err) {
        console.error('Error in visibility listener:', err);
      }
    }
  }

  private _initBrowserListeners(): void {
    if (typeof window === 'undefined') return;

    const handleKeyDown = (event: KeyboardEvent) => {
      // Ignore shortcut when active in text inputs/textareas to avoid interfering with typing
      const activeTag = document.activeElement?.tagName?.toLowerCase();
      const isInput = activeTag === 'input' || activeTag === 'textarea';

      // Alt+J or Ctrl+Space: Toggle companion window (browser fallback)
      if ((event.altKey && (event.key === 'j' || event.key === 'J')) || (event.ctrlKey && event.code === 'Space')) {
        event.preventDefault();
        this.toggleWindow();
        for (const cb of this._shortcutListeners) {
          cb('TOGGLE_COMPANION');
        }
      }

      // Escape: Close companion window (if not in an active input with text)
      if (event.key === 'Escape' && this._visible && !isInput) {
        event.preventDefault();
        this.hideWindow();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    this._cleanupKeydown = () => window.removeEventListener('keydown', handleKeyDown);
  }

  private _initNativeListeners(): void {
    if (typeof window === 'undefined') return;

    // Listen to Tauri global-shortcut events if present
    if (window.__TAURI__?.event?.listen) {
      window.__TAURI__.event.listen('jarvis-toggle-shortcut', () => {
        this.toggleWindow();
        for (const cb of this._shortcutListeners) {
          cb('TOGGLE_COMPANION');
        }
      }).catch(() => {
        // Safe ignore
      });
    }
  }

  public destroy(): void {
    if (this._cleanupKeydown) {
      this._cleanupKeydown();
    }
    this._visibilityListeners.clear();
    this._shortcutListeners.clear();
  }
}

export const desktopAdapter: DesktopShellAPI = new DesktopAdapter();
