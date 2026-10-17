import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import type {
  CoreState,
  ChatMessage,
  UserSettings,
  SystemTelemetry as SystemTelemetryType,
  Conversation,
  ConversationMessage,
} from './types';
import { sendUserPrompt, checkBackendHealth, requestSpeech, INITIAL_MESSAGES } from './services/agentService';
import {
  listConversations,
  createConversation,
  getConversationMessages,
  renameConversation,
  deleteConversation,
  setActiveConversation,
  sendChatMessage,
} from './services/apiClient';
import { Header } from './components/Header';
import { AiCore } from './components/AiCore';
import { StateSelector } from './components/StateSelector';
import { Greeting } from './components/Greeting';
import { ConversationFeed } from './components/ConversationFeed';
import { CommandComposer } from './components/CommandComposer';
import { SystemTelemetry } from './components/SystemTelemetry';
import { QuickActions } from './components/QuickActions';
import { SettingsModal } from './components/SettingsModal';
import { JarvisCompanion } from './components/JarvisCompanion';
import { TasksDashboard } from './components/TasksDashboard';
import { desktopAdapter } from './services/desktopAdapter';
import './App.css';

const DEFAULT_SETTINGS: UserSettings = {
  model: 'Qwen3 8B',
  voice: 'en_US-lessac-medium',
  animationIntensity: 'cinematic',
  accentColor: 'cyan',
  audioChimes: true,
  enableTts: false,
  useFastApiBackend: true,
  fastApiUrl: 'http://localhost:8000',
};

