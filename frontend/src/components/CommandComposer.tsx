import { type FC, useState, useRef, useEffect, type KeyboardEvent } from 'react';
import { SendIcon, MicIcon, SparklesIcon } from './Icons';

interface CommandComposerProps {
  onSendMessage: (text: string) => void;
  disabled?: boolean;
  onTriggerVoiceDemo?: () => void;
}

export const CommandComposer: FC<CommandComposerProps> = ({
  onSendMessage,
  disabled = false,
  onTriggerVoiceDemo,
}) => {
  const [input, setInput] = useState('');
  const [micActiveDemo, setMicActiveDemo] = useState(false);
  const [showMicHint, setShowMicHint] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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

  const handleMicClick = () => {
    setMicActiveDemo(true);
    setShowMicHint(true);
    onTriggerVoiceDemo?.();
    setTimeout(() => {
      setMicActiveDemo(false);
    }, 3500);
  };

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [input]);

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
      {/* Microphone Phase 2 Notice Banner (dismissible / auto-hides) */}
      {showMicHint && (
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
          <span>PHASE 2 PREVIEW: Microphone stream simulation active (Whisper engine integration stands by).</span>
          <button
            type="button"
            onClick={() => setShowMicHint(false)}
            style={{ color: 'var(--text-muted)', marginLeft: '4px', cursor: 'pointer' }}
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
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.6), 0 0 20px rgba(0, 240, 255, 0.08)',
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
          JARVIS://&gt;
        </div>

        {/* Text Area */}
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask JARVIS anything... (e.g. 'Run system diagnostics' or 'Inspect workspace')"
          disabled={disabled}
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
          {/* Future Voice Mic Placeholder Button */}
          <button
            type="button"
            onClick={handleMicClick}
            className="hud-mic-btn"
            title="Voice Input (Phase 2 Placeholder - Click to preview listening animation)"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '38px',
              height: '38px',
              borderRadius: '50%',
              background: micActiveDemo ? 'rgba(0, 240, 255, 0.25)' : 'rgba(0, 240, 255, 0.06)',
              border: micActiveDemo ? '1px solid var(--color-primary)' : '1px solid rgba(0, 240, 255, 0.25)',
              color: micActiveDemo ? '#00f0ff' : 'var(--text-secondary)',
              boxShadow: micActiveDemo ? '0 0 16px var(--glow-primary)' : 'none',
              cursor: 'pointer',
              transition: 'all 0.25s ease',
            }}
          >
            <MicIcon style={{ width: '18px', height: '18px' }} />
          </button>

          {/* Send Button */}
          <button
            type="button"
            onClick={handleSend}
            disabled={!input.trim() || disabled}
            aria-label="Transmit Command to JARVIS"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '38px',
              height: '38px',
              borderRadius: '10px',
              background: input.trim() && !disabled
                ? 'linear-gradient(135deg, #00f0ff, #0284c7)'
                : 'rgba(0, 240, 255, 0.08)',
              border: input.trim() && !disabled
                ? '1px solid #00f0ff'
                : '1px solid rgba(0, 240, 255, 0.2)',
              color: input.trim() && !disabled ? '#030712' : 'var(--text-muted)',
              boxShadow: input.trim() && !disabled
                ? '0 0 18px var(--glow-primary)'
                : 'none',
              cursor: input.trim() && !disabled ? 'pointer' : 'not-allowed',
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
        <span>[↵ TRANSMIT] • [⇧↵ NEWLINE]</span>
        <span>100% PRIVATE • LOCAL OLLAMA / QWEN3 8B</span>
      </div>
    </div>
  );
};
