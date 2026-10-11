import { type FC, useState, useEffect } from 'react';
import { ArcReactorIcon, SettingsIcon, CpuIcon } from './Icons';

interface HeaderProps {
  modelName: string;
  onOpenSettings: () => void;
  status?: 'nominal' | 'optimizing' | 'offline';
}

export const Header: FC<HeaderProps> = ({ modelName, onOpenSettings, status = 'nominal' }) => {
  const [timeString, setTimeString] = useState<string>('');

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      const hours = String(now.getHours()).padStart(2, '0');
      const minutes = String(now.getMinutes()).padStart(2, '0');
      const seconds = String(now.getSeconds()).padStart(2, '0');
      setTimeString(`${hours}:${minutes}:${seconds}`);
    };
    updateTime();
    const timer = setInterval(updateTime, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 20px',
        borderBottom: '1px solid var(--color-primary-border)',
        background: 'rgba(3, 7, 18, 0.85)',
        backdropFilter: 'blur(16px)',
        position: 'sticky',
        top: 0,
        zIndex: 50,
      }}
    >
      {/* Left: J.A.R.V.I.S. Brand & System Status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '36px',
            height: '36px',
            borderRadius: '8px',
            background: 'rgba(0, 240, 255, 0.08)',
            border: '1px solid var(--color-primary-border)',
            color: 'var(--color-primary)',
            boxShadow: '0 0 12px var(--glow-primary)',
          }}
        >
          <ArcReactorIcon style={{ width: '22px', height: '22px' }} />
        </div>

        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span
              style={{
                fontFamily: 'var(--font-hud)',
                fontSize: '18px',
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#ffffff',
                textShadow: '0 0 10px var(--glow-primary)',
              }}
            >
              J.A.R.V.I.S.
            </span>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '10px',
                padding: '1px 6px',
                borderRadius: '4px',
                background: 'rgba(0, 240, 255, 0.12)',
                border: '1px solid rgba(0, 240, 255, 0.3)',
                color: 'var(--color-primary-bright)',
                letterSpacing: '0.05em',
              }}
            >
              MK-85 LOCAL
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '2px' }}>
            <span
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                backgroundColor: status === 'nominal' ? '#10b981' : '#f59e0b',
                boxShadow: status === 'nominal' ? '0 0 8px #10b981' : '0 0 8px #f59e0b',
                animation: 'beaconPulse 2.5s infinite',
              }}
            />
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '10px',
                color: 'var(--text-muted)',
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
              }}
            >
              SYSTEMS NOMINAL • 100% OFFLINE
            </span>
          </div>
        </div>
      </div>

      {/* Center: Live Military Clock & Frequency Wavelet (Hidden on small mobile) */}
      <div
        style={{
          display: 'none',
          alignItems: 'center',
          gap: '12px',
        }}
        className="hud-header-center"
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            padding: '4px 14px',
            borderRadius: '6px',
            background: 'rgba(0, 240, 255, 0.04)',
            border: '1px solid rgba(0, 240, 255, 0.15)',
          }}
        >
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '13px',
              fontWeight: 600,
              color: 'var(--color-primary-bright)',
              letterSpacing: '0.08em',
            }}
          >
            {timeString || '00:00:00'}
          </span>
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '9px',
              color: 'var(--text-muted)',
              letterSpacing: '0.08em',
            }}
          >
            LOCAL TIME // TELEMETRY
          </span>
        </div>

        {/* Ambient Telemetry Frequency Bars */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '3px',
            height: '20px',
            padding: '0 6px',
          }}
          title="Telemetry Sensor Wavelet"
        >
          {[8, 14, 18, 11, 16, 7].map((h, i) => (
            <span
              key={i}
              style={{
                width: '2px',
                height: `${h}px`,
                backgroundColor: 'var(--color-primary)',
                borderRadius: '1px',
                animation: `barOscillate 1.${(i % 4) + 2}s ease-in-out infinite alternate`,
                transformOrigin: 'bottom',
              }}
            />
          ))}
        </div>
      </div>

      {/* Right: Model Badge & Settings Trigger */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '5px 12px',
            borderRadius: 'var(--radius-full)',
            background: 'rgba(0, 240, 255, 0.06)',
            border: '1px solid var(--color-primary-border)',
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            color: 'var(--text-primary)',
          }}
          title="Local LLM Engine via Ollama"
        >
          <CpuIcon style={{ width: '13px', height: '13px', color: 'var(--color-primary)' }} />
          <span>{modelName}</span>
          <span style={{ color: 'var(--color-primary)', fontSize: '9px' }}>[OLLAMA]</span>
        </div>

        <button
          type="button"
          onClick={onOpenSettings}
          className="hud-glow-btn"
          aria-label="Open Neural System Settings"
          style={{ padding: '7px 11px', borderRadius: '8px' }}
        >
          <SettingsIcon style={{ width: '16px', height: '16px' }} />
          <span style={{ display: 'none' }} className="hud-settings-text">
            CONFIG
          </span>
        </button>
      </div>
    </header>
  );
};
