import type { FC } from 'react';
import type { CoreState } from '../types';

interface StateSelectorProps {
  currentState: CoreState;
  onSelectState: (state: CoreState) => void;
}

export const StateSelector: FC<StateSelectorProps> = ({ currentState, onSelectState }) => {
  const states: { id: CoreState; label: string; tag: string }[] = [
    { id: 'idle', label: 'IDLE', tag: 'STBY' },
    { id: 'listening', label: 'LISTENING', tag: 'REC' },
    { id: 'thinking', label: 'THINKING', tag: 'PROC' },
    { id: 'speaking', label: 'SPEAKING', tag: 'AUDIO' },
  ];

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
              gap: '5px',
              padding: '4px 11px',
              borderRadius: 'var(--radius-full)',
              fontFamily: 'var(--font-hud)',
              fontSize: '11px',
              fontWeight: 600,
              letterSpacing: '0.07em',
              textTransform: 'uppercase',
              color: isActive ? '#ffffff' : 'var(--text-secondary)',
              background: isActive
                ? s.id === 'thinking'
                  ? 'linear-gradient(135deg, rgba(168, 85, 247, 0.45), rgba(0, 240, 255, 0.25))'
                  : 'linear-gradient(135deg, var(--color-primary-dim), rgba(2, 132, 199, 0.35))'
                : 'transparent',
              border: isActive
                ? s.id === 'thinking'
                  ? '1px solid rgba(168, 85, 247, 0.7)'
                  : '1px solid var(--color-primary)'
                : '1px solid transparent',
              boxShadow: isActive ? '0 0 12px var(--glow-primary)' : 'none',
              transition: 'all 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
              cursor: 'pointer',
            }}
          >
            <span
              style={{
                width: '5px',
                height: '5px',
                borderRadius: '50%',
                backgroundColor: isActive
                  ? s.id === 'thinking'
                    ? 'var(--color-accent-violet)'
                    : 'var(--color-primary)'
                  : 'var(--text-muted)',
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
