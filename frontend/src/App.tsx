import { useState, useEffect } from 'react';
import type { CoreState, ChatMessage, UserSettings, SystemTelemetry as SystemTelemetryType } from './types';
import { sendUserPrompt, checkBackendHealth, INITIAL_MESSAGES } from './services/agentService';
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
  useFastApiBackend: false,
  fastApiUrl: 'http://localhost:8000',
};

function App() {
  // Application State
  const [coreState, setCoreState] = useState<CoreState>('idle');
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [isThinking, setIsThinking] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

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

  // Submit User Message Handler
  const handleSendMessage = async (text: string) => {
    if (!text.trim() || isThinking) return;

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

      // Transition to speaking state for response presentation
      setCoreState('speaking');
      setTimeout(() => {
        setCoreState('idle');
      }, 3200);
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
