import { type FC, useMemo } from 'react';

interface GreetingProps {
  userName?: string;
  coreState: string;
}

export const Greeting: FC<GreetingProps> = ({ userName = 'RISHI', coreState }) => {
  const greetingText = useMemo(() => {
    const hour = new Date().getHours();
    if (hour < 12) return `GOOD MORNING, ${userName}`;
    if (hour < 17) return `GOOD AFTERNOON, ${userName}`;
    return `GOOD EVENING, ${userName}`;
  }, [userName]);

  const statusSubtext = useMemo(() => {
    switch (coreState) {
      case 'listening':
        return 'ACOUSTIC SENSORS ACTIVE • AWAITING SPEECH INPUT';
      case 'thinking':
        return 'NEURAL QUANTUM COMPUTE IN PROGRESS • SYNTHESIZING';
      case 'speaking':
        return 'PIPER TTS SPEECH STREAM ACTIVE • TRANSMITTING AUDIO';
      default:
        return 'ALL SYSTEMS STANDING BY • 100% PRIVATE & OFFLINE';
    }
  }, [coreState]);

  return (
    <div
      style={{
        textAlign: 'center',
        marginTop: '10px',
        marginBottom: '8px',
        animation: 'hudFadeIn 0.8s cubic-bezier(0.16, 1, 0.3, 1)',
      }}
    >
      <h1
        style={{
          fontFamily: 'var(--font-hud)',
          fontSize: 'clamp(18px, 2.2vw, 24px)',
          fontWeight: 700,
          letterSpacing: '0.12em',
          color: '#ffffff',
          textTransform: 'uppercase',
          margin: 0,
          textShadow: '0 0 14px var(--glow-primary)',
        }}
      >
        {greetingText}
      </h1>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '6px',
          marginTop: '4px',
        }}
      >
        <span
          style={{
            width: '5px',
            height: '5px',
            borderRadius: '50%',
            backgroundColor: coreState === 'thinking' ? 'var(--color-accent-violet)' : 'var(--color-primary)',
            boxShadow: `0 0 6px ${coreState === 'thinking' ? 'var(--color-accent-violet)' : 'var(--color-primary)'}`,
          }}
        />
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            fontWeight: 500,
            letterSpacing: '0.06em',
            color: 'var(--color-primary-bright)',
            textTransform: 'uppercase',
          }}
        >
          {statusSubtext}
        </span>
      </div>

      <p
        style={{
          fontFamily: 'var(--font-sans)',
          fontSize: '11.5px',
          color: 'var(--text-muted)',
          marginTop: '4px',
          letterSpacing: '0.03em',
        }}
      >
        “Your intelligence. Your machine. Your rules.”
      </p>
    </div>
  );
};
