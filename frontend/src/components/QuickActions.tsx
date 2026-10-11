import type { FC } from 'react';
import type { QuickActionItem } from '../types';
import { DiagnosticIcon, FileTreeIcon, MemoryBrainIcon, TerminalIcon } from './Icons';

interface QuickActionsProps {
  onTriggerAction: (prompt: string) => void;
  disabled?: boolean;
}

export const QuickActions: FC<QuickActionsProps> = ({ onTriggerAction, disabled = false }) => {
  const actions: QuickActionItem[] = [
    {
      id: 'diag',
      label: 'SYSTEM HEALTH',
      category: 'DIAGNOSTIC',
      description: 'Run CPU, RAM, & model latency checks',
      prompt: 'Run full system diagnostic and check hardware tolerances.',
      icon: 'diagnostic',
    },
    {
      id: 'files',
      label: 'WORKSPACE REPO',
      category: 'FILE SYSTEM',
      description: 'Index local files and models on disk',
      prompt: 'What files and models are in the assistant workspace?',
      icon: 'files',
    },
    {
      id: 'mem',
      label: 'SQLITE MEMORY',
      category: 'PERSISTENCE',
      description: 'Inspect active memory bank in SQLite',
      prompt: 'Access memory bank: recall stored preferences and active records.',
      icon: 'memory',
    },
    {
      id: 'dev',
      label: 'DEV PROTOCOL',
      category: 'AUTOMATION',
      description: 'Preview application execution hook',
      prompt: 'Launch developer application hook for local voice agent.',
      icon: 'apps',
    },
  ];

  const renderIcon = (iconType: QuickActionItem['icon']) => {
    switch (iconType) {
      case 'diagnostic':
        return <DiagnosticIcon style={{ width: '15px', height: '15px' }} />;
      case 'files':
        return <FileTreeIcon style={{ width: '15px', height: '15px' }} />;
      case 'memory':
        return <MemoryBrainIcon style={{ width: '15px', height: '15px' }} />;
      case 'apps':
        return <TerminalIcon style={{ width: '15px', height: '15px' }} />;
    }
  };

  return (
    <div
      className="hud-glass-panel hud-corner-brackets"
      style={{
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        width: '100%',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid rgba(0, 240, 255, 0.15)',
          paddingBottom: '6px',
        }}
      >
        <span
          style={{
            fontFamily: 'var(--font-hud)',
            fontSize: '12px',
            fontWeight: 700,
            letterSpacing: '0.1em',
            color: 'var(--color-primary-bright)',
            textTransform: 'uppercase',
          }}
        >
          [ OPERATIONAL PROTOCOLS ]
        </span>
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            color: 'var(--text-muted)',
          }}
        >
          DEMO HOOKS
        </span>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))',
          gap: '6px',
        }}
      >
        {actions.map((act) => (
          <button
            key={act.id}
            type="button"
            onClick={() => onTriggerAction(act.prompt)}
            disabled={disabled}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              padding: '8px 10px',
              borderRadius: '6px',
              background: 'rgba(0, 240, 255, 0.04)',
              border: '1px solid rgba(0, 240, 255, 0.14)',
              textAlign: 'left',
              cursor: disabled ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
            }}
            onMouseEnter={(e) => {
              if (disabled) return;
              e.currentTarget.style.background = 'rgba(0, 240, 255, 0.14)';
              e.currentTarget.style.borderColor = 'var(--color-primary)';
              e.currentTarget.style.boxShadow = '0 0 12px var(--glow-primary)';
              e.currentTarget.style.transform = 'translateY(-1px)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'rgba(0, 240, 255, 0.04)';
              e.currentTarget.style.borderColor = 'rgba(0, 240, 255, 0.14)';
              e.currentTarget.style.boxShadow = 'none';
              e.currentTarget.style.transform = 'translateY(0)';
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                color: 'var(--color-primary)',
                marginBottom: '4px',
              }}
            >
              {renderIcon(act.icon)}
              <span
                style={{
                  fontFamily: 'var(--font-hud)',
                  fontSize: '12px',
                  fontWeight: 700,
                  letterSpacing: '0.06em',
                  color: '#ffffff',
                }}
              >
                {act.label}
              </span>
            </div>
            <span
              style={{
                fontSize: '11px',
                color: 'var(--text-secondary)',
                lineHeight: '1.3',
              }}
            >
              {act.description}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
};
