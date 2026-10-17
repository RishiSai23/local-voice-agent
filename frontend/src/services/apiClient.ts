/**
 * Centralized TypeScript API Client for J.A.R.V.I.S. Local Backend
 *
 * Provides unified, strictly-typed access to:
 * - GET  /health
 * - POST /chat
 * - POST /transcribe
 * - POST /speak
 * - GET  /voice-assistant/status
 * - POST /voice-assistant/start
 * - POST /voice-assistant/stop
 * - POST /voice-assistant/cancel
 *
 * Preserves 100% offline, local-only architecture with zero cloud telemetry.
 */

import type {
  VoiceAssistantState,
  VoiceAssistantStatus,
  Conversation,
  ConversationMessage,
  ConversationsListResponse,
  ConversationDetailResponse,
  ConversationMessagesResponse,
  Task,
  TasksListResponse,
  RemindersListResponse,
  CreateTaskInput,
  UpdateTaskInput,
  NotificationSettings,
} from '../types';
import type { AgentResponse, BackendHealth } from './agentService';

export const DEFAULT_BACKEND_URL = 'http://localhost:8000';

export interface StartAssistantResponse {
  status: 'started' | 'already_running';
  state: VoiceAssistantState;
}

export interface StopAssistantResponse {
  status: 'stopped' | 'not_running';
  state: 'stopped';
}

export interface CancelAssistantResponse {
  status: 'cancelling' | 'nothing_to_cancel';
  state: VoiceAssistantState;
}

/**
 * Safely parse error details from a failed backend HTTP response.
 */
async function parseErrorDetail(response: Response, defaultMessage: string): Promise<string> {
  try {
    const errorData = await response.json();
    if (errorData && typeof errorData.detail === 'string') {
      return errorData.detail;
    }
    if (errorData && Array.isArray(errorData.detail)) {
      return errorData.detail
        .map((d: { msg?: string }) => d.msg || JSON.stringify(d))
        .join(', ');
    }
    if (errorData && errorData.detail) {
      return JSON.stringify(errorData.detail);
    }
  } catch {
    // Response was not JSON
  }
  return defaultMessage;
}

/**
 * Fetch with an explicit timeout to prevent lingering pending requests.
 */
async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = 8000
): Promise<Response> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
    });
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error(`Request timed out after ${timeoutMs}ms.`);
    }
    throw err;
  } finally {
    clearTimeout(id);
  }
}

/**
 * Query the hands-free voice assistant lifecycle state (Stage 3B / 4A).
 */
export async function getVoiceAssistantStatus(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<VoiceAssistantStatus> {
  let response: Response;
  try {
    response = await fetchWithTimeout(`${baseUrl}/voice-assistant/status`, { method: 'GET' }, 3000);
  } catch {
    throw new Error(`Unable to connect to local voice assistant at ${baseUrl}. Backend may be offline.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to fetch assistant status: ${detail}`);
  }

  const data = (await response.json()) as VoiceAssistantStatus;
  if (!data || typeof data.state !== 'string') {
    throw new Error('Malformed voice-assistant status response from backend.');
  }

  return data;
}

/**
 * Start the hands-free voice loop (arms openWakeWord background listener).
 */
export async function startVoiceAssistant(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<StartAssistantResponse> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/voice-assistant/start`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' } },
      5000
    );
  } catch {
    throw new Error(`Failed to reach ${baseUrl}/voice-assistant/start. Ensure the backend server is running.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to start voice assistant: ${detail}`);
  }

  return (await response.json()) as StartAssistantResponse;
}

/**
 * Stop the voice loop, interrupt active recording/playback, release the microphone.
 */
export async function stopVoiceAssistant(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<StopAssistantResponse> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/voice-assistant/stop`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' } },
      5000
    );
  } catch {
    throw new Error(`Failed to reach ${baseUrl}/voice-assistant/stop. Backend may be unreachable.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to stop voice assistant: ${detail}`);
  }

  return (await response.json()) as StopAssistantResponse;
}

/**
 * Cancel the active command capture and return immediately to wake listening.
 */
