import { useState, useEffect, useRef, useCallback } from 'react';
import type { CoreState, ChatMessage, UserSettings, SystemTelemetry as SystemTelemetryType } from './types';
import { sendUserPrompt, checkBackendHealth, requestSpeech, INITIAL_MESSAGES } from './services/agentService';
import { Header } from './components/Header';
import { AiCore } from './components/AiCore';
import { StateSelector } from './components/StateSelector';
import { Greeting } from './components/Greeting';
import { ConversationFeed } from './components/ConversationFeed';
import { CommandComposer } from './components/CommandComposer';
import { SystemTelemetry } from './components/SystemTelemetry';
import { QuickActions } from './components/QuickActions';
import { SettingsModal } from './components/SettingsModal';
import './App.css';

const DEFAULT_SETTINGS: UserSettings = {
  model: 'Qwen3 8B',
  voice: 'en_US-lessac-medium',
  animationIntensity: 'cinematic',
  accentColor: 'cyan',
  audioChimes: true,
  enableTts: false,
  useFastApiBackend: false,
  fastApiUrl: 'http://localhost:8000',
};

function App() {
  // Application State
  const [coreState, setCoreState] = useState<CoreState>('idle');
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [isThinking, setIsThinking] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Ref to the currently active Audio element so we can stop it on demand.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Ref to the current blob object URL so we can revoke it after use.
  const audioBlobUrlRef = useRef<string | null>(null);
  // Monotonic playback token. Every playback claims a new token; audio
  // callbacks only act while their token is still current. This prevents a
  // stale playback callback from overwriting a newer THINKING/LISTENING state
  // (e.g. the user sent a new message while a previous reply was synthesising).
  const playbackIdRef = useRef(0);
  // True when the browser blocked autoplay and a manual control is offered.
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);

  // Settings with LocalStorage persistence
  const [settings, setSettings] = useState<UserSettings>(() => {
    try {
      const saved = localStorage.getItem('jarvis_settings');
      if (saved) return { ...DEFAULT_SETTINGS, ...JSON.parse(saved) };
    } catch {
      // Fallback to default
    }
    return DEFAULT_SETTINGS;
  });

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

  // Stop any currently-playing audio and clean up its resources.
  const stopAudio = useCallback(() => {
    // Invalidate the current playback token so any pending audio callbacks
    // (onplay/onended/onerror) become no-ops and cannot mutate UI state.
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
  }, []);

  // Ensure audio is cleaned up when the component unmounts.
  useEffect(() => {
    return () => {
      stopAudio();
    };
  }, [stopAudio]);

  // Synchronize settings changes
  const handleUpdateSettings = (updated: Partial<UserSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...updated };
      localStorage.setItem('jarvis_settings', JSON.stringify(next));
      return next;
    });
  };

  // Keep telemetry in sync with active model and backend health
  useEffect(() => {
    if (!settings.useFastApiBackend) {
      setTelemetry((prev) => ({
        ...prev,
        modelName: settings.model,
        status: 'nominal',
      }));
      return;
    }

    let isMounted = true;
    checkBackendHealth(settings.fastApiUrl)
      .then((health) => {
        if (!isMounted) return;
        setTelemetry((prev) => ({
          ...prev,
          modelName: health.ollama?.configured_model || settings.model,
          activeMemoriesCount: health.memory?.active_memories ?? prev.activeMemoriesCount,
          status: health.status === 'healthy' ? 'nominal' : health.status === 'degraded' ? 'optimizing' : 'offline',
        }));
      })
      .catch(() => {
        if (!isMounted) return;
        setTelemetry((prev) => ({
          ...prev,
          status: 'offline',
        }));
      });

    return () => {
      isMounted = false;
    };
  }, [settings.useFastApiBackend, settings.fastApiUrl, settings.model]);

  // Cycle through states on AI Core click (Interactive demo feature)
  const handleCycleState = () => {
    const states: CoreState[] = ['idle', 'listening', 'thinking', 'speaking'];
    const currentIndex = states.indexOf(coreState);
    const nextState = states[(currentIndex + 1) % states.length];
    setCoreState(nextState);
  };

  // Microphone Demo Trigger (Phase 2 Simulation)
  const handleTriggerVoiceDemo = () => {
    setCoreState('listening');
    setTimeout(() => {
      setCoreState('idle');
    }, 3500);
  };

  // Format current local time for message timestamps
  const getCurrentTimeString = () => {
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
  };

  /**
   * Play a spoken reply from the local Piper TTS backend.
   *
   * - Only called when useFastApiBackend AND enableTts are both enabled.
   * - Claims a monotonic playback token; audio callbacks only mutate UI state
   *   while that token is still current, so a stale callback can never
   *   overwrite a newer THINKING/LISTENING state set by a later message.
   * - Sets coreState to 'speaking' only when audio actually starts playing
   *   (via onplay), which also correctly advances the flow from 'thinking'.
   * - Returns coreState to 'idle' on natural end, error, or stop.
   * - On browser autoplay restriction, keeps the audio resource alive and
   *   surfaces a manual "Play" control instead of destroying it.
   * - Errors here are non-fatal: text chat is always preserved.
   */
  const playSpeechReply = useCallback(
    async (text: string) => {
      // Stop any previous playback before starting a new one. This also
      // invalidates the previous token so its callbacks become no-ops.
      stopAudio();

      // Claim a fresh playback token for this reply.
      const playbackId = playbackIdRef.current;
      const isCurrent = () => playbackIdRef.current === playbackId;

      let blobUrl: string;
      try {
        blobUrl = await requestSpeech(text, settings.fastApiUrl);
      } catch (err: unknown) {
        // A newer interaction may have started while awaiting synthesis.
        if (isCurrent()) {
          const msg = err instanceof Error ? err.message : 'TTS request failed.';
          setMessages((prev) => [
            ...prev,
            {
              id: `tts-err-${Date.now()}`,
              sender: 'assistant',
              text: `⚠️ Speech synthesis unavailable: ${msg}`,
              timestamp: getCurrentTimeString(),
              status: 'error',
            },
          ]);
          // Leave the transient THINKING state so the core doesn't hang.
          setCoreState((current) => (current === 'thinking' ? 'idle' : current));
        }
        return;
      }

      // A newer message/playback started while we were awaiting synthesis.
      // Discard this audio and its resources without touching UI state.
      if (!isCurrent()) {
        URL.revokeObjectURL(blobUrl);
        return;
      }

      audioBlobUrlRef.current = blobUrl;
      const audio = new Audio(blobUrl);
      audioRef.current = audio;

      audio.onplay = () => {
        if (!isCurrent()) return;
        setAutoplayBlocked(false);
        setCoreState('speaking');
      };

      const handleEnd = () => {
        if (!isCurrent()) return;
        // Only reset SPEAKING; never clobber a THINKING/LISTENING state.
        setCoreState((current) => (current === 'speaking' ? 'idle' : current));
        if (audioBlobUrlRef.current === blobUrl) {
          URL.revokeObjectURL(blobUrl);
          audioBlobUrlRef.current = null;
        }
        audioRef.current = null;
      };

      audio.onended = handleEnd;
      // If playback is paused (e.g. by an external control), leave SPEAKING.
      // Guarded by the token so the pause() inside stopAudio() is a no-op.
      audio.onpause = () => {
        if (!isCurrent()) return;
        setCoreState((current) => (current === 'speaking' ? 'idle' : current));
      };
      audio.onerror = () => {
        if (!isCurrent()) return;
        handleEnd();
        setMessages((prev) => [
          ...prev,
          {
            id: `tts-play-err-${Date.now()}`,
            sender: 'assistant',
            text: '⚠️ Audio playback error. The response text is shown above.',
            timestamp: getCurrentTimeString(),
            status: 'error',
          },
        ]);
      };

      try {
        await audio.play();
      } catch {
        if (!isCurrent()) return;
        // Autoplay blocked by the browser (policy restriction). Keep the
        // audio resource alive and surface a manual control rather than
        // destroying it. Text chat remains fully usable regardless.
        setCoreState((current) => (current === 'thinking' ? 'idle' : current));
        setAutoplayBlocked(true);
        setMessages((prev) => [
          ...prev,
          {
            id: `tts-autoplay-${Date.now()}`,
            sender: 'assistant',
            text: '🔇 Auto-play was blocked by your browser. Use the ▶ Play button below to hear this response.',
            timestamp: getCurrentTimeString(),
            status: 'error',
          },
        ]);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settings.fastApiUrl, stopAudio]
  );

  /**
   * Manual playback control offered when the browser blocks autoplay.
   * Triggered by a user click (a valid user gesture), so autoplay policy
   * is satisfied and the retained audio can start playing.
   */
  const handleManualPlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    // If playback succeeds, onplay sets coreState to 'speaking' and clears
    // the autoplay notice. If it still fails, the control stays available.
    audio.play().catch(() => {
      /* keep the manual control available */
    });
  }, []);


  // Submit User Message Handler
  const handleSendMessage = async (text: string) => {
    if (!text.trim() || isThinking) return;

    // Stop any currently playing speech before processing a new message.
    stopAudio();
    setCoreState((current) => (current === 'speaking' ? 'idle' : current));

    const userMessage: ChatMessage = {
      id: `usr-${Date.now()}`,
      sender: 'user',
      text,
      timestamp: getCurrentTimeString(),
      status: 'complete',
    };

    setMessages((prev) => [...prev, userMessage]);
    setIsThinking(true);
    setCoreState('thinking');

    try {
      const response = await sendUserPrompt(
        text,
        settings.useFastApiBackend,
        settings.fastApiUrl
      );

      const assistantMessage: ChatMessage = {
        id: `asst-${Date.now()}`,
        sender: 'assistant',
        text: response.reply,
        timestamp: getCurrentTimeString(),
        latencyMs: response.latencyMs,
        tokens: response.tokens,
        toolCall: response.toolCall,
        status: 'complete',
      };

      setMessages((prev) => [...prev, assistantMessage]);
      setIsThinking(false);

      // --- TTS playback path ---
      // Only speak when BOTH the live backend AND TTS are enabled.
      // Never speak demo responses or initial messages.
      if (settings.useFastApiBackend && settings.enableTts) {
        // playSpeechReply sets coreState to 'speaking' via audio.onplay.
        await playSpeechReply(response.reply);
      } else {
        // Text-only path: brief visual SPEAKING pulse, then idle.
        setCoreState('speaking');
        setTimeout(() => {
          setCoreState((current) => (current === 'speaking' ? 'idle' : current));
        }, 3200);
      }
    } catch (err: unknown) {
      setIsThinking(false);
      setCoreState('idle');
      const errorText = err instanceof Error
        ? err.message
        : 'Error processing local neural stream. Standing by for diagnostics.';
      const errorMessage: ChatMessage = {
        id: `err-${Date.now()}`,
        sender: 'assistant',
        text: errorText,
        timestamp: getCurrentTimeString(),
        status: 'error',
      };
      setMessages((prev) => [...prev, errorMessage]);
    }
  };

  return (
    <div
      className={`jarvis-container ${settings.animationIntensity === 'reduced' ? 'reduced-motion' : ''}`}
      data-accent={settings.accentColor}
    >
      {/* Background Holographic Scanline Overlay */}
      <div className="jarvis-scanlines" aria-hidden="true" />

      {/* Futuristic System Header */}
      <Header
        modelName={settings.model}
        status={telemetry.status}
        onOpenSettings={() => setIsSettingsOpen(true)}
      />

      {/* Main Experience Layout: Balanced Cockpit Architecture */}
      <main className="jarvis-main-layout">
        {/* Left Column: Intelligence & Control Deck */}
        <section className="hud-glass-panel hud-corner-brackets jarvis-intelligence-deck">
          {/* Animated AI Core Centerpiece */}
          <div className="jarvis-core-wrapper">
            <AiCore state={coreState} onCycleState={handleCycleState} />
          </div>

          {/* Interactive State Selector Pills */}
          <StateSelector currentState={coreState} onSelectState={setCoreState} />

          {/* Personal Intelligence Greeting */}
          <Greeting userName="RISHI" coreState={coreState} />

          {/* Quick Action Protocols */}
          <QuickActions
            onTriggerAction={handleSendMessage}
            disabled={isThinking}
          />
        </section>

        {/* Right Column: Tactical Communication & Telemetry Deck */}
        <section className="jarvis-tactical-deck">
          {/* Live Hardware Telemetry HUD */}
          <SystemTelemetry telemetry={telemetry} />

          {/* Holographic Conversation & Composer Deck */}
          <div className="hud-glass-panel hud-corner-brackets jarvis-chat-deck">
            <ConversationFeed
              messages={messages}
              isThinking={isThinking}
              onSelectPrompt={handleSendMessage}
            />

            {/* Manual playback control shown only when the browser blocked
                autoplay. A user click is a valid gesture that satisfies the
                autoplay policy so the retained Piper audio can start. */}
            {autoplayBlocked && (
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'center',
                  padding: '6px 14px',
                }}
              >
                <button
                  type="button"
                  onClick={handleManualPlay}
                  className="hud-glow-btn"
                  aria-label="Play the assistant's spoken response"
                  style={{ padding: '8px 18px', fontSize: '12px' }}
                >
                  ▶ Play response
                </button>
              </div>
            )}

            <CommandComposer
              onSendMessage={handleSendMessage}
              disabled={isThinking}
              onTriggerVoiceDemo={handleTriggerVoiceDemo}
            />
          </div>
        </section>
      </main>

      {/* Settings Modal Drawer */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        settings={settings}
        onUpdateSettings={handleUpdateSettings}
      />
    </div>
  );
}

export default App;
