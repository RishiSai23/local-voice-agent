import {
  type FC,
  useState,
  useRef,
  useEffect,
  useCallback,
  type KeyboardEvent,
} from 'react';
import { SendIcon, MicIcon, SparklesIcon } from './Icons';
import { transcribeAudio } from '../services/agentService';

// ---------------------------------------------------------------------------
// WAV encoding helpers
// ---------------------------------------------------------------------------

/**
 * Write a 4-byte little-endian uint32 into a DataView at `offset`.
 */
function writeUint32LE(view: DataView, offset: number, value: number): void {
  view.setUint32(offset, value, true);
}

/**
 * Write a 2-byte little-endian uint16 into a DataView at `offset`.
 */
function writeUint16LE(view: DataView, offset: number, value: number): void {
  view.setUint16(offset, value, true);
}

/**
 * Build a RIFF/WAVE header + 16-bit PCM body from a Float32Array of audio
 * samples already at the target sample rate.
 *
 * Output format guaranteed to match Whisper.cpp requirements:
 *   - RIFF / WAVE
 *   - PCM (format tag 1)
 *   - 1 channel (mono)
 *   - 16 000 Hz sample rate
 *   - 16-bit signed integer samples (little-endian)
 */
function float32ToWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const numSamples = samples.length;
  // 44-byte RIFF header + 2 bytes per 16-bit sample
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  // ---- RIFF chunk descriptor ----
  // ChunkID: "RIFF"
  view.setUint8(0, 0x52); view.setUint8(1, 0x49);
  view.setUint8(2, 0x46); view.setUint8(3, 0x46);
  // ChunkSize: total file size minus the 8-byte RIFF header
  writeUint32LE(view, 4, 36 + numSamples * 2);
  // Format: "WAVE"
  view.setUint8(8, 0x57); view.setUint8(9, 0x41);
  view.setUint8(10, 0x56); view.setUint8(11, 0x45);

  // ---- fmt sub-chunk ----
  // Subchunk1ID: "fmt "
  view.setUint8(12, 0x66); view.setUint8(13, 0x6d);
  view.setUint8(14, 0x74); view.setUint8(15, 0x20);
  // Subchunk1Size: 16 for PCM
  writeUint32LE(view, 16, 16);
  // AudioFormat: 1 = PCM
  writeUint16LE(view, 20, 1);
  // NumChannels: 1 = mono
  writeUint16LE(view, 22, 1);
  // SampleRate
  writeUint32LE(view, 24, sampleRate);
  // ByteRate: SampleRate * NumChannels * BitsPerSample / 8
  writeUint32LE(view, 28, sampleRate * 1 * 2);
  // BlockAlign: NumChannels * BitsPerSample / 8
  writeUint16LE(view, 32, 1 * 2);
  // BitsPerSample: 16
  writeUint16LE(view, 34, 16);

  // ---- data sub-chunk ----
  // Subchunk2ID: "data"
  view.setUint8(36, 0x64); view.setUint8(37, 0x61);
  view.setUint8(38, 0x74); view.setUint8(39, 0x61);
  // Subchunk2Size
  writeUint32LE(view, 40, numSamples * 2);

  // PCM samples: clamp float32 [-1, 1] → int16 [-32768, 32767]
  let offset = 44;
  for (let i = 0; i < numSamples; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    // Scale negative samples to -32768 and positive to 32767 to avoid clipping
    const s = clamped < 0 ? clamped * 32768 : clamped * 32767;
    view.setInt16(offset, Math.max(-32768, Math.min(32767, Math.round(s))), true);
    offset += 2;
  }

  return buffer;
}

/**
 * Decode a raw audio blob (any format the browser supports), mix down to
 * mono, and resample to `targetSampleRate` using OfflineAudioContext.
 *
 * Returns a Float32Array of PCM samples at the target rate.
 */