export async function cancelVoiceAssistant(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<CancelAssistantResponse> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/voice-assistant/cancel`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' } },
      4000
    );
  } catch {
    throw new Error(`Failed to reach ${baseUrl}/voice-assistant/cancel.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to cancel command: ${detail}`);
  }

  return (await response.json()) as CancelAssistantResponse;
}

/**
 * Send a typed prompt directly to the local agent endpoint (POST /chat).
 * Supports optional conversationId to link interaction.
 * Backward compatible: if second argument is a URL string, treats it as baseUrl.
 */
export async function sendChatMessage(
  message: string,
  conversationIdOrBaseUrl?: string,
  explicitBaseUrl?: string
): Promise<AgentResponse & { conversationId?: string }> {
  const trimmed = message.trim();
  if (!trimmed) {
    throw new Error('Message cannot be empty.');
  }

  let conversationId: string | undefined;
  let baseUrl: string = DEFAULT_BACKEND_URL;

  if (conversationIdOrBaseUrl) {
    if (
      conversationIdOrBaseUrl.startsWith('http://') ||
      conversationIdOrBaseUrl.startsWith('https://')
    ) {
      baseUrl = conversationIdOrBaseUrl;
    } else {
      conversationId = conversationIdOrBaseUrl;
      baseUrl = explicitBaseUrl || DEFAULT_BACKEND_URL;
    }
  }

  const startTime = performance.now();
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/chat`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: trimmed,
          conversation_id: conversationId,
        }),
      },
      45000
    );
  } catch {
    throw new Error(`Unable to reach local FastAPI backend at ${baseUrl}/chat.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`[AGENT ERROR ${response.status}] ${detail}`);
  }

  const data = (await response.json()) as {
    reply: string;
    toolCall?: AgentResponse['toolCall'];
    conversation_id?: string;
  };

  if (!data || typeof data.reply !== 'string') {
    throw new Error('Malformed response from backend: Missing "reply" field.');
  }

  const endTime = performance.now();
  return {
    reply: data.reply,
    latencyMs: Math.round(endTime - startTime),
    tokens: Math.max(1, Math.round(data.reply.length / 4)),
    toolCall: data.toolCall,
    conversationId: data.conversation_id,
  };
}

/**
 * Check backend subsystem health (Ollama, SQLite memory, workspace).
 */
export async function checkBackendHealth(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<BackendHealth> {
  let response: Response;
  try {
    response = await fetchWithTimeout(`${baseUrl}/health`, { method: 'GET' }, 3000);
  } catch {
    throw new Error(`Backend health check failed: unable to connect to ${baseUrl}/health.`);
  }

  if (!response.ok) {
    throw new Error(`Backend health check returned HTTP ${response.status}`);
  }

  return (await response.json()) as BackendHealth;
}

// ============================================================================
// Persistent Conversation API Client (Stage 4B)
// ============================================================================

/**
 * List all saved conversations ordered by updated_at descending.
 */
export async function listConversations(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<ConversationsListResponse> {
  let response: Response;
  try {
    response = await fetchWithTimeout(`${baseUrl}/conversations`, { method: 'GET' }, 4000);
  } catch {
    throw new Error(`Unable to load conversations from ${baseUrl}. Backend may be offline.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to list conversations: ${detail}`);
  }

  return (await response.json()) as ConversationsListResponse;
}

/**
 * Create a new conversation and set it as active on the backend.
 */
export async function createConversation(
  title?: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ conversation: Conversation; active_conversation_id: string }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/conversations`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title || 'New Chat' }),
      },
      4000
    );
  } catch {
    throw new Error(`Unable to create conversation at ${baseUrl}. Backend may be offline.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to create conversation: ${detail}`);
  }

  return (await response.json()) as {
    conversation: Conversation;
    active_conversation_id: string;
  };
}

/**
 * Get active conversation ID and conversation metadata.
 */
export async function getActiveConversation(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ active_conversation_id: string; conversation: Conversation | null }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(`${baseUrl}/conversations/active`, { method: 'GET' }, 3000);
  } catch {
    throw new Error(`Unable to fetch active conversation from ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to get active conversation: ${detail}`);
  }

  return (await response.json()) as {
    active_conversation_id: string;
    conversation: Conversation | null;
  };
}

/**
 * Change the active conversation ID on the backend.
 */