function App() {
  // Navigation View: 'library' (ChatGPT-style Conversation Archive) vs 'cockpit' (Full HUD Deck) vs 'tasks' (Tasks & Reminders)
  const [activeView, setActiveView] = useState<'library' | 'cockpit' | 'tasks'>('library');

  // Core & Modal states
  const [coreState, setCoreState] = useState<CoreState>('idle');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isCompanionVisible, setIsCompanionVisible] = useState(true);

  // Settings with LocalStorage persistence
  const [settings, setSettings] = useState<UserSettings>(() => {
    try {
      const saved = localStorage.getItem('jarvis_settings');
      if (saved) return { ...DEFAULT_SETTINGS, ...JSON.parse(saved) };
    } catch {
      // Fallback
    }
    return DEFAULT_SETTINGS;
  });

  // Backend connection status
  const [isBackendOnline, setIsBackendOnline] = useState<boolean>(true);
  const [backendError, setBackendError] = useState<string | null>(null);

  // =========================================================================
  // Persistent Conversation State (Stage 4B)
  // =========================================================================
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [conversationMessages, setConversationMessages] = useState<ConversationMessage[]>([]);
  const [isLoadingConversations, setIsLoadingConversations] = useState<boolean>(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Inline rename state
  const [editingConvId, setEditingConvId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState<string>('');

  // Fallback text input state in the main panel
  const [fallbackInput, setFallbackInput] = useState<string>('');
  const [isSendingFallback, setIsSendingFallback] = useState<boolean>(false);

  // Legacy Cockpit conversation feed state
  const [cockpitMessages, setCockpitMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [isThinking, setIsThinking] = useState(false);

  // System Telemetry Metrics
  const [telemetry, setTelemetry] = useState<SystemTelemetryType>({
    modelName: settings.model,
    modelContext: 'Ollama • 32k Context',
    whisperEngine: 'Whisper.cpp',
    ttsEngine: 'Piper TTS',
    memoryDb: 'memory.db',
    activeMemoriesCount: 14,
    status: 'nominal',
    coreTemperature: '38°C',
    localLatency: '120ms',
  });

  // Audio Playback Refs
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioBlobUrlRef = useRef<string | null>(null);
  const playbackIdRef = useRef(0);
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);
  const messagesScrollRef = useRef<HTMLDivElement>(null);

  // Synchronize companion visibility with desktop adapter
  useEffect(() => {
    const unsubscribe = desktopAdapter.onVisibilityChange((visible) => {
      setIsCompanionVisible(visible);
    });
    return () => unsubscribe();
  }, []);

  // Stop any currently-playing audio and clean up its resources.
  const stopAudio = useCallback(() => {
    playbackIdRef.current += 1;
    setAutoplayBlocked(false);
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    if (audioBlobUrlRef.current) {
      URL.revokeObjectURL(audioBlobUrlRef.current);
      audioBlobUrlRef.current = null;
    }
    setCoreState((current) => (current === 'speaking' ? 'idle' : current));
  }, []);

  useEffect(() => {
    return () => {
      stopAudio();
    };
  }, [stopAudio]);

  const handleUpdateSettings = (updated: Partial<UserSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...updated };
      localStorage.setItem('jarvis_settings', JSON.stringify(next));
      return next;
    });
  };

  // Auto-scroll messages stream to bottom
  useEffect(() => {
    if (messagesScrollRef.current) {
      messagesScrollRef.current.scrollTop = messagesScrollRef.current.scrollHeight;
    }
  }, [conversationMessages, isSendingFallback]);

  // Load conversations from backend
  const refreshConversations = useCallback(
    async (preferSelectId?: string) => {
      if (!settings.useFastApiBackend) return;
      try {
        const res = await listConversations(settings.fastApiUrl);
        setIsBackendOnline(true);
        setBackendError(null);
        setConversations(res.conversations || []);
        setActiveConversationId(res.active_conversation_id);

        // Determine which conversation to select
        setSelectedConversationId((currentSelected) => {
          if (preferSelectId) return preferSelectId;
          if (currentSelected && res.conversations.some((c) => c.id === currentSelected)) {
            return currentSelected;
          }
          if (res.active_conversation_id) return res.active_conversation_id;
          if (res.conversations.length > 0) return res.conversations[0].id;
          return null;
        });
      } catch (err: unknown) {
        setIsBackendOnline(false);
        const msg = err instanceof Error ? err.message : 'Unable to connect to local backend.';
        setBackendError(msg);
      }
    },
    [settings.useFastApiBackend, settings.fastApiUrl]
  );

  // Load messages for a given conversation
  const loadMessages = useCallback(
    async (convId: string) => {
      if (!convId || !settings.useFastApiBackend) return;
      try {
        setIsLoadingMessages(true);
        const res = await getConversationMessages(convId, settings.fastApiUrl);
        setConversationMessages(res.messages || []);
      } catch (err: unknown) {
        console.error('Failed to load conversation messages:', err);
      } finally {
        setIsLoadingMessages(false);
      }
    },
    [settings.useFastApiBackend, settings.fastApiUrl]
  );

  // Initial load
  useEffect(() => {
    setIsLoadingConversations(true);
    refreshConversations().finally(() => setIsLoadingConversations(false));
  }, [refreshConversations]);

  // When selected conversation changes, load its messages
  useEffect(() => {
    if (selectedConversationId) {
      loadMessages(selectedConversationId);
    } else {
      setConversationMessages([]);
    }
  }, [selectedConversationId, loadMessages]);

  // Periodic poll to keep conversations and active messages synchronized
  useEffect(() => {
    if (!settings.useFastApiBackend) return;
    const interval = setInterval(() => {
      refreshConversations();
      if (selectedConversationId) {
        getConversationMessages(selectedConversationId, settings.fastApiUrl)
          .then((res) => {
            setConversationMessages((prev) => {
              if (JSON.stringify(prev) !== JSON.stringify(res.messages)) {
                return res.messages || [];
              }
              return prev;
            });
          })
          .catch(() => {});
      }
    }, 2500);

    return () => clearInterval(interval);
  }, [settings.useFastApiBackend, settings.fastApiUrl, selectedConversationId, refreshConversations]);

  // Telemetry check
  useEffect(() => {
    if (!settings.useFastApiBackend) return;
    checkBackendHealth(settings.fastApiUrl)
      .then((health) => {
        setIsBackendOnline(true);
        setTelemetry((prev) => ({
          ...prev,
          modelName: health.active_model || settings.model,
          status: health.status === 'ok' ? 'nominal' : 'optimizing',
        }));
      })
      .catch(() => {
        setIsBackendOnline(false);
        setTelemetry((prev) => ({ ...prev, status: 'offline' }));
      });
  }, [settings.useFastApiBackend, settings.fastApiUrl, settings.model]);

  // Filter conversations by search query
  const filteredConversations = useMemo(() => {
    if (!searchQuery.trim()) return conversations;
    const query = searchQuery.toLowerCase();
    return conversations.filter((c) => c.title.toLowerCase().includes(query));
  }, [conversations, searchQuery]);

  // Active selected conversation object
  const currentConversation = useMemo(() => {
    return conversations.find((c) => c.id === selectedConversationId) || null;
  }, [conversations, selectedConversationId]);

  // Actions: New Chat
  const handleCreateNewChat = async () => {
    try {
      const res = await createConversation('New Chat', settings.fastApiUrl);
      await refreshConversations(res.conversation.id);
      setSelectedConversationId(res.conversation.id);
      setActiveConversationId(res.conversation.id);
      setConversationMessages([]);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to create new conversation.');
    }
  };

  // Actions: Set Active Conversation
  const handleSetActive = async (convId: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    try {
      await setActiveConversation(convId, settings.fastApiUrl);
      setActiveConversationId(convId);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to set active conversation.');
    }
  };

  // Actions: Rename
  const handleStartRename = (conv: Conversation, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingConvId(conv.id);
    setEditingTitle(conv.title);
  };

  const handleConfirmRename = async (convId: string, e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!editingTitle.trim()) return;
    try {
      await renameConversation(convId, editingTitle.trim(), settings.fastApiUrl);
      setEditingConvId(null);
      await refreshConversations();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to rename conversation.');
    }
  };

  const handleCancelRename = (e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setEditingConvId(null);
  };

  // Actions: Delete
  const handleDeleteConversation = async (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm('Delete this conversation and all its messages?')) return;
    try {
      const res = await deleteConversation(convId, settings.fastApiUrl);
      await refreshConversations(res.active_conversation_id);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to delete conversation.');
    }
  };

  // Send typed fallback message in the main library panel
  const handleSendFallback = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const trimmed = fallbackInput.trim();
    if (!trimmed || isSendingFallback) return;

    const targetConvId = selectedConversationId || activeConversationId;
    setFallbackInput('');
    setIsSendingFallback(true);

    try {
      const res = await sendChatMessage(trimmed, targetConvId || undefined, settings.fastApiUrl);
      const updatedConvId = res.conversationId || targetConvId;
      if (updatedConvId) {
        await refreshConversations(updatedConvId);
        await loadMessages(updatedConvId);
      }
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to send message.');
    } finally {
      setIsSendingFallback(false);
    }
  };

  // Legacy Cockpit Message Handler
  const handleCockpitSendMessage = async (text: string) => {
    if (!text.trim() || isThinking) return;
    stopAudio();

    const userMessage: ChatMessage = {
      id: `usr-${Date.now()}`,
      sender: 'user',
      text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      status: 'complete',
    };

    setCockpitMessages((prev) => [...prev, userMessage]);
    setIsThinking(true);
    setCoreState('thinking');

    try {
      const response = await sendUserPrompt(text, settings.useFastApiBackend, settings.fastApiUrl);
      const assistantMessage: ChatMessage = {
        id: `asst-${Date.now()}`,
        sender: 'assistant',
        text: response.reply,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        latencyMs: response.latencyMs,
        tokens: response.tokens,
        toolCall: response.toolCall,
        status: 'complete',
      };

      setCockpitMessages((prev) => [...prev, assistantMessage]);
      setIsThinking(false);
      setCoreState('idle');

      if (settings.useFastApiBackend && settings.enableTts) {
        requestSpeech(response.reply, settings.fastApiUrl).catch(() => {});
      }
    } catch (err: unknown) {
      setIsThinking(false);
      setCoreState('idle');
      const errorText = err instanceof Error ? err.message : 'Error processing neural stream.';
      setCockpitMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          sender: 'assistant',
          text: errorText,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          status: 'error',
        },
      ]);
    }
  };

  // Format message timestamps
  const formatTime = (isoString?: string) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  const formatDate = (isoString?: string) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    } catch {
      return '';
    }
  };

  return (
    <div
      className={`jarvis-container ${settings.animationIntensity === 'reduced' ? 'reduced-motion' : ''}`}
      data-accent={settings.accentColor}
    >
      {/* Background Holographic Scanlines */}
      <div className="jarvis-scanlines" aria-hidden="true" />

      {/* Futuristic System Header */}
      <Header
        modelName={settings.model}
        status={telemetry.status}
        onOpenSettings={() => setIsSettingsOpen(true)}
        isCompanionOpen={isCompanionVisible}
        onToggleCompanion={() => desktopAdapter.toggleWindow()}
      />

      {/* View Mode Switcher Sub-Header Bar */}
      <nav className="view-mode-bar" aria-label="Main Navigation">
        <div className="view-mode-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeView === 'library'}
            className={`view-mode-tab ${activeView === 'library' ? 'active' : ''}`}
            onClick={() => setActiveView('library')}
          >
            💬 Conversation Library
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === 'cockpit'}
            className={`view-mode-tab ${activeView === 'cockpit' ? 'active' : ''}`}
            onClick={() => setActiveView('cockpit')}
          >
            ⚡ System Cockpit
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === 'tasks'}
            className={`view-mode-tab ${activeView === 'tasks' ? 'active' : ''}`}
            onClick={() => setActiveView('tasks')}
          >
            📋 Tasks & Reminders
          </button>
        </div>

        <div className="view-mode-meta">
          {activeConversationId && (
            <div className="active-voice-pill" title="Current conversation bound to Alt+J voice assistant">
              <span className="dot" />
              <span>Voice Active</span>
            </div>
          )}
          <span>{telemetry.modelName}</span>
        </div>
      </nav>

      {/* View 1: ChatGPT-style Conversation Library (Stage 4B) */}
      {activeView === 'library' && (
        <main className="jarvis-library-layout">
          {/* Left Sidebar */}
          <aside className="library-sidebar" aria-label="Conversations Sidebar">
            <div className="sidebar-action-header">
              <button
                type="button"
                className="btn-new-chat"
                onClick={handleCreateNewChat}
                title="Create a new persistent conversation"
              >
                + New Chat
              </button>

              <div className="sidebar-search-box">
                <span className="search-icon">🔍</span>
                <input
                  type="text"
                  className="sidebar-search-input"
                  placeholder="Search conversations..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  aria-label="Search conversations"
                />
              </div>
            </div>

            {/* Offline warning if backend unreachable */}
            {!isBackendOnline && (
              <div className="library-offline-banner">
                <span>⚠ Backend Offline</span>
                <button
                  type="button"
                  style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '11px' }}
                  onClick={() => refreshConversations()}
                >
                  Retry
                </button>
              </div>
            )}

            {/* Scrollable Conversation List */}
            <div className="sidebar-conversation-list" role="list">
              {isLoadingConversations ? (
                <div style={{ textAlign: 'center', padding: '20px', color: '#64748b', fontSize: '12px' }}>
                  Loading conversations...
                </div>
              ) : filteredConversations.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '24px 10px', color: '#64748b', fontSize: '12px' }}>
                  {searchQuery ? 'No matching conversations.' : 'No conversations yet. Click "+ New Chat" to begin.'}
                </div>
              ) : (
                filteredConversations.map((conv) => {
                  const isSelected = conv.id === selectedConversationId;
                  const isActive = conv.id === activeConversationId;
                  const isEditing = conv.id === editingConvId;

                  return (
                    <div
                      key={conv.id}
                      role="listitem"
                      tabIndex={0}
                      className={`conversation-card ${isSelected ? 'selected' : ''}`}
                      onClick={() => setSelectedConversationId(conv.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          setSelectedConversationId(conv.id);
                        }
                      }}
                    >
                      {isEditing ? (
                        <form
                          className="inline-rename-form"
                          onSubmit={(e) => handleConfirmRename(conv.id, e)}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="text"
                            className="inline-rename-input"
                            value={editingTitle}
                            onChange={(e) => setEditingTitle(e.target.value)}
                            autoFocus
                            maxLength={80}
                          />
                          <button type="submit" className="rename-btn-confirm" title="Save">
                            ✓
                          </button>
                          <button
                            type="button"
                            className="rename-btn-cancel"
                            onClick={handleCancelRename}
                            title="Cancel"
                          >
                            ✕
                          </button>
                        </form>
                      ) : (
                        <>
                          <div className="conversation-card-body">
                            <div className="conversation-title-row">
                              <span className="conversation-title" title={conv.title}>
                                {conv.title}
                              </span>
                              {isActive && <span className="badge-active-tag">VOICE</span>}
                            </div>
                            <span className="conversation-date">{formatDate(conv.updated_at)}</span>
                          </div>

                          <div className="conversation-card-actions">
                            <button
                              type="button"
                              className="card-action-btn"
                              onClick={(e) => handleStartRename(conv, e)}
                              title="Rename conversation"
                              aria-label="Rename conversation"
                            >
                              ✏
                            </button>
                            <button
                              type="button"
                              className="card-action-btn btn-delete"
                              onClick={(e) => handleDeleteConversation(conv.id, e)}
                              title="Delete conversation"
                              aria-label="Delete conversation"
                            >
                              🗑
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </aside>

          {/* Main Panel: Chronological Message Feed & Details */}
          <section className="library-main-panel" aria-label="Conversation Messages">
            {currentConversation ? (
              <>
                {/* Panel Header */}
                <header className="library-panel-header">
                  <div className="panel-header-title-area">
                    <h1 className="panel-conv-title">{currentConversation.title}</h1>
                    {currentConversation.id === activeConversationId ? (
                      <span className="badge-active-tag">VOICE TARGET</span>
                    ) : (
                      <button
                        type="button"
                        className="btn-make-active"
                        onClick={(e) => handleSetActive(currentConversation.id, e)}
                        title="Set this conversation as the voice assistant's target"
                      >
                        Set as Voice Target
                      </button>
                    )}
                  </div>

                  <div className="panel-header-actions">
                    <span style={{ fontSize: '11px', color: '#64748b', fontFamily: 'monospace' }}>
                      Created {formatDate(currentConversation.created_at)}
                    </span>
                  </div>
                </header>

                {/* Messages Stream */}
                <div className="library-messages-stream" ref={messagesScrollRef}>
                  {isLoadingMessages ? (
                    <div style={{ textAlign: 'center', padding: '40px', color: '#64748b' }}>
                      Loading messages...
                    </div>
                  ) : conversationMessages.length === 0 ? (
                    <div className="library-empty-state">
                      <div className="empty-state-icon">🎙</div>
                      <h2 className="empty-state-title">No messages in this conversation</h2>
                      <p className="empty-state-desc">
                        Say <strong style={{ color: '#00f0ff' }}>"Hey Jarvis"</strong> or press{' '}
                        <strong style={{ color: '#00f0ff' }}>Alt + J</strong> to speak your command, or type below.
                      </p>
                    </div>
                  ) : (
                    conversationMessages.map((msg) => (
                      <div
                        key={msg.id}
                        className={`message-row ${msg.role === 'user' ? 'row-user' : 'row-assistant'}`}
                      >
                        <div
                          className={`stream-bubble ${
                            msg.role === 'user' ? 'bubble-user' : 'bubble-assistant'
                          }`}
                        >
                          <div className="bubble-meta-header">
                            <span className="bubble-author">
                              {msg.role === 'user' ? 'USER' : 'J.A.R.V.I.S.'}
                            </span>
                            <span className="bubble-time">{formatTime(msg.timestamp)}</span>
                          </div>
                          <div className="bubble-body">{msg.content}</div>
                          {msg.metadata?.toolCall && (
                            <div className="bubble-tool-call">
                              <span className="tool-tag">TOOL</span>
                              <span className="tool-name">{msg.metadata.toolCall.name}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    ))
                  )}

                  {isSendingFallback && (
                    <div className="message-row row-assistant">
                      <div className="stream-bubble bubble-assistant" style={{ opacity: 0.8 }}>
                        <span style={{ fontSize: '12px', color: '#38bdf8' }}>
                          ⚡ Neural processing response...
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                {/* Fallback Composer Bar */}
                <div className="library-composer-bar">
                  <div className="composer-hint">
                    <span>Microphone & voice companion (Alt+J) are the primary controls.</span>
                    <span>Optional manual input</span>
                  </div>
                  <form className="composer-input-row" onSubmit={handleSendFallback}>
                    <input
                      type="text"
                      className="library-text-input"
                      placeholder="Type a message to Jarvis (or speak with Alt+J)..."
                      value={fallbackInput}
                      onChange={(e) => setFallbackInput(e.target.value)}
                      disabled={isSendingFallback}
                    />
                    <button
                      type="submit"
                      className="library-send-btn"
                      disabled={!fallbackInput.trim() || isSendingFallback}
                    >
                      {isSendingFallback ? 'Sending...' : 'Send'}
                    </button>
                  </form>
                </div>
              </>
            ) : (
              <div className="library-empty-state">
                <div className="empty-state-icon">💬</div>
                <h2 className="empty-state-title">Select a Conversation</h2>
                <p className="empty-state-desc">
                  Choose a conversation from the sidebar or click "+ New Chat" to start an interaction.
                </p>
                <button type="button" className="btn-new-chat" style={{ maxWidth: '200px' }} onClick={handleCreateNewChat}>
                  + New Chat
                </button>
              </div>
            )}
          </section>
        </main>
      )}

      {/* View 2: System Cockpit View (Reorganized Existing Deck) */}
      {activeView === 'cockpit' && (
        <main className="jarvis-main-layout">
          {/* Left Column: Intelligence & Control Deck */}
          <section className="hud-glass-panel hud-corner-brackets jarvis-intelligence-deck">
            <div className="jarvis-core-wrapper">
              <AiCore state={coreState} onCycleState={() => {}} />
            </div>
            <StateSelector currentState={coreState} onSelectState={setCoreState} />
            <Greeting userName="RISHI" coreState={coreState} />
            <QuickActions onTriggerAction={handleCockpitSendMessage} disabled={isThinking} />
          </section>

          {/* Right Column: Tactical Communication & Telemetry Deck */}
          <section className="jarvis-tactical-deck">
            <SystemTelemetry telemetry={telemetry} />
            <div className="hud-glass-panel hud-corner-brackets jarvis-chat-deck">
              <ConversationFeed
                messages={cockpitMessages}
                isThinking={isThinking}
                onSelectPrompt={handleCockpitSendMessage}
              />
              <CommandComposer
                onSendMessage={handleCockpitSendMessage}
                disabled={isThinking}
                onRecordingStart={() => setCoreState('listening')}
                onTranscribing={() => setCoreState('transcribing')}
                onRecordingEnd={() => setCoreState('thinking')}
                backendEnabled={settings.useFastApiBackend}
                backendUrl={settings.fastApiUrl}
              />
            </div>
          </section>
        </main>
      )}

      {/* View 3: Tasks & Reminders Management Dashboard (Stage 5) */}
      {activeView === 'tasks' && (
        <TasksDashboard fastApiUrl={settings.fastApiUrl} />
      )}

      {/* Settings Modal Drawer */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        settings={settings}
        onUpdateSettings={handleUpdateSettings}
      />

      {/* J.A.R.V.I.S. Floating Companion (Stage 4A & 4B) */}
      <JarvisCompanion
        backendUrl={settings.fastApiUrl}
        defaultVisible={isCompanionVisible}
        onOpenLibrary={() => setActiveView('library')}
        onOpenSettings={() => setIsSettingsOpen(true)}
      />
    </div>
  );
}

export default App;
