import { type FC, useState, useEffect, useRef, useCallback } from 'react';
import type { VoiceAssistantState, VoiceAssistantStatus, ConversationMessage } from '../types';
import {
  getVoiceAssistantStatus,
  startVoiceAssistant,
  stopVoiceAssistant,
  cancelVoiceAssistant,
  sendChatMessage,
  createConversation,
  getConversationMessages,
  DEFAULT_BACKEND_URL,
} from '../services/apiClient';
import { desktopAdapter } from '../services/desktopAdapter';
import { CompanionReactor } from './CompanionReactor';
import './JarvisCompanion.css';

interface JarvisCompanionProps {
  backendUrl?: string;
  onOpenSettings?: () => void;
  onOpenLibrary?: () => void;
  /** Initial visibility of the floating companion */
  defaultVisible?: boolean;
}

export const JarvisCompanion: FC<JarvisCompanionProps> = ({
  backendUrl = DEFAULT_BACKEND_URL,
  onOpenLibrary,
  defaultVisible = true,
}) => {
  // Companion visibility state (synchronized with desktop adapter boundary)
  const [isVisible, setIsVisible] = useState<boolean>(defaultVisible);

  // Voice Assistant lifecycle state
  const [assistantState, setAssistantState] = useState<VoiceAssistantState>('stopped');
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isBackendOnline, setIsBackendOnline] = useState<boolean>(true);

  // Active persistent conversation tracking
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [isLoadingMessages, setIsLoadingMessages] = useState<boolean>(false);

  // Transition tracking for auto-summoning on wake word detection
  const previousStateRef = useRef<VoiceAssistantState>('stopped');
  const activeConvIdRef = useRef<string | null>(null);

  // Fallback text input state
  const [inputText, setInputText] = useState<string>('');
  const [isSending, setIsSending] = useState<boolean>(false);

  // Guard against parallel status requests
  const isPollingRef = useRef<boolean>(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);

  // Keep ref synchronized for closures
  useEffect(() => {
    activeConvIdRef.current = activeConversationId;
  }, [activeConversationId]);

  // Synchronize visibility with desktop adapter
  useEffect(() => {
    const unsubscribe = desktopAdapter.onVisibilityChange((visible) => {
      setIsVisible(visible);
    });
    return () => unsubscribe();
  }, []);

  // Fetch messages for a conversation safely
  const fetchMessages = useCallback(async (convId: string) => {
    try {
      const res = await getConversationMessages(convId, backendUrl);
      setMessages(res.messages || []);
    } catch {
      // Non-fatal if offline or fresh conversation
    }
  }, [backendUrl]);

  // Auto-scroll conversation to bottom whenever messages or status update
  useEffect(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [messages, assistantState, isSending]);

  // Poll voice-assistant status and refresh active conversation messages
  useEffect(() => {
    let isMounted = true;
    let timerId: ReturnType<typeof setTimeout> | null = null;

    const pollStatus = async () => {
      if (isPollingRef.current) return;
      isPollingRef.current = true;

      try {
        const status: VoiceAssistantStatus = await getVoiceAssistantStatus(backendUrl);
        if (!isMounted) return;

        setIsBackendOnline(true);
        setIsRunning(status.running);
        setAssistantState(status.state);
        setLastError(status.error);

        // Synchronize active conversation ID with backend
        const backendConvId = status.active_conversation_id || null;
        if (backendConvId && backendConvId !== activeConvIdRef.current) {
          setActiveConversationId(backendConvId);
          activeConvIdRef.current = backendConvId;
          fetchMessages(backendConvId);
        } else if (
          backendConvId &&
          (previousStateRef.current === 'speaking' || previousStateRef.current === 'thinking') &&
          (status.state === 'wake_listening' || status.state === 'stopped')
        ) {
          // Interaction completed: refresh persistent messages
          fetchMessages(backendConvId);
        }

        // AUTO-SUMMON BEHAVIOR:
        // When wake word detects "Hey Jarvis", backend transitions from
        // 'wake_listening' to 'acknowledging' or 'recording_command'.
        const prevState = previousStateRef.current;
        const becameActive =
          (prevState === 'wake_listening' || prevState === 'stopped') &&
          (status.state === 'acknowledging' || status.state === 'recording_command');

        if (becameActive) {
          desktopAdapter.showWindow();
        }

        previousStateRef.current = status.state;
      } catch (err) {
        if (!isMounted) return;
        setIsBackendOnline(false);
        if (err instanceof Error) {
          setLastError(err.message);
        }
      } finally {
        isPollingRef.current = false;
        if (isMounted) {
          const interval = isRunning ? 600 : 1800;
          timerId = setTimeout(pollStatus, interval);
        }
      }
    };

    pollStatus();

    return () => {
      isMounted = false;
      if (timerId) clearTimeout(timerId);
    };
  }, [backendUrl, isRunning, fetchMessages]);

  // Initial fetch of active conversation messages when component mounts
  useEffect(() => {
    if (activeConversationId) {
      fetchMessages(activeConversationId);
    }
  }, [activeConversationId, fetchMessages]);

  // Start Assistant (idempotent, calls POST /voice-assistant/start)
  const handleStartAssistant = async () => {
    try {
      setLastError(null);
      const res = await startVoiceAssistant(backendUrl);
      setAssistantState(res.state);
      setIsRunning(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to start assistant.';
      setLastError(msg);
    }
  };

  // Stop Assistant (calls POST /voice-assistant/stop)
  const handleStopAssistant = async () => {
    try {
      await stopVoiceAssistant(backendUrl);
      setAssistantState('stopped');
      setIsRunning(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to stop assistant.';
      setLastError(msg);
    }
  };

  // Cancel Command Capture (calls POST /voice-assistant/cancel)
  const handleCancelCommand = async () => {
    try {
      const res = await cancelVoiceAssistant(backendUrl);
      setAssistantState(res.state);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to cancel command.';
      setLastError(msg);
    }
  };

  // Create New Chat
  const handleNewChat = async () => {
    try {
      setIsLoadingMessages(true);
      const res = await createConversation('New Chat', backendUrl);
      setActiveConversationId(res.conversation.id);
      activeConvIdRef.current = res.conversation.id;
      setMessages([]);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create new chat.';
      setLastError(msg);
    } finally {
      setIsLoadingMessages(false);
    }
  };

  // Fallback text input send handler
  const handleSendText = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const trimmed = inputText.trim();
    if (!trimmed || isSending) return;

    setInputText('');
    setIsSending(true);
    setLastError(null);

    // Optimistically show user message
    const tempUserMsg: ConversationMessage = {
      id: `temp-usr-${Date.now()}`,
      conversation_id: activeConversationId || '',
      role: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      const res = await sendChatMessage(trimmed, activeConversationId || undefined, backendUrl);
      if (res.conversationId && res.conversationId !== activeConversationId) {
        setActiveConversationId(res.conversationId);
        activeConvIdRef.current = res.conversationId;
      }
      const targetConv = res.conversationId || activeConversationId;
      if (targetConv) {
        await fetchMessages(targetConv);
      }
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : 'Failed to receive response.';
      setLastError(errorMsg);
      const errReply: ConversationMessage = {
        id: `temp-err-${Date.now()}`,
        conversation_id: activeConversationId || '',
        role: 'assistant',
        content: `Error: ${errorMsg}`,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errReply]);
    } finally {
      setIsSending(false);
    }
  };

  // Window state in native desktop mode
  const [isMaximized, setIsMaximized] = useState<boolean>(false);

  // Close control: hide/dismiss the companion window without stopping the assistant
  const handleClose = useCallback(() => {
    desktopAdapter.closeWindow();
  }, []);

  // Minimize window control
  const handleMinimize = useCallback(() => {
    desktopAdapter.minimizeWindow();
  }, []);

  // Toggle maximize / restore control
  const handleToggleMaximize = useCallback(async () => {
    await desktopAdapter.maximizeWindow();
    const max = await desktopAdapter.isMaximized();
    setIsMaximized(max);
  }, []);

  // Window drag hook for dedicated title bar
  const handleTitleBarMouseDown = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    // Don't drag when clicking interactive buttons, inputs, or badges
    if (target.closest('button') || target.closest('input') || target.closest('a')) {
      return;
    }
    desktopAdapter.startDragging();
  }, []);

  // Global keyboard shortcuts (Escape dismiss without stopping background assistant)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Escape closes companion if not active in input with text and not actively recording
      if (e.key === 'Escape' && isVisible) {
        const isInput = ['input', 'textarea'].includes(document.activeElement?.tagName?.toLowerCase() || '');
        if (isInput && inputText.trim().length > 0) return;
        const isCapturingNow =
          assistantState === 'acknowledging' || assistantState === 'recording_command';
        if (isCapturingNow) return;

        e.preventDefault();
        desktopAdapter.hideWindow();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isVisible, assistantState, inputText]);

  // Global Alt+J shortcut trigger handler (preserves active recording)
  useEffect(() => {
    const unsubscribe = desktopAdapter.onShortcut((shortcut) => {
      if (shortcut === 'TOGGLE_COMPANION') {
        const isCapturingNow =
          assistantState === 'acknowledging' || assistantState === 'recording_command';
        if (isCapturingNow && isVisible) {
          // Do not hide while recording command
          return;
        }
        desktopAdapter.toggleWindow();
      }
    });
    return () => unsubscribe();
  }, [assistantState, isVisible]);

  // State readable labels & guidance
  const stateInfo: Record<
    VoiceAssistantState,
    { label: string; badge: string; hint: string; color: string }
  > = {
    stopped: {
      label: 'Assistant Offline',
      badge: 'STANDBY',
      hint: 'Click "Power On" to arm hands-free wake listening.',
      color: '#64748b',
    },
    wake_listening: {
      label: 'Wake Word Active',
      badge: 'LISTENING',
      hint: 'Say "Hey Jarvis" or type a command below.',
      color: 'var(--color-primary, #00f0ff)',
    },
    acknowledging: {
      label: 'Acknowledging',
      badge: 'ACK',
      hint: 'Wake detected. Preparing audio recorder...',
      color: '#38bdf8',
    },
    recording_command: {
      label: 'Listening to Command',
      badge: 'RECORDING',
      hint: 'Speak your question naturally. Silence ends recording.',
      color: '#00f0ff',
    },
    transcribing: {
      label: 'Transcribing Audio',
      badge: 'WHISPER',
      hint: 'Whisper.cpp transcribing local voice input...',
      color: '#38bdf8',
    },
    thinking: {
      label: 'Neural Processing',
      badge: 'QWEN3',
      hint: 'Local Ollama model reasoning and executing tools...',
      color: '#a855f7',
    },
    speaking: {
      label: 'Speaking Response',
      badge: 'SPEAKING',
      hint: 'Piper TTS streaming neural response...',
      color: '#00f0ff',
    },
    error: {
      label: 'System Alert',
      badge: 'ALERT',
      hint: lastError || 'An error occurred during voice interaction.',
      color: '#f59e0b',
    },
  };

  const currentState = stateInfo[assistantState] || stateInfo.stopped;
  const isCapturing = assistantState === 'acknowledging' || assistantState === 'recording_command';

  // Format message time
  const formatTime = (isoString?: string) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  // If companion is hidden in the browser, show a discreet floating trigger pill
  if (!isVisible) {
    return (
      <aside
        className="jarvis-companion-mini-trigger"
        onClick={() => desktopAdapter.showWindow()}
        title="Open J.A.R.V.I.S. Companion (Alt+J)"
        role="button"
        tabIndex={0}
        aria-label="Open J.A.R.V.I.S. Companion"
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            desktopAdapter.showWindow();
          }
        }}
      >
        <div className={`mini-orb state-${assistantState}`}>
          <CompanionReactor state={assistantState} size={42} interactive={false} />
        </div>
        <div className="mini-meta">
          <span className="mini-title">J.A.R.V.I.S.</span>
          <span className="mini-badge" style={{ color: currentState.color }}>
            {currentState.badge}
          </span>
        </div>
        <span className="mini-shortcut" title="Keyboard Shortcut">
          Alt+J
        </span>
      </aside>
    );
  }

  return (
    <section
      className={`jarvis-companion-window hud-corner-brackets ${
        desktopAdapter.isDesktopShell() ? 'is-desktop-shell' : ''
      } ${isMaximized ? 'is-maximized' : ''}`}
      role="region"
      aria-label="J.A.R.V.I.S. Floating Companion"
    >
      {/* 1. Dedicated Movable Title Bar: Drag Region, Brand, Controls, Window Actions */}
      <header
        className="companion-header companion-titlebar"
        data-tauri-drag-region
        onMouseDown={handleTitleBarMouseDown}
      >
        <div className="header-brand" data-tauri-drag-region>
          <div className="brand-dot-pulse" style={{ backgroundColor: currentState.color }} />
          <span className="brand-name">J.A.R.V.I.S.</span>
          <span className="brand-sub">COMPANION</span>
        </div>

        <div className="header-controls">
          {/* New Chat Button */}
          <button
            type="button"
            className="companion-nav-btn companion-btn-new-chat"
            onClick={handleNewChat}
            title="Start a new persistent conversation"
            aria-label="New Chat"
            disabled={isLoadingMessages}
          >
            + New
          </button>

          {/* Open Full Library Button */}
          {onOpenLibrary && (
            <button
              type="button"
              className="companion-nav-btn companion-btn-library"
              onClick={onOpenLibrary}
              title="Open ChatGPT-style Conversation Library"
              aria-label="Conversation Library"
            >
              Library
            </button>
          )}

          {/* Status Badge */}
          <span
            className="status-chip"
            style={{
              borderColor: currentState.color,
              color: currentState.color,
            }}
            title={`Status: ${currentState.label}`}
          >
            {currentState.badge}
          </span>

          {/* Connection Indicator */}
          <span
            className={`connection-dot ${isBackendOnline ? 'online' : 'offline'}`}
            title={isBackendOnline ? 'Backend Online (Localhost)' : 'Backend Offline'}
          />

          {/* Cancel Button (active when recording or acknowledging) */}
          {isCapturing && (
            <button
              type="button"
              className="companion-btn-cancel"
              onClick={handleCancelCommand}
              title="Cancel current recording"
              aria-label="Cancel recording"
            >
              Cancel
            </button>
          )}

          {/* Power On/Off Toggle (stops/restarts runtime and releases microphone) */}
          <button
            type="button"
            className={`companion-power-btn ${isRunning ? 'is-running' : 'is-stopped'}`}
            onClick={isRunning ? handleStopAssistant : handleStartAssistant}
            title={
              isRunning
                ? 'Power Off: Stops voice runtime and releases microphone'
                : 'Power On: Arms hands-free wake word listener'
            }
            aria-label={isRunning ? 'Stop Assistant' : 'Start Assistant'}
          >
            {isRunning ? 'Power Off' : 'Power On'}
          </button>

          {/* Native Window Controls Group */}
          <div className="window-controls-group" aria-label="Window Controls">
            <button
              type="button"
              className="window-control-btn btn-minimize"
              onClick={handleMinimize}
              title="Minimize window"
              aria-label="Minimize"
            >
              &minus;
            </button>
            <button
              type="button"
              className="window-control-btn btn-maximize"
              onClick={handleToggleMaximize}
              title={isMaximized ? 'Restore window size' : 'Maximize window'}
              aria-label={isMaximized ? 'Restore' : 'Maximize'}
            >
              {isMaximized ? '❐' : '□'}
            </button>
            <button
              type="button"
              className="window-control-btn btn-close"
              onClick={handleClose}
              title="Close window (Alt+J to reopen; background assistant continues)"
              aria-label="Close window"
            >
              ✕
            </button>
          </div>
        </div>
      </header>

      {/* 2. Visual Deck: Arc Reactor Centerpiece */}
      <div className="companion-reactor-deck">
        <CompanionReactor
          state={assistantState}
          size={130}
          onClick={() => {
            if (!isRunning) handleStartAssistant();
          }}
        />

        <div className="companion-reactor-meta">
          <h2 className="reactor-headline" style={{ color: currentState.color }}>
            {currentState.label}
          </h2>
          <p className="reactor-subtext">{currentState.hint}</p>
        </div>
      </div>

      {/* 3. Conversation & Interaction Deck */}
      <div className="companion-conversation-deck">
        <div className="companion-feed" ref={chatScrollRef}>
          {messages.length === 0 ? (
            <div className="companion-empty-state">
              <p className="empty-title">All local neural systems nominal.</p>
              <p className="empty-desc">
                Voice mode is {isRunning ? 'armed' : 'standby'}. Say{' '}
                <span className="wake-phrase">"Hey Jarvis"</span> to speak, or type below.
              </p>
            </div>
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                className={`companion-msg-bubble ${
                  msg.role === 'user' ? 'msg-user' : 'msg-assistant'
                }`}
              >
                <div className="msg-header">
                  <span className="msg-author">{msg.role === 'user' ? 'YOU' : 'JARVIS'}</span>
                  <span className="msg-time">{formatTime(msg.timestamp)}</span>
                </div>
                <div className="msg-content">{msg.content}</div>
                {msg.metadata?.toolCall && (
                  <div className="msg-tool-call">
                    <span className="tool-tag">TOOL</span>
                    <span className="tool-name">{msg.metadata.toolCall.name}</span>
                  </div>
                )}
              </div>
            ))
          )}

          {/* Active Generation Loading Indicator */}
          {(isSending || assistantState === 'transcribing' || assistantState === 'thinking') && (
            <div className="companion-loading-bubble">
              <div className="typing-rings">
                <span className="ring-pulse" />
                <span className="ring-pulse" />
                <span className="ring-pulse" />
              </div>
              <span className="loading-text">
                {assistantState === 'transcribing'
                  ? 'Whisper transcribing audio...'
                  : assistantState === 'thinking'
                  ? 'Qwen3 neural processing...'
                  : 'Processing command...'}
              </span>
            </div>
          )}
        </div>

        {/* Recoverable Error Banner */}
        {lastError && assistantState === 'error' && (
          <div className="companion-error-banner" role="alert">
            <span className="error-icon">⚠</span>
            <div className="error-body">
              <span className="error-title">Alert: {lastError}</span>
              <button
                type="button"
                className="error-retry-btn"
                onClick={handleStartAssistant}
              >
                Retry Connection
              </button>
            </div>
          </div>
        )}

        {/* 4. Text Input Fallback (Dedicated Bottom Layout) */}
        <form className="companion-input-form" onSubmit={handleSendText}>
          <input
            ref={inputRef}
            type="text"
            className="companion-text-input"
            placeholder={
              isRunning
                ? 'Speak "Hey Jarvis" or type here...'
                : 'Type a message to Jarvis...'
            }
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            disabled={isSending || assistantState === 'transcribing' || assistantState === 'thinking'}
            aria-label="Command input"
          />
          <button
            type="submit"
            className="companion-send-btn"
            disabled={
              !inputText.trim() ||
              isSending ||
              assistantState === 'transcribing' ||
              assistantState === 'thinking'
            }
            aria-label="Send message"
            title="Send (Enter)"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </form>
      </div>
    </section>
  );
};