export async function setActiveConversation(
  conversationId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ active_conversation_id: string; conversation: Conversation }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/conversations/active`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: conversationId }),
      },
      4000
    );
  } catch {
    throw new Error(`Unable to set active conversation at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to set active conversation: ${detail}`);
  }

  return (await response.json()) as {
    active_conversation_id: string;
    conversation: Conversation;
  };
}

/**
 * Fetch all messages for a specific conversation in chronological order.
 */
export async function getConversationMessages(
  conversationId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<ConversationMessagesResponse> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/conversations/${encodeURIComponent(conversationId)}/messages`,
      { method: 'GET' },
      5000
    );
  } catch {
    throw new Error(`Unable to fetch conversation messages from ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to fetch messages: ${detail}`);
  }

  return (await response.json()) as ConversationMessagesResponse;
}

/**
 * Rename a conversation title.
 */
export async function renameConversation(
  conversationId: string,
  title: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ conversation: Conversation }> {
  const trimmed = title.trim();
  if (!trimmed) {
    throw new Error('Title cannot be empty.');
  }

  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/conversations/${encodeURIComponent(conversationId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed }),
      },
      4000
    );
  } catch {
    throw new Error(`Unable to rename conversation at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to rename conversation: ${detail}`);
  }

  return (await response.json()) as { conversation: Conversation };
}

/**
 * Delete a conversation and all its associated messages.
 */
export async function deleteConversation(
  conversationId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ status: string; conversation_id: string; active_conversation_id: string }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/conversations/${encodeURIComponent(conversationId)}`,
      { method: 'DELETE' },
      4000
    );
  } catch {
    throw new Error(`Unable to delete conversation at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to delete conversation: ${detail}`);
  }

  return (await response.json()) as {
    status: string;
    conversation_id: string;
    active_conversation_id: string;
  };
}

/**
 * Append a user message and assistant reply to a conversation.
 */
export async function appendConversationInteraction(
  conversationId: string,
  userMessage: string,
  assistantReply: string,
  metadata?: Record<string, any>,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ conversation_id: string; messages: ConversationMessage[] }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_message: userMessage,
          assistant_reply: assistantReply,
          metadata: metadata || {},
        }),
      },
      4000
    );
  } catch {
    throw new Error(`Unable to append interaction at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to append interaction: ${detail}`);
  }

  return (await response.json()) as {
    conversation_id: string;
    messages: ConversationMessage[];
  };
}

// =============================================================================
// Stage 5 — Tasks & Reminders API Client Methods
// =============================================================================

export interface ListTasksOptions {
  status?: string;
  due_before?: string;
  due_after?: string;
  limit?: number;
}

/**
 * List tasks with optional status and due-date filters.
 */
export async function listTasks(
  options: ListTasksOptions = {},
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<TasksListResponse> {
  const params = new URLSearchParams();
  if (options.status) params.set('status', options.status);
  if (options.due_before) params.set('due_before', options.due_before);
  if (options.due_after) params.set('due_after', options.due_after);
  if (options.limit) params.set('limit', String(options.limit));

  const url = `${baseUrl}/tasks${params.toString() ? `?${params.toString()}` : ''}`;
  let response: Response;
  try {
    response = await fetchWithTimeout(url, { method: 'GET' }, 5000);
  } catch {
    throw new Error(`Unable to list tasks from ${baseUrl}. Backend may be offline.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to list tasks: ${detail}`);
  }

  return (await response.json()) as TasksListResponse;
}

/**
 * Create a new task.
 */
export async function createTask(
  input: CreateTaskInput,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ task: Task }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/tasks`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      },
      5000
    );
  } catch {
    throw new Error(`Unable to create task at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to create task: ${detail}`);
  }

  return (await response.json()) as { task: Task };
}

/**
 * Retrieve a task by ID.
 */
export async function getTask(
  taskId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ task: Task }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(`${baseUrl}/tasks/${encodeURIComponent(taskId)}`, { method: 'GET' }, 5000);
  } catch {
    throw new Error(`Unable to fetch task at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to fetch task: ${detail}`);
  }

  return (await response.json()) as { task: Task };
}

/**
 * Update a task's title, description, status, due date, reminder, or recurrence.
 */
export async function updateTask(
  taskId: string,
  updates: UpdateTaskInput,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ task: Task }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/tasks/${encodeURIComponent(taskId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      },
      5000
    );
  } catch {
    throw new Error(`Unable to update task at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to update task: ${detail}`);
  }

  return (await response.json()) as { task: Task };
}

/**
 * Mark a task as completed.
 */
