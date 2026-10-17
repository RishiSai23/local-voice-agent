"""
backend/diagnostics.py
----------------------
High-resolution monotonic timing diagnostics for the JARVIS voice pipeline.

Instruments the voice cycle across all critical stages:
1. Wake-word detection to command-recording start
2. Command recording duration and silence detection
3. Audio preprocessing and Whisper transcription
4. Ollama request start, time to first token, and total Ollama response time
5. Piper synthesis duration
6. Audio playback start
7. End-to-end time from end of user's spoken command to audible response start

Captures Ollama cold-start metadata (load_duration, prompt_eval_duration, eval_duration)
to explicitly identify whether the ~20s initial latency is driven by model weight loading.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("backend.diagnostics")

# Structured JSON log path
DIAGNOSTICS_DIR = Path(__file__).resolve().parent
LOG_FILE = DIAGNOSTICS_DIR / "voice_latency_diagnostics.jsonl"


@dataclass
class VoiceCycleTiming:
    cycle_id: str
    timestamp: str

    # 1. Wake to command recording
    wake_detected_time: float = 0.0
    command_cycle_start_time: float = 0.0
    wake_to_ack_start_ms: float = 0.0
    ack_synth_ms: float = 0.0
    ack_play_ms: float = 0.0
    ack_duration_ms: float = 0.0
    settle_delay_ms: float = 0.0
    wake_to_recording_start_ms: float = 0.0

    # 2. Recording & silence
    recording_start_time: float = 0.0
    speech_start_time: Optional[float] = None
    speech_end_time: Optional[float] = None
    recording_end_time: float = 0.0
    wait_for_speech_ms: float = 0.0
    active_speech_duration_ms: float = 0.0
    trailing_silence_ms: float = 0.0
    total_recording_duration_ms: float = 0.0

    # 3. Whisper Transcription
    whisper_start_time: float = 0.0
    whisper_end_time: float = 0.0
    whisper_duration_ms: float = 0.0
    whisper_model: str = "ggml-base.bin"
    transcript_char_count: int = 0

    # 4. Ollama Generation
    ollama_start_time: float = 0.0
    ollama_first_token_time: Optional[float] = None
    ollama_end_time: float = 0.0
    ollama_time_to_first_token_ms: Optional[float] = None
    ollama_total_duration_ms: float = 0.0
    ollama_load_duration_ms: float = 0.0
    ollama_prompt_eval_duration_ms: float = 0.0
    ollama_eval_duration_ms: float = 0.0
    ollama_prompt_eval_count: int = 0
    ollama_eval_count: int = 0
    ollama_model: str = "qwen3:8b"
    is_cold_start: bool = False

    # 5. Piper Synthesis
    tts_start_time: float = 0.0
    tts_end_time: float = 0.0
    tts_duration_ms: float = 0.0
    tts_engine: str = "unknown"
    tts_fallback_reason: Optional[str] = None
    reply_char_count: int = 0

    # 6. Audio Playback
    playback_start_time: float = 0.0
    playback_setup_ms: float = 0.0

    # 7. End-to-End Latencies
    e2e_from_speech_end_ms: float = 0.0
    e2e_from_recording_end_ms: float = 0.0
    total_cycle_ms: float = 0.0

    # Diagnosis summary
    bottleneck: str = ""
    notes: List[str] = field(default_factory=list)

    def compute_metrics(self) -> None:
        """Derive stage durations and identify bottleneck."""
        if self.recording_start_time and self.wake_detected_time:
            self.wake_to_recording_start_ms = max(
                0.0, (self.recording_start_time - self.wake_detected_time) * 1000.0
            )

        if self.recording_end_time and self.recording_start_time:
            self.total_recording_duration_ms = max(
                0.0, (self.recording_end_time - self.recording_start_time) * 1000.0
            )

        if self.speech_start_time and self.recording_start_time:
            self.wait_for_speech_ms = max(
                0.0, (self.speech_start_time - self.recording_start_time) * 1000.0
            )

        if self.speech_end_time and self.speech_start_time:
            self.active_speech_duration_ms = max(
                0.0, (self.speech_end_time - self.speech_start_time) * 1000.0
            )

        if self.recording_end_time and self.speech_end_time:
            self.trailing_silence_ms = max(
                0.0, (self.recording_end_time - self.speech_end_time) * 1000.0
            )

        if self.whisper_end_time and self.whisper_start_time:
            self.whisper_duration_ms = max(
                0.0, (self.whisper_end_time - self.whisper_start_time) * 1000.0
            )

        if self.ollama_end_time and self.ollama_start_time:
            self.ollama_total_duration_ms = max(
                0.0, (self.ollama_end_time - self.ollama_start_time) * 1000.0
            )

        if self.ollama_first_token_time and self.ollama_start_time:
            self.ollama_time_to_first_token_ms = max(
                0.0, (self.ollama_first_token_time - self.ollama_start_time) * 1000.0
            )

        # Detect cold start from Ollama metadata or time-to-first-token
        if self.ollama_load_duration_ms > 1000.0:
            self.is_cold_start = True
        elif (
            self.ollama_time_to_first_token_ms is not None
            and self.ollama_time_to_first_token_ms > 5000.0
        ):
            self.is_cold_start = True

        if self.tts_end_time and self.tts_start_time:
            self.tts_duration_ms = max(
                0.0, (self.tts_end_time - self.tts_start_time) * 1000.0
            )

        if self.playback_start_time and self.tts_end_time:
            self.playback_setup_ms = max(
                0.0, (self.playback_start_time - self.tts_end_time) * 1000.0
            )

        # End-to-end calculations:
        # Audible response start is when playback_start_time triggers
        ref_playback = self.playback_start_time or self.tts_end_time
        if ref_playback:
            if self.speech_end_time:
                self.e2e_from_speech_end_ms = max(
                    0.0, (ref_playback - self.speech_end_time) * 1000.0
                )
            if self.recording_end_time:
                self.e2e_from_recording_end_ms = max(
                    0.0, (ref_playback - self.recording_end_time) * 1000.0
                )

        if self.playback_start_time and self.command_cycle_start_time:
            self.total_cycle_ms = max(
                0.0, (self.playback_start_time - self.command_cycle_start_time) * 1000.0
            )

        # Bottleneck detection
        components = [
            ("Ollama Model Cold Load", self.ollama_load_duration_ms),
            ("Ollama Generation", max(0.0, self.ollama_total_duration_ms - self.ollama_load_duration_ms)),
            ("Whisper Transcription", self.whisper_duration_ms),
            ("Trailing Silence Wait", self.trailing_silence_ms),
            ("Piper TTS Synthesis", self.tts_duration_ms),
            ("Wake/Ack/Settle Overhead", self.wake_to_recording_start_ms),
            ("Playback Setup", self.playback_setup_ms),
        ]
        components.sort(key=lambda x: x[1], reverse=True)
        top_name, top_val = components[0]
        total_post_rec = (
            self.whisper_duration_ms
            + self.ollama_total_duration_ms
            + self.tts_duration_ms
            + self.playback_setup_ms
        )
        if total_post_rec > 0:
            pct = (top_val / total_post_rec) * 100.0
            self.bottleneck = f"{top_name} ({top_val:.1f}ms / {pct:.1f}% of processing)"
        else:
            self.bottleneck = f"{top_name} ({top_val:.1f}ms)"

    def format_ascii_table(self) -> str:
        """Render a readable diagnostic ASCII table for server stdout/logging."""
        sep = "=" * 78
        subsep = "-" * 78
        cold_tag = "COLD START" if self.is_cold_start else "STEADY STATE"

        lines = [
            sep,
            f" VOICE PIPELINE LATENCY DIAGNOSTICS | ID: {self.cycle_id} | [{cold_tag}]",
            f" Timestamp: {self.timestamp}",
            subsep,
            f" 1. Wake -> Rec Start:     {self.wake_to_recording_start_ms:>8.1f} ms  (wake->ack: {self.wake_to_ack_start_ms:.1f}ms, synth: {self.ack_synth_ms:.1f}ms, play: {self.ack_play_ms:.1f}ms, settle: {self.settle_delay_ms:.1f}ms)",
            f" 2. Command Recording:     {self.total_recording_duration_ms:>8.1f} ms  (speech: {self.active_speech_duration_ms:.1f}ms, trailing silence: {self.trailing_silence_ms:.1f}ms)",
            f" 3. Whisper Transcription: {self.whisper_duration_ms:>8.1f} ms  (model: {self.whisper_model}, chars: {self.transcript_char_count})",
            f" 4. Ollama ({self.ollama_model}):     {self.ollama_total_duration_ms:>8.1f} ms",
        ]

        if self.ollama_time_to_first_token_ms is not None:
            lines.append(
                f"    - Time to 1st Token:   {self.ollama_time_to_first_token_ms:>8.1f} ms"
            )

        if self.ollama_load_duration_ms > 0:
            lines.append(
                f"    - Model Load Duration: {self.ollama_load_duration_ms:>8.1f} ms  {'<-- COLD WEIGHT LOAD' if self.ollama_load_duration_ms > 1000 else ''}"
            )

        if self.ollama_prompt_eval_duration_ms > 0:
            lines.append(
                f"    - Prompt Evaluation:   {self.ollama_prompt_eval_duration_ms:>8.1f} ms  ({self.ollama_prompt_eval_count} tokens)"
            )

        if self.ollama_eval_duration_ms > 0:
            tok_per_sec = (
                (self.ollama_eval_count / (self.ollama_eval_duration_ms / 1000.0))
                if self.ollama_eval_duration_ms > 0
                else 0.0
            )
            lines.append(
                f"    - Token Generation:    {self.ollama_eval_duration_ms:>8.1f} ms  ({self.ollama_eval_count} tokens, {tok_per_sec:.1f} tok/s)"
            )

        tts_info = f" 5. Piper Synthesis:       {self.tts_duration_ms:>8.1f} ms  (engine: {self.tts_engine}, reply chars: {self.reply_char_count})"
        if self.tts_engine == "cli-fallback" and self.tts_fallback_reason:
            tts_info += f" [FALLBACK: {self.tts_fallback_reason}]"

        lines.extend([
            tts_info,
            f" 6. Audio Playback Setup:  {self.playback_setup_ms:>8.1f} ms  (stream init)",
            subsep,
            f" >> LATENCY SUMMARY:",
            f"    - End-of-Speech to Audio Start:    {self.e2e_from_speech_end_ms:>8.1f} ms  ({self.e2e_from_speech_end_ms/1000.0:.2f} s)",
            f"    - End-of-Recording to Audio Start: {self.e2e_from_recording_end_ms:>8.1f} ms  ({self.e2e_from_recording_end_ms/1000.0:.2f} s)",
            f"    - Primary Bottleneck:              {self.bottleneck}",
            sep,
        ])
        return "\n".join(lines)


class DiagnosticsManager:
    """Thread-safe manager for recording and querying voice latency diagnostics."""

    def __init__(self, max_history: int = 50):
        self._history: collections.deque[VoiceCycleTiming] = collections.deque(
            maxlen=max_history
        )
        self._lock = threading.Lock()

    def record_cycle(self, timing: VoiceCycleTiming) -> None:
        """Finalize metrics, log to file and console, and retain in memory."""
        timing.compute_metrics()

        # Emit formatted table to logger
        report = timing.format_ascii_table()
        logger.info("\n%s", report)
        print(f"\n{report}\n")

        with self._lock:
            self._history.append(timing)

        # Append structured JSON record locally (without sensitive user content)
        try:
            record_data = asdict(timing)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record_data) + "\n")
        except Exception as err:
            logger.debug("Failed to write diagnostics JSON record: %s", err)

    def get_latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._history:
                return None
            return asdict(self._history[-1])

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._history)[-limit:]
            return [asdict(item) for item in reversed(items)]


# Process-wide singleton
_diagnostics_manager = DiagnosticsManager()


def get_diagnostics_manager() -> DiagnosticsManager:
    return _diagnostics_manager
