export type CoreState = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking';


export type AccentColor = 'cyan' | 'arc-blue' | 'violet' | 'amber';

export type AnimationIntensity = 'cinematic' | 'balanced' | 'reduced';

export interface ChatMessage {
  id: string;
  sender: 'user' | 'assistant' | 'system';
  text: string;
  timestamp: string;
  status?: 'sending' | 'complete' | 'error';
  latencyMs?: number;
  tokens?: number;
  toolCall?: {
    name: string;
    details?: string;
    status: 'executing' | 'success' | 'failed';
  };
}

export interface SystemTelemetry {
  modelName: string;
  modelContext: string;
  whisperEngine: string;
  ttsEngine: string;
  memoryDb: string;
  activeMemoriesCount: number;
  status: 'nominal' | 'optimizing' | 'offline';
  coreTemperature: string;
  localLatency: string;
}

export interface UserSettings {
  model: string;
  voice: string;
  animationIntensity: AnimationIntensity;
  accentColor: AccentColor;
  audioChimes: boolean;
  /** Enable spoken replies via the local Piper TTS backend endpoint. */
  enableTts: boolean;
  useFastApiBackend: boolean;
  fastApiUrl: string;
}

export interface QuickActionItem {
  id: string;
  label: string;
  category: string;
  description: string;
  prompt: string;
  icon: 'diagnostic' | 'files' | 'memory' | 'apps';
}
