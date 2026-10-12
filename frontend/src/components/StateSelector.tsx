import type { FC } from 'react';
import type { CoreState } from '../types';

interface StateSelectorProps {
  currentState: CoreState;
  onSelectState: (state: CoreState) => void;
}

export const StateSelector: FC<StateSelectorProps> = ({ currentState, onSelectState }) => {
  const states: { id: CoreState; label: string }[] = [
    { id: 'idle',         label: 'IDLE' },
    { id: 'listening',    label: 'LISTEN' },
    { id: 'transcribing', label: 'XSCRIBE' },
    { id: 'thinking',     label: 'THINK' },
    { id: 'speaking',     label: 'SPEAK' },
  ];

  const getActiveGradient = (id: CoreState) => {
    switch (id) {
      case 'thinking':     return 'linear-gradient(135deg, rgba(168, 85, 247, 0.45), rgba(0, 240, 255, 0.25))';
      case 'transcribing': return 'linear-gradient(135deg, rgba(251, 191, 36, 0.4), rgba(245, 158, 11, 0.2))';
      default:             return 'linear-gradient(135deg, var(--color-primary-dim), rgba(2, 132, 199, 0.35))';
    }
  };

  const getActiveBorder = (id: CoreState) => {
    switch (id) {
      case 'thinking':     return '1px solid rgba(168, 85, 247, 0.7)';
      case 'transcribing': return '1px solid rgba(251, 191, 36, 0.65)';
      default:             return '1px solid var(--color-primary)';
    }
  };

  const getDotColor = (id: CoreState) => {
    switch (id) {
      case 'thinking':     return 'var(--color-accent-violet)';
      case 'transcribing': return '#fbbf24';
      default:             return 'var(--color-primary)';
    }
  };

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '3px',
        padding: '3px',
        background: 'rgba(5, 12, 28, 0.75)',
        backdropFilter: 'blur(12px)',
        border: '1px solid var(--color-primary-border)',
        borderRadius: 'var(--radius-full)',
        boxShadow: '0 4px 16px rgba(0, 0, 0, 0.4)',
      }}
    >
      {states.map((s) => {
        const isActive = currentState === s.id;
        return (
          <button
            key={s.id}
            type="button"
            onClick={() => onSelectState(s.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              fontFamily: 'var(--font-hud)',
              fontSize: '10px',
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: isActive ? '#ffffff' : 'var(--text-secondary)',
              background: isActive ? getActiveGradient(s.id) : 'transparent',
              border: isActive ? getActiveBorder(s.id) : '1px solid transparent',
              boxShadow: isActive ? '0 0 12px var(--glow-primary)' : 'none',
              transition: 'all 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
              cursor: 'pointer',
            }}
            aria-pressed={isActive}
            aria-label={`Set state to ${s.label}`}
          >
            <span
              style={{
                width: '5px',
                height: '5px',
                borderRadius: '50%',
                flexShrink: 0,
                backgroundColor: isActive ? getDotColor(s.id) : 'var(--text-muted)',
                boxShadow: isActive ? '0 0 6px currentColor' : 'none',
              }}
            />
            <span>{s.label}</span>
          </button>
        );
      })}
    </div>
  );
};
