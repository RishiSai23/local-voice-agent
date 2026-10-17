export type CoreState = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking';

/**
 * Real backend voice assistant lifecycle states (Stage 3B & 4A)
 * Directly matches VoiceAssistant state machine in backend/voice_loop.py
 */
export type VoiceAssistantState =
  | 'stopped'
  | 'wake_listening'
  | 'acknowledging'
  | 'recording_command'
  | 'transcribing'
  | 'thinking'
  | 'speaking'
  | 'error';

export interface VoiceAssistantStatus {
  state: VoiceAssistantState;
  running: boolean;
  error: string | null;
  active_conversation_id?: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  /** Backend returns this as 'timestamp' (ISO-8601 UTC string) */
  timestamp: string;
  metadata?: {
    toolCall?: {
      name: string;
      details?: string;
      status?: 'executing' | 'success' | 'failed';
    };
    source?: string;
    latencyMs?: number;
    tokens?: number;
    [key: string]: any;
  } | null;
}

export interface ConversationsListResponse {
  conversations: Conversation[];
  active_conversation_id: string | null;
}

export interface ConversationDetailResponse {
  conversation: Conversation;
}

export interface ConversationMessagesResponse {
  conversation_id: string;
  messages: ConversationMessage[];
}

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

/**
 * Task Engine & Reminder Types (Stage 5)
 */
export type TaskStatus = 'pending' | 'completed' | 'cancelled';
export type TaskRecurrence = 'daily' | 'weekly' | 'weekday' | null;

export interface Task {
  id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  due_at: string | null;
  reminder_at: string | null;
  recurrence: TaskRecurrence;
  last_fired_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  conversation_id: string | null;
}

export interface TasksListResponse {
  tasks: Task[];
  total: number;
}

export interface RemindersListResponse {
  reminders: Task[];
  total: number;
}

export interface CreateTaskInput {
  title: string;
  description?: string;
  due_at?: string;
  reminder_at?: string;
  recurrence?: TaskRecurrence;
  conversation_id?: string;
}

export interface UpdateTaskInput {
  title?: string;
  description?: string;
  status?: TaskStatus;
  due_at?: string;
  reminder_at?: string;
  recurrence?: TaskRecurrence;
}

export interface NotificationSettings {
  windows_notifications_enabled: boolean;
  spoken_reminders_enabled: boolean;
}