async function decodeAndResample(
  rawBlob: Blob,
  targetSampleRate: number
): Promise<Float32Array> {
  const arrayBuffer = await rawBlob.arrayBuffer();

  // Decode the raw audio (works for webm/opus, ogg, mp4, etc.)
  const decodeCtx = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await decodeCtx.decodeAudioData(arrayBuffer);
  } finally {
    await decodeCtx.close();
  }

  const sourceSampleRate = decoded.sampleRate;
  const numChannels = decoded.numberOfChannels;
  const numInputFrames = decoded.length;

  // Mix down to mono by averaging all channels.
  const monoInput = new Float32Array(numInputFrames);
  for (let ch = 0; ch < numChannels; ch++) {
    const channelData = decoded.getChannelData(ch);
    for (let i = 0; i < numInputFrames; i++) {
      monoInput[i] += channelData[i] / numChannels;
    }
  }

  // If the source is already at the target rate, skip resampling.
  if (sourceSampleRate === targetSampleRate) {
    return monoInput;
  }

  // Resample via OfflineAudioContext: create a buffer at the source rate,
  // fill it with the mono data, then render at the target rate.
  const numOutputFrames = Math.round(
    (numInputFrames * targetSampleRate) / sourceSampleRate
  );
  const offlineCtx = new OfflineAudioContext(
    1,               // mono
    numOutputFrames,
    targetSampleRate
  );

  const srcBuffer = offlineCtx.createBuffer(1, numInputFrames, sourceSampleRate);
  srcBuffer.copyToChannel(monoInput, 0);

  const srcNode = offlineCtx.createBufferSource();
  srcNode.buffer = srcBuffer;
  srcNode.connect(offlineCtx.destination);
  srcNode.start(0);

  const rendered = await offlineCtx.startRendering();
  return rendered.getChannelData(0);
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Target audio format required by Whisper.cpp */
const WHISPER_SAMPLE_RATE = 16_000;

/** Maximum recording time in seconds (matches the backend limit). */
const MAX_RECORD_SECONDS = 15;

// ---------------------------------------------------------------------------
// Component types
// ---------------------------------------------------------------------------

