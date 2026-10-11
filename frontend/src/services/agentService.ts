import type { ChatMessage } from '../types';

/**
 * JARVIS Agent Service Layer
 * 
 * Provides simulated local neural intelligence for UI demonstration and
 * bridges directly to the local FastAPI backend (POST /chat) when enabled.
 */

export interface AgentResponse {
  reply: string;
  latencyMs: number;
  tokens: number;
  toolCall?: {
    name: string;
    details?: string;
    status: 'executing' | 'success' | 'failed';
  };
}

export interface BackendHealth {
  status: 'healthy' | 'degraded' | 'offline';
  api: string;
  ollama: {
    status: 'online' | 'offline';
    endpoint: string;
    configured_model: string;
    model_available: boolean;
    available_models: string[];
    error?: string | null;
  };
  memory: {
    status: string;
    database: string;
    active_memories: number;
  };
  workspace: {
    status: string;
    path: string;
  };
}

/**
 * Query the local FastAPI backend health status without starting any background process.
 */
export async function checkBackendHealth(
  backendUrl: string = 'http://localhost:8000'
): Promise<BackendHealth> {
  const response = await fetch(`${backendUrl}/health`);
  if (!response.ok) {
    throw new Error(`Backend health check failed with HTTP ${response.status} (${response.statusText})`);
  }
  return response.json();
}

/**
 * Transmit user message to either the live FastAPI backend or the offline demo engine.
 */
export async function sendUserPrompt(
  prompt: string,
  useBackend: boolean = false,
  backendUrl: string = 'http://localhost:8000'
): Promise<AgentResponse> {
  const startTime = performance.now();

  // --- LIVE FASTAPI BACKEND MODE ---
  if (useBackend) {
    let response: Response;
    try {
      response = await fetch(`${backendUrl}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: prompt }),
      });
    } catch {
      // Hard network failure: backend server is down or unreachable
      throw new Error(
        `Unable to reach local FastAPI backend at ${backendUrl}. Ensure the backend server is running or toggle Demo Mode in Settings.`
      );
    }

    if (!response.ok) {
      let detail = `HTTP ${response.status} (${response.statusText || 'Error'})`;
      try {
        const errorData = await response.json();
        if (errorData && typeof errorData.detail === 'string') {
          detail = errorData.detail;
        } else if (errorData && Array.isArray(errorData.detail)) {
          detail = errorData.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join(', ');
        } else if (errorData && errorData.detail) {
          detail = JSON.stringify(errorData.detail);
        }
      } catch {
        // Response was not JSON
      }
      throw new Error(`[BACKEND ERROR ${response.status}] ${detail}`);
    }

    let data: unknown;
    try {
      data = await response.json();
    } catch {
      throw new Error('Malformed response from backend: Invalid JSON format.');
    }

    if (!data || typeof (data as { reply?: unknown }).reply !== 'string') {
      throw new Error('Malformed response from backend: Missing "reply" field.');
    }

    const typedData = data as {
      reply: string;
      toolCall?: AgentResponse['toolCall'];
    };

    const endTime = performance.now();
    return {
      reply: typedData.reply,
      latencyMs: Math.round(endTime - startTime),
      tokens: Math.max(1, Math.round(typedData.reply.length / 4)),
      toolCall: typedData.toolCall,
    };
  }

  // --- LOCAL DEMO RESPONSE ENGINE ---
  // Realistic simulated latency: 600ms to 1200ms
  await new Promise((resolve) => setTimeout(resolve, 850));

  const lower = prompt.toLowerCase();
  let reply = '';
  let toolCall: AgentResponse['toolCall'] = undefined;

  if (lower.includes('diagnostic') || lower.includes('status') || lower.includes('health') || lower.includes('system')) {
    toolCall = {
      name: 'tools.get_system_info',
      details: 'OS: Windows 11 Pro (64-bit) | CPU: 16-Threads | RAM: 32 GB (41% Allocated) | GPU: NVIDIA RTX CUDA Active',
      status: 'success',
    };
    reply =
      'Diagnostics complete, sir. All core subsystems are operating within optimal tolerances. Whisper speech-to-text binary is indexed, Piper neural TTS voice model is cached in memory, and your SQLite memory database is synchronized.';
  } else if (lower.includes('file') || lower.includes('workspace') || lower.includes('directory') || lower.includes('code')) {
    toolCall = {
      name: 'tools.list_files',
      details: 'assistant_workspace/ | voice_agent.py | memory.py | tools.py | memory.db | en_US-lessac-medium.onnx',
      status: 'success',
    };
    reply =
      'I have indexed your workspace, Rishi. The repository contains local voice agent components including voice_agent.py, tools.py, and your neural TTS model en_US-lessac-medium.onnx. All local files are secured on your drive.';
  } else if (lower.includes('memory') || lower.includes('remember') || lower.includes('recall')) {
    toolCall = {
      name: 'memory.get_memories',
      details: 'Querying SQLite memory.db table "memories"... Found 14 active associative records.',
      status: 'success',
    };
    reply =
      'Accessing persistent memory bank, sir. I have preserved 14 memory records in SQLite, including your coding preferences, project architecture rules, and privacy configuration for 100% offline local inference.';
  } else if (lower.includes('who are you') || lower.includes('what are you') || lower.includes('jarvis')) {
    reply =
      'I am J.A.R.V.I.S.—Just A Rather Very Intelligent System. In this iteration, I run completely on your local machine using Qwen3 8B via Ollama, local Whisper transcription, and Piper neural speech. No telemetry, no cloud tokens, purely your intelligence.';
  } else if (lower.includes('app') || lower.includes('launch') || lower.includes('open')) {
    toolCall = {
      name: 'tools.open_application',
      details: 'Target: Visual Studio Code | Status: Hook configured (Frontend Demo)',
      status: 'success',
    };
    reply =
      'Application launch protocol verified. When connected to the FastAPI backend, this triggers the native Windows process launcher via tools.open_application().';
  } else {
    reply = `Command received: "${prompt}". Local Qwen3 8B neural engine is standing by. All protocols are primed for voice automation and tools execution. What shall we work on next, Rishi?`;
  }

  const endTime = performance.now();
  const latencyMs = Math.round(endTime - startTime);
  const tokens = Math.max(28, Math.round(reply.length / 3.8));

  return { reply, latencyMs, tokens, toolCall };
}

export const INITIAL_MESSAGES: ChatMessage[] = [
  {
    id: 'msg-init-1',
    sender: 'assistant',
    text: 'All local neural systems initialized and standing by. Whisper ASR, Piper TTS, and SQLite memory layers are nominal. Ready for your command, Rishi.',
    timestamp: 'INITIALIZED',
    latencyMs: 124,
    tokens: 34,
    status: 'complete',
  },
];
