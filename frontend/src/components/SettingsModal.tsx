import { type FC, useEffect } from 'react';
import type { UserSettings, AccentColor, AnimationIntensity } from '../types';
import { CloseIcon, SettingsIcon, ArcReactorIcon, InfoIcon, CheckIcon } from './Icons';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  settings: UserSettings;
  onUpdateSettings: (newSettings: Partial<UserSettings>) => void;
}

export const SettingsModal: FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  settings,
  onUpdateSettings,
}) => {
  // Close on Escape key press
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      window.addEventListener('keydown', handleKey);
    }
    return () => window.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const modelOptions = [
    { id: 'Qwen3 8B', desc: 'Default local model (Balanced reasoning & speed)' },
    { id: 'Qwen 2.5 7B', desc: 'Fast general-purpose local model' },
    { id: 'Llama 3.2 3B', desc: 'Ultra-lightweight edge inference' },
    { id: 'DeepSeek R1 Distill', desc: 'Advanced step-by-step local reasoning' },
  ];

  const voiceOptions = [
    { id: 'en_US-lessac-medium', desc: 'Clear mid-tempo standard Piper voice (Default)' },
    { id: 'en_US-ryan-medium', desc: 'Authoritative deep cadence' },
    { id: 'en_GB-alan-medium', desc: 'Refined British butler inflection' },
  ];

  const accentOptions: { id: AccentColor; label: string; color: string }[] = [
    { id: 'cyan', label: 'Electric Cyan', color: '#00f0ff' },
    { id: 'arc-blue', label: 'Arc Reactor Blue', color: '#0284c7' },
    { id: 'violet', label: 'Quantum Violet', color: '#a855f7' },
    { id: 'amber', label: 'Reactor Amber', color: '#f59e0b' },
  ];

  const motionOptions: { id: AnimationIntensity; label: string; desc: string }[] = [
    { id: 'cinematic', label: 'Cinematic (Full)', desc: 'Smooth high-FPS orbital rotations, glows, & ripples' },
    { id: 'balanced', label: 'Balanced', desc: 'Standard motion and soft glow' },
    { id: 'reduced', label: 'Reduced Motion', desc: 'Disables rapid continuous spin for accessibility' },
  ];

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
        background: 'rgba(2, 6, 18, 0.78)',
        backdropFilter: 'blur(16px)',
        animation: 'modalBackdropFade 0.25s ease',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="hud-glass-panel hud-corner-brackets"
        style={{
          width: '100%',
          maxWidth: '620px',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          background: 'rgba(6, 14, 34, 0.94)',
          border: '1px solid var(--color-primary)',
          boxShadow: '0 0 40px rgba(0, 240, 255, 0.2), 0 20px 50px rgba(0, 0, 0, 0.8)',
          animation: 'modalContentPop 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
          overflow: 'hidden',
        }}
      >
        {/* Modal Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--color-primary-border)',
            background: 'rgba(0, 240, 255, 0.05)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <SettingsIcon style={{ width: '20px', height: '20px', color: 'var(--color-primary)' }} />
            <div>
              <h2
                style={{
                  fontFamily: 'var(--font-hud)',
                  fontSize: '18px',
                  fontWeight: 700,
                  letterSpacing: '0.1em',
                  color: '#ffffff',
                  margin: 0,
                }}
              >
                NEURAL SYSTEM CONFIGURATION
              </h2>
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '10px',
                  color: 'var(--text-muted)',
                }}
              >
                FRONTEND PREFERENCES • LOCAL STORAGE PERSISTENCE
              </span>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '32px',
              height: '32px',
              borderRadius: '6px',
              background: 'rgba(255, 255, 255, 0.05)',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'all 0.2s ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = '#ffffff';
              e.currentTarget.style.background = 'rgba(239, 68, 68, 0.3)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'var(--text-secondary)';
              e.currentTarget.style.background = 'rgba(255, 255, 255, 0.05)';
            }}
          >
            <CloseIcon style={{ width: '18px', height: '18px' }} />
          </button>
        </div>

        {/* Modal Body / Scrollable settings */}
        <div
          style={{
            padding: '20px',
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '24px',
          }}
        >
          {/* Section 1: Local Model Selection */}
          <div>
            <label
              style={{
                display: 'block',
                fontFamily: 'var(--font-hud)',
                fontSize: '13px',
                fontWeight: 700,
                letterSpacing: '0.08em',
                color: 'var(--color-primary-bright)',
                marginBottom: '8px',
                textTransform: 'uppercase',
              }}
            >
              [ 01 • ACTIVE LOCAL LLM ]
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '8px' }}>
              {modelOptions.map((m) => {
                const isSelected = settings.model === m.id;
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => onUpdateSettings({ model: m.id })}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '10px 14px',
                      borderRadius: '8px',
                      background: isSelected ? 'rgba(0, 240, 255, 0.15)' : 'rgba(0, 240, 255, 0.03)',
                      border: isSelected ? '1px solid var(--color-primary)' : '1px solid rgba(0, 240, 255, 0.12)',
                      cursor: 'pointer',
                      textAlign: 'left',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: 600, color: '#ffffff' }}>
                        {m.id}
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                        {m.desc}
                      </div>
                    </div>
                    {isSelected && <CheckIcon style={{ width: '16px', height: '16px', color: 'var(--color-primary)' }} />}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Section 2: Piper Neural Voice Model */}
          <div>
            <label
              style={{
                display: 'block',
                fontFamily: 'var(--font-hud)',
                fontSize: '13px',
                fontWeight: 700,
                letterSpacing: '0.08em',
                color: 'var(--color-primary-bright)',
                marginBottom: '8px',
                textTransform: 'uppercase',
              }}
            >
              [ 02 • PIPER TTS NEURAL VOICE ]
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '8px' }}>
              {voiceOptions.map((v) => {
                const isSelected = settings.voice === v.id;
                return (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => onUpdateSettings({ voice: v.id })}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '10px 14px',
                      borderRadius: '8px',
                      background: isSelected ? 'rgba(0, 240, 255, 0.15)' : 'rgba(0, 240, 255, 0.03)',
                      border: isSelected ? '1px solid var(--color-primary)' : '1px solid rgba(0, 240, 255, 0.12)',
                      cursor: 'pointer',
                      textAlign: 'left',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: 600, color: '#ffffff' }}>
                        {v.id}
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                        {v.desc}
                      </div>
                    </div>
                    {isSelected && <CheckIcon style={{ width: '16px', height: '16px', color: 'var(--color-primary)' }} />}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Section 3: Motion & Animation Intensity */}
          <div>
            <label
              style={{
                display: 'block',
                fontFamily: 'var(--font-hud)',
                fontSize: '13px',
                fontWeight: 700,
                letterSpacing: '0.08em',
                color: 'var(--color-primary-bright)',
                marginBottom: '8px',
                textTransform: 'uppercase',
              }}
            >
              [ 03 • MOTION DESIGN &amp; SENSORY TOLERANCE ]
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '8px' }}>
              {motionOptions.map((mot) => {
                const isSelected = settings.animationIntensity === mot.id;
                return (
                  <button
                    key={mot.id}
                    type="button"
                    onClick={() => onUpdateSettings({ animationIntensity: mot.id })}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '10px 14px',
                      borderRadius: '8px',
                      background: isSelected ? 'rgba(0, 240, 255, 0.15)' : 'rgba(0, 240, 255, 0.03)',
                      border: isSelected ? '1px solid var(--color-primary)' : '1px solid rgba(0, 240, 255, 0.12)',
                      cursor: 'pointer',
                      textAlign: 'left',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: 600, color: '#ffffff' }}>
                        {mot.label}
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                        {mot.desc}
                      </div>
                    </div>
                    {isSelected && <CheckIcon style={{ width: '16px', height: '16px', color: 'var(--color-primary)' }} />}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Section 4: Theme Accent Spectrum */}
          <div>
            <label
              style={{
                display: 'block',
                fontFamily: 'var(--font-hud)',
                fontSize: '13px',
                fontWeight: 700,
                letterSpacing: '0.08em',
                color: 'var(--color-primary-bright)',
                marginBottom: '8px',
                textTransform: 'uppercase',
              }}
            >
              [ 04 • HUD THEME ACCENT ]
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px' }}>
              {accentOptions.map((acc) => {
                const isSelected = settings.accentColor === acc.id;
                return (
                  <button
                    key={acc.id}
                    type="button"
                    onClick={() => onUpdateSettings({ accentColor: acc.id })}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                      padding: '10px 14px',
                      borderRadius: '8px',
                      background: isSelected ? 'rgba(0, 240, 255, 0.12)' : 'rgba(255, 255, 255, 0.03)',
                      border: isSelected ? '1px solid var(--color-primary)' : '1px solid rgba(255, 255, 255, 0.1)',
                      cursor: 'pointer',
                      textAlign: 'left',
                    }}
                  >
                    <span
                      style={{
                        width: '14px',
                        height: '14px',
                        borderRadius: '50%',
                        backgroundColor: acc.color,
                        boxShadow: `0 0 10px ${acc.color}`,
                      }}
                    />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: '#ffffff' }}>
                      {acc.label}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Section 5: Future FastAPI Backend Integration Bridge */}
          <div
            style={{
              padding: '14px',
              borderRadius: '8px',
              background: 'rgba(0, 240, 255, 0.04)',
              border: '1px solid var(--color-primary-border)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-primary-bright)', marginBottom: '8px' }}>
              <ArcReactorIcon style={{ width: '16px', height: '16px' }} />
              <span style={{ fontFamily: 'var(--font-hud)', fontSize: '13px', fontWeight: 700, letterSpacing: '0.08em' }}>
                FASTAPI BACKEND BRIDGE (PHASE 2 READY)
              </span>
            </div>
            <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '10px', lineHeight: '1.4' }}>
              When enabled, user prompts will transmit directly to your local FastAPI server (<code style={{ color: '#00f0ff' }}>{settings.fastApiUrl}/chat</code>). When disabled or offline, J.A.R.V.I.S. operates using the local intelligent simulation engine.
            </p>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
                color: '#ffffff',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={settings.useFastApiBackend}
                onChange={(e) => onUpdateSettings({ useFastApiBackend: e.target.checked })}
                style={{ width: '16px', height: '16px', accentColor: 'var(--color-primary)' }}
              />
              <span>Attempt live connection to FastAPI server</span>
            </label>
          </div>

          {/* Disclaimer / Guidance Note */}
          <div
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '8px',
              padding: '10px 12px',
              borderRadius: '6px',
              background: 'rgba(56, 189, 248, 0.05)',
              border: '1px solid rgba(56, 189, 248, 0.15)',
              color: 'var(--text-muted)',
              fontSize: '11px',
              lineHeight: '1.4',
            }}
          >
            <InfoIcon style={{ width: '15px', height: '15px', flexShrink: 0, marginTop: '2px', color: 'var(--color-primary-bright)' }} />
            <span>
              All settings are saved to your browser’s localStorage. No external telemetry or cloud configuration is ever transmitted.
            </span>
          </div>
        </div>

        {/* Modal Footer */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            padding: '12px 20px',
            borderTop: '1px solid var(--color-primary-border)',
            background: 'rgba(0, 0, 0, 0.3)',
          }}
        >
          <button
            type="button"
            onClick={onClose}
            className="hud-glow-btn"
            style={{ padding: '8px 20px' }}
          >
            CONFIRM &amp; RETURN
          </button>
        </div>
      </div>
    </div>
  );
};