export async function completeTask(
  taskId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ task: Task }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/tasks/${encodeURIComponent(taskId)}/complete`,
      { method: 'POST' },
      5000
    );
  } catch {
    throw new Error(`Unable to complete task at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to complete task: ${detail}`);
  }

  return (await response.json()) as { task: Task };
}

/**
 * Cancel a task.
 */
export async function cancelTask(
  taskId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ task: Task }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: 'POST' },
      5000
    );
  } catch {
    throw new Error(`Unable to cancel task at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to cancel task: ${detail}`);
  }

  return (await response.json()) as { task: Task };
}

/**
 * Permanently delete a task.
 */
export async function deleteTask(
  taskId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ status: string; task_id: string }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/tasks/${encodeURIComponent(taskId)}`,
      { method: 'DELETE' },
      5000
    );
  } catch {
    throw new Error(`Unable to delete task at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to delete task: ${detail}`);
  }

  return (await response.json()) as { status: string; task_id: string };
}

/**
 * Snooze a reminder by N minutes.
 */
export async function snoozeReminder(
  taskId: string,
  minutes: number = 10,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ task: Task; snoozed_minutes: number }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/tasks/${encodeURIComponent(taskId)}/snooze`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minutes }),
      },
      5000
    );
  } catch {
    throw new Error(`Unable to snooze reminder at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to snooze reminder: ${detail}`);
  }

  return (await response.json()) as { task: Task; snoozed_minutes: number };
}

/**
 * Dismiss a reminder.
 */
export async function dismissReminder(
  taskId: string,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ task: Task }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/tasks/${encodeURIComponent(taskId)}/dismiss`,
      { method: 'POST' },
      5000
    );
  } catch {
    throw new Error(`Unable to dismiss reminder at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to dismiss reminder: ${detail}`);
  }

  return (await response.json()) as { task: Task };
}

/**
 * List upcoming reminders in the next window (e.g. 72h).
 */
export async function listUpcomingReminders(
  options: { limit?: number; window_hours?: number } = {},
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<RemindersListResponse> {
  const params = new URLSearchParams();
  if (options.limit) params.set('limit', String(options.limit));
  if (options.window_hours) params.set('window_hours', String(options.window_hours));

  const url = `${baseUrl}/reminders/upcoming${params.toString() ? `?${params.toString()}` : ''}`;
  let response: Response;
  try {
    response = await fetchWithTimeout(url, { method: 'GET' }, 5000);
  } catch {
    throw new Error(`Unable to fetch upcoming reminders from ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to fetch upcoming reminders: ${detail}`);
  }

  return (await response.json()) as RemindersListResponse;
}

/**
 * Get notification settings (Windows toast and spoken reminder announcements).
 */
export async function getNotificationSettings(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ settings: NotificationSettings }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(`${baseUrl}/settings/notifications`, { method: 'GET' }, 4000);
  } catch {
    throw new Error(`Unable to fetch notification settings from ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to fetch notification settings: ${detail}`);
  }

  return (await response.json()) as { settings: NotificationSettings };
}

/**
 * Update notification settings.
 */
export async function updateNotificationSettings(
  updates: Partial<NotificationSettings>,
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ settings: NotificationSettings }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/settings/notifications`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      },
      4000
    );
  } catch {
    throw new Error(`Unable to update notification settings at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to update notification settings: ${detail}`);
  }

  return (await response.json()) as { settings: NotificationSettings };
}

/**
 * Manually trigger a scheduler check (useful for instant verification).
 */
export async function triggerSchedulerCheck(
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<{ status: string; claimed_count: number; scheduler_running: boolean }> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/scheduler/trigger`,
      { method: 'POST' },
      5000
    );
  } catch {
    throw new Error(`Unable to trigger scheduler check at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Failed to trigger scheduler: ${detail}`);
  }

  return (await response.json()) as { status: string; claimed_count: number; scheduler_running: boolean };
}

/**
 * Trigger a diagnostic test notification.
 */
export async function testNotification(
  payload: { title?: string; message?: string; test_speech?: boolean } = {},
  baseUrl: string = DEFAULT_BACKEND_URL
): Promise<any> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${baseUrl}/notifications/test`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
      14000
    );
  } catch {
    throw new Error(`Unable to trigger test notification at ${baseUrl}.`);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response, `HTTP ${response.status}`);
    throw new Error(`Test notification failed: ${detail}`);
  }

  return await response.json();
}