interface CommandComposerProps {
  onSendMessage: (text: string) => void;
  disabled?: boolean;
  /** Called when the user starts recording (sets LISTENING state in parent). */
  onRecordingStart?: () => void;
  /** Called when transcription is in progress (sets THINKING state in parent). */
  onTranscribing?: () => void;
  /** Called when recording or transcription ends (success, error, or cancel). */
  onRecordingEnd?: () => void;
  /** Whether the live FastAPI backend is enabled (required for mic). */
  backendEnabled?: boolean;
  /** FastAPI base URL used for the /transcribe call. */
  backendUrl?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const CommandComposer: FC<CommandComposerProps> = ({
  onSendMessage,
  disabled = false,
  onRecordingStart,
  onTranscribing,
  onRecordingEnd,
  backendEnabled = true,
  backendUrl = 'http://localhost:8000',
}) => {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Mic state machine:
  //  'idle'         — not recording
  //  'recording'    — MediaRecorder active, user holding button
  //  'processing'   — uploading + transcribing
  type MicPhase = 'idle' | 'recording' | 'processing';
  const [micPhase, setMicPhase] = useState<MicPhase>('idle');
  const [micError, setMicError] = useState<string | null>(null);

  // Refs for cleanup
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Stale-completion guard: incremented on every new recording attempt.
  // A transcription callback only acts if its token matches the current one.
  const recordingTokenRef = useRef(0);

  // -----------------------------------------------------------------------
  // Cleanup helper — releases mic tracks and resets recorder state
  // -----------------------------------------------------------------------
  const releaseMedia = useCallback(() => {
    if (autoStopTimerRef.current !== null) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
    if (mediaRecorderRef.current) {
      try {
        if (mediaRecorderRef.current.state !== 'inactive') {
          mediaRecorderRef.current.stop();
        }
      } catch {
        // Best-effort stop
      }
      mediaRecorderRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    chunksRef.current = [];
  }, []);

  // Cleanup on unmount
  useEffect(() => () => releaseMedia(), [releaseMedia]);

  // -----------------------------------------------------------------------
  // Start recording (pointer-down / mouse-down)
  // -----------------------------------------------------------------------
  const startRecording = useCallback(async () => {
    if (micPhase !== 'idle' || disabled) return;

    // If the live backend is disabled, show a clear message and bail.
    if (!backendEnabled) {
      setMicError(
        'Voice input requires the live local backend. Enable it in Settings.'
      );
      return;
    }

    // Check browser support before requesting permission.
    if (
      !navigator.mediaDevices ||
      typeof navigator.mediaDevices.getUserMedia !== 'function'
    ) {
      setMicError(
        'Your browser does not support microphone access. Try Chrome or Edge.'
      );
      return;
    }

    setMicError(null);
    // Increment token so any in-flight transcription from a prior recording is discarded.
    recordingTokenRef.current += 1;
    const myToken = recordingTokenRef.current;

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: { ideal: WHISPER_SAMPLE_RATE },
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch (err) {
      const msg =
        err instanceof Error && err.name === 'NotAllowedError'
          ? 'Microphone permission denied. Allow microphone access and try again.'
          : 'Could not access the microphone. Check your system audio settings.';
      setMicError(msg);
      return;
    }

    // Guard: token may have changed while awaiting permission
    if (recordingTokenRef.current !== myToken) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }

    streamRef.current = stream;
    chunksRef.current = [];

    // Prefer webm/opus if available (best browser support); fallback to
    // whatever the browser offers.  We decode any format with AudioContext.
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
      ? 'audio/webm'
      : '';

    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) {
        chunksRef.current.push(e.data);
      }
    };

    recorder.onstop = async () => {
      // Capture chunks snapshot before async work
      const capturedChunks = [...chunksRef.current];
      chunksRef.current = [];
      releaseMedia();

      // Stale-completion guard
      if (recordingTokenRef.current !== myToken) return;

      if (capturedChunks.length === 0) {
        setMicPhase('idle');
        onRecordingEnd?.();
        return;
      }

      setMicPhase('processing');
      onTranscribing?.();

      const rawBlob = new Blob(capturedChunks, {
        type: recorder.mimeType || 'audio/webm',
      });

      try {
        // Decode (any browser format) → resample to 16 kHz mono → encode as WAV
        const pcm16k = await decodeAndResample(rawBlob, WHISPER_SAMPLE_RATE);
        const wavBuffer = float32ToWav(pcm16k, WHISPER_SAMPLE_RATE);
        const wavBlob = new Blob([wavBuffer], { type: 'audio/wav' });

        // Stale-completion guard after the decode (can take ~200ms)
        if (recordingTokenRef.current !== myToken) return;

        const transcript = await transcribeAudio(wavBlob, backendUrl);

        // Stale-completion guard after the HTTP round-trip
        if (recordingTokenRef.current !== myToken) return;

        // Populate the textarea and submit — reusing the existing send path
        onSendMessage(transcript);
        setInput('');
      } catch (err) {
        if (recordingTokenRef.current !== myToken) return;
        const msg =
          err instanceof Error ? err.message : 'Speech recognition failed.';
        setMicError(msg);
      } finally {
        if (recordingTokenRef.current === myToken) {
          setMicPhase('idle');
          onRecordingEnd?.();
        }
      }
    };

    recorder.onerror = () => {
      if (recordingTokenRef.current !== myToken) return;
      setMicError('Microphone recording error. Please try again.');
      releaseMedia();
      setMicPhase('idle');
      onRecordingEnd?.();
    };

    recorder.start(100); // collect data every 100 ms
    setMicPhase('recording');
    onRecordingStart?.();

    // Auto-stop after the maximum recording duration
    autoStopTimerRef.current = setTimeout(() => {
      stopRecording();
    }, MAX_RECORD_SECONDS * 1000);
  }, [
    micPhase,
    disabled,
    backendEnabled,
    backendUrl,
    releaseMedia,
    onSendMessage,
    onRecordingStart,
    onTranscribing,
    onRecordingEnd,
  ]);

  // -----------------------------------------------------------------------
  // Stop recording (pointer-up / mouse-up / cancel)
  // -----------------------------------------------------------------------
  const stopRecording = useCallback(() => {
    if (micPhase !== 'recording') return;
    if (
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state !== 'inactive'
    ) {
      mediaRecorderRef.current.stop(); // triggers recorder.onstop
    }
    if (autoStopTimerRef.current !== null) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
  }, [micPhase]);

  // Cancel mid-recording without submitting
  const cancelRecording = useCallback(() => {
    recordingTokenRef.current += 1; // invalidate any pending onstop callback
    releaseMedia();
    setMicPhase('idle');
    setMicError(null);
    onRecordingEnd?.();
  }, [releaseMedia, onRecordingEnd]);

  // -----------------------------------------------------------------------
  // Typed chat handlers (unchanged from original)
  // -----------------------------------------------------------------------
  const handleSend = () => {
    if (!input.trim() || disabled) return;
    onSendMessage(input.trim());
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [input]);

  // -----------------------------------------------------------------------
  // Derived display values
  // -----------------------------------------------------------------------
  const isRecording = micPhase === 'recording';
  const isProcessing = micPhase === 'processing';
  const micActive = isRecording || isProcessing;

  const micTitle = isRecording
    ? 'Release to transcribe'
    : isProcessing
    ? 'Processing speech…'
    : backendEnabled
    ? 'Hold to speak (push-to-talk)'
    : 'Enable local backend in Settings to use voice input';

  const micStatusLabel = isRecording
    ? '🎙 RECORDING — release to send'
    : isProcessing
    ? '⏳ TRANSCRIBING…'
    : null;

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        margin: '0 auto',
        padding: '6px 14px 12px',
        flexShrink: 0,
      }}
    >
      {/* Recording / processing status banner */}
      {micStatusLabel && (
        <div
          style={{
            position: 'absolute',
            top: '-34px',
            left: '24px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '4px 10px',
            borderRadius: 'var(--radius-full)',
            background: 'rgba(5, 12, 28, 0.95)',
            border: '1px solid var(--color-primary-border)',
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            color: 'var(--color-primary-bright)',
            boxShadow: '0 0 16px var(--glow-primary)',
            animation: 'messageReveal 0.25s ease',
            zIndex: 10,
          }}
        >
          <SparklesIcon style={{ width: '12px', height: '12px', color: '#00f0ff' }} />
          <span>{micStatusLabel}</span>
          {isRecording && (
            <button
              type="button"
              onClick={cancelRecording}
              style={{ color: 'var(--text-muted)', marginLeft: '4px', cursor: 'pointer' }}
              title="Cancel recording"
            >
              ✕
            </button>
          )}
        </div>
      )}

      {/* Error banner (auto-clears on next mic press) */}
      {micError && (
        <div
          style={{
            position: 'absolute',
            top: '-34px',
            left: '24px',
            right: '24px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '4px 10px',
            borderRadius: 'var(--radius-full)',
            background: 'rgba(28, 5, 5, 0.97)',
            border: '1px solid rgba(255, 80, 80, 0.5)',
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            color: '#ff6060',
            boxShadow: '0 0 12px rgba(255, 60, 60, 0.3)',
            animation: 'messageReveal 0.25s ease',
            zIndex: 10,
          }}
        >
          <span style={{ flex: 1 }}>⚠ {micError}</span>
          <button
            type="button"
            onClick={() => setMicError(null)}
            style={{ color: 'var(--text-muted)', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Main Composer Capsule */}
      <div
        className="hud-glass-panel hud-corner-brackets"
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: '8px',
          padding: '10px 14px',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--color-primary-border)',
          background: 'rgba(6, 14, 34, 0.85)',
          boxShadow:
            '0 8px 32px rgba(0, 0, 0, 0.6), 0 0 20px rgba(0, 240, 255, 0.08)',
          transition: 'all 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        {/* Terminal Prompt Prefix */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            fontFamily: 'var(--font-mono)',
            fontSize: '13px',
            fontWeight: 700,
            color: 'var(--color-primary)',
            paddingBottom: '8px',
            userSelect: 'none',
          }}
        >
          JARVIS://{'>'}
        </div>

        {/* Text Area */}
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask JARVIS anything… (e.g. 'Run system diagnostics' or 'Inspect workspace')"
          disabled={disabled || isProcessing}
          rows={1}
          style={{
            flex: 1,
            maxHeight: '120px',
            minHeight: '26px',
            resize: 'none',
            fontSize: '14px',
            lineHeight: '1.5',
            color: '#ffffff',
            padding: '4px 6px',
            fontFamily: 'var(--font-sans)',
            letterSpacing: '0.02em',
          }}
        />

        {/* Action Buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', paddingBottom: '2px' }}>
          {/* Push-to-talk mic button */}
          <button
            type="button"
            title={micTitle}
            aria-label={micTitle}
            aria-pressed={isRecording}
            disabled={isProcessing || disabled}
            onPointerDown={(e) => {
              // Use pointer events for reliable press/release across mouse and touch
              e.currentTarget.setPointerCapture(e.pointerId);
              startRecording();
            }}
            onPointerUp={() => stopRecording()}
            onPointerCancel={() => cancelRecording()}
            // Keyboard accessibility: Space/Enter to toggle
            onKeyDown={(e) => {
              if ((e.key === ' ' || e.key === 'Enter') && !isRecording && !isProcessing) {
                e.preventDefault();
                startRecording();
              }
            }}
            onKeyUp={(e) => {
              if ((e.key === ' ' || e.key === 'Enter') && isRecording) {
                e.preventDefault();
                stopRecording();
              }
            }}
            className="hud-mic-btn"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '38px',
              height: '38px',
              borderRadius: '50%',
              background: isRecording
                ? 'rgba(255, 60, 60, 0.30)'
                : micActive
                ? 'rgba(0, 240, 255, 0.25)'
                : 'rgba(0, 240, 255, 0.06)',
              border: isRecording
                ? '1px solid rgba(255, 80, 80, 0.9)'
                : micActive
                ? '1px solid var(--color-primary)'
                : '1px solid rgba(0, 240, 255, 0.25)',
              color: isRecording
                ? '#ff6060'
                : micActive
                ? '#00f0ff'
                : 'var(--text-secondary)',
              boxShadow: isRecording
                ? '0 0 18px rgba(255, 60, 60, 0.5)'
                : micActive
                ? '0 0 16px var(--glow-primary)'
                : 'none',
              cursor: isProcessing || disabled ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s ease',
              // Pulse animation while recording
              animation: isRecording ? 'pulse 1s ease-in-out infinite' : 'none',
            }}
          >
            <MicIcon style={{ width: '18px', height: '18px' }} />
          </button>

          {/* Send Button */}
          <button
            type="button"
            onClick={handleSend}
            disabled={!input.trim() || disabled || isProcessing}
            aria-label="Transmit Command to JARVIS"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '38px',
              height: '38px',
              borderRadius: '10px',
              background:
                input.trim() && !disabled && !isProcessing
                  ? 'linear-gradient(135deg, #00f0ff, #0284c7)'
                  : 'rgba(0, 240, 255, 0.08)',
              border:
                input.trim() && !disabled && !isProcessing
                  ? '1px solid #00f0ff'
                  : '1px solid rgba(0, 240, 255, 0.2)',
              color:
                input.trim() && !disabled && !isProcessing
                  ? '#030712'
                  : 'var(--text-muted)',
              boxShadow:
                input.trim() && !disabled && !isProcessing
                  ? '0 0 18px var(--glow-primary)'
                  : 'none',
              cursor:
                input.trim() && !disabled && !isProcessing
                  ? 'pointer'
                  : 'not-allowed',
              transition: 'all 0.25s ease',
            }}
          >
            <SendIcon style={{ width: '17px', height: '17px' }} />
          </button>
        </div>
      </div>

      {/* Keyboard Shortcut Hints Footer */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '6px 12px 0',
          fontFamily: 'var(--font-mono)',
          fontSize: '10px',
          color: 'var(--text-dim)',
          letterSpacing: '0.04em',
        }}
      >
        <span>[↵ TRANSMIT] • [⇧↵ NEWLINE] • [HOLD 🎙 SPEAK]</span>
        <span>100% PRIVATE • LOCAL OLLAMA / QWEN3 8B</span>
      </div>
    </div>
  );
};
