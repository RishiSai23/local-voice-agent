import { type FC, useRef, useEffect } from 'react';
import type { ChatMessage } from '../types';
import { ArcReactorIcon, TerminalIcon, CheckIcon } from './Icons';

interface ConversationFeedProps {
  messages: ChatMessage[];
  isThinking: boolean;
  onSelectPrompt?: (prompt: string) => void;
}

export const ConversationFeed: FC<ConversationFeedProps> = ({
  messages,
  isThinking,
  onSelectPrompt,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  return (
    <div
      className="jarvis-conversation-feed"
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        padding: '14px 16px',
        overflowY: 'auto',
      }}
    >
      {messages.length === 0 ? (
        /* Empty State */
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '24px 16px',
            textAlign: 'center',
            color: 'var(--text-muted)',
            gap: '10px',
            margin: 'auto 0',
          }}
        >
          <div
            style={{
              width: '42px',
              height: '42px',
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(0, 240, 255, 0.05)',
              border: '1px solid var(--color-primary-border)',
              color: 'var(--color-primary)',
            }}
          >
            <ArcReactorIcon style={{ width: '24px', height: '24px' }} />
          </div>
          <div>
            <h3
              style={{
                fontFamily: 'var(--font-hud)',
                fontSize: '16px',
                fontWeight: 600,
                color: 'var(--text-primary)',
                letterSpacing: '0.08em',
              }}
            >
              TRANSMISSION CHANNELS STANDING BY
            </h3>
            <p style={{ fontSize: '12px', marginTop: '2px' }}>
              Transmit a command below or select an operational protocol to begin.
            </p>
          </div>

          {onSelectPrompt && (
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '6px',
                justifyContent: 'center',
                marginTop: '8px',
              }}
            >
              {[
                'Run full system diagnostic',
                'What files are in the workspace?',
                'Recall memories from SQLite',
              ].map((prompt, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => onSelectPrompt(prompt)}
                  style={{
                    padding: '6px 14px',
                    borderRadius: 'var(--radius-full)',
                    background: 'rgba(0, 240, 255, 0.05)',
                    border: '1px solid var(--color-primary-border)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '11px',
                    color: 'var(--color-primary-bright)',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = 'rgba(0, 240, 255, 0.15)';
                    e.currentTarget.style.boxShadow = '0 0 10px var(--glow-primary)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'rgba(0, 240, 255, 0.05)';
                    e.currentTarget.style.boxShadow = 'none';
                  }}
                >
                  {prompt} →
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        /* Messages List */
        messages.map((msg) => {
          const isUser = msg.sender === 'user';
          return (
            <div
              key={msg.id}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: isUser ? 'flex-end' : 'flex-start',
                animation: 'messageReveal 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
                width: '100%',
              }}
            >
              {/* Message Header / Meta */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  marginBottom: '4px',
                  fontFamily: 'var(--font-mono)',
                  fontSize: '10px',
                  color: isUser ? 'var(--color-primary-bright)' : 'var(--text-muted)',
                }}
              >
                {!isUser && (
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      color: 'var(--color-primary)',
                      fontWeight: 600,
                    }}
                  >
                    <ArcReactorIcon style={{ width: '12px', height: '12px' }} />
                    J.A.R.V.I.S.
                  </span>
                )}
                <span>{msg.timestamp}</span>
                {msg.latencyMs && (
                  <span style={{ color: 'var(--color-primary-bright)' }}>
                    [{msg.latencyMs}ms]
                  </span>
                )}
                {msg.tokens && (
                  <span style={{ color: 'var(--text-dim)' }}>
                    • {msg.tokens} tokens
                  </span>
                )}
                {isUser && <span style={{ fontWeight: 600 }}>RISHI</span>}
              </div>

              {/* Message Bubble Container */}
              <div
                style={{
                  maxWidth: '85%',
                  padding: '12px 18px',
                  borderRadius: isUser ? '16px 16px 2px 16px' : '16px 16px 16px 2px',
                  background: isUser
                    ? 'linear-gradient(135deg, rgba(0, 240, 255, 0.18), rgba(2, 132, 199, 0.28))'
                    : 'rgba(6, 15, 36, 0.75)',
                  border: isUser
                    ? '1px solid rgba(0, 240, 255, 0.45)'
                    : '1px solid var(--color-primary-border)',
                  boxShadow: isUser
                    ? '0 4px 20px rgba(0, 240, 255, 0.15)'
                    : '0 4px 20px rgba(0, 0, 0, 0.4)',
                  backdropFilter: 'blur(12px)',
                  color: isUser ? '#ffffff' : 'var(--text-primary)',
                  fontSize: '14px',
                  lineHeight: '1.6',
                  letterSpacing: '0.02em',
                  wordBreak: 'break-word',
                  position: 'relative',
                }}
              >
                {/* Tool Execution Details (if any) */}
                {msg.toolCall && (
                  <div
                    style={{
                      marginBottom: '10px',
                      padding: '8px 12px',
                      borderRadius: '6px',
                      background: 'rgba(0, 0, 0, 0.45)',
                      border: '1px solid rgba(0, 240, 255, 0.25)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '11px',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        color: 'var(--color-primary-bright)',
                        fontWeight: 600,
                        marginBottom: '4px',
                      }}
                    >
                      <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <TerminalIcon style={{ width: '12px', height: '12px' }} />
                        EXECUTED: {msg.toolCall.name}
                      </span>
                      <span style={{ color: '#10b981', display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <CheckIcon style={{ width: '12px', height: '12px' }} />
                        SYNC
                      </span>
                    </div>
                    {msg.toolCall.details && (
                      <div style={{ color: 'var(--text-secondary)', fontSize: '10px', opacity: 0.9 }}>
                        {msg.toolCall.details}
                      </div>
                    )}
                  </div>
                )}

                {/* Main Message Content */}
                <div>{msg.text}</div>
              </div>
            </div>
          );
        })
      )}

      {/* Thinking Indicator */}
      {isThinking && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            animation: 'messageReveal 0.25s ease',
          }}
        >
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '10px',
              color: 'var(--color-primary-bright)',
              marginBottom: '4px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <ArcReactorIcon style={{ width: '12px', height: '12px', animation: 'spinClockwise 4s linear infinite' }} />
            <span>J.A.R.V.I.S. // PROCESSING NEURAL STREAM...</span>
          </div>
          <div
            style={{
              padding: '12px 18px',
              borderRadius: '16px 16px 16px 2px',
              background: 'rgba(6, 15, 36, 0.75)',
              border: '1px solid rgba(0, 240, 255, 0.4)',
              boxShadow: '0 0 16px var(--glow-primary)',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <span
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                backgroundColor: 'var(--color-primary)',
                animation: 'beaconPulse 1s infinite',
              }}
            />
            <span
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                backgroundColor: 'var(--color-primary-bright)',
                animation: 'beaconPulse 1s infinite 0.2s',
              }}
            />
            <span
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                backgroundColor: '#ffffff',
                animation: 'beaconPulse 1s infinite 0.4s',
              }}
            />
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '11px',
                color: 'var(--text-muted)',
                marginLeft: '6px',
                letterSpacing: '0.05em',
              }}
            >
              Synthesizing local response...
            </span>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
};
