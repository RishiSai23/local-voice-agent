# J.A.R.V.I.S. // Local Neural Interface

A cinematic, futuristic personal AI assistant interface inspired by JARVIS from Iron Man. Built with React 19, TypeScript, and Vite. Designed specifically for local AI architectures powered by Ollama (Qwen3 8B), Whisper speech recognition, Piper neural TTS, and SQLite memory.

---

## 🚀 Key Features & Visual Design

- **Animated Arc Reactor AI Core:**
  - Multi-layered vector HUD centerpiece with 6 concentric orbital rings, precision degree reticle ticks, rotating segmented arcs, orbital diamond quantum nodes, and a luminous singularity nucleus.
  - 4 distinct animated states:
    1. **Idle:** Calm, slow rotation with gentle breathing cyan glow.
    2. **Listening:** Bright electric cyan radiance with expanding acoustic wave ripple rings.
    3. **Thinking:** High-speed orbital rotation, quantum violet/cyan flux, and energetic nucleus vibration.
    4. **Speaking:** Harmonic audio frequency modulation with radial soundwave pulses.
  - Interactive state switcher pills and click-to-cycle core interaction.

- **Futuristic HUD Header:**
  - Distinctive `J.A.R.V.I.S. // MK-85 LOCAL` identity wordmark.
  - Live military telemetry clock with real-time seconds ticking.
  - Local AI engine status badge (`SYSTEMS NOMINAL • 100% OFFLINE`).
  - Active model indicator (`Qwen3 8B [OLLAMA]`).
  - Neural Configuration trigger button.

- **Conversation Stream:**
  - Holographic message cards with glowing borders and glassmorphism.
  - User vs. Assistant distinct visual styling.
  - Message metadata tags: Local latency (`[120ms]`), token counts, and timestamps.
  - Tool execution badges with terminal-style logs (`tools.get_system_info`, `tools.list_files`, `memory.get_memories`).
  - Fluid entrance animations with auto-scroll down.

- **Command Composer:**
  - Angled cyber input capsule with glowing focus states.
  - Keyboard navigation: `Enter` to transmit, `Shift + Enter` for new lines.
  - Microphone control with interactive Phase 2 voice demo simulation.
  - One-click starter prompts in empty state.

- **Telemetry & Quick Action Deck:**
  - Live sensor cards displaying the local LLM, Whisper ASR, Piper TTS, and SQLite memory records.
  - Operational Protocol shortcuts (`System Health`, `Workspace Repo`, `SQLite Memory`, `Dev Protocol`).

- **Settings Surface:**
  - Local model selection (`Qwen3 8B`, `Qwen 2.5 7B`, `Llama 3.2 3B`, `DeepSeek R1 Distill`).
  - Neural voice selection (`en_US-lessac-medium`, `en_US-ryan-medium`, `en_GB-alan-medium`).
  - Motion design preference (`Cinematic Full`, `Balanced`, `Reduced Motion`).
  - Theme accent palette (`Electric Cyan`, `Arc Reactor Blue`, `Quantum Violet`, `Reactor Amber`).
  - Future FastAPI bridge toggle (`http://localhost:8000/chat`).

---

## 🛠️ How to Run & Inspect

Navigate to the frontend folder:

```bash
cd D:\Projects\local-voice-agent\frontend
```

Start the Vite development server:

```bash
npm run dev
```

Open your browser at the displayed local URL (typically [http://localhost:5173/](http://localhost:5173/)).
