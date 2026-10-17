import { type FC, useMemo } from 'react';
import type { VoiceAssistantState } from '../types';
import './CompanionReactor.css';

interface CompanionReactorProps {
  state: VoiceAssistantState;
  size?: number;
  onClick?: () => void;
  interactive?: boolean;
}

export const CompanionReactor: FC<CompanionReactorProps> = ({
  state,
  size = 160,
  onClick,
  interactive = true,
}) => {
  // Generate 36 radial ticks for the outer technical HUD ring
  const outerTicks = useMemo(() => {
    return Array.from({ length: 36 }).map((_, i) => {
      const angle = (i * 360) / 36;
      const isMajor = i % 4 === 0;
      const rInner = isMajor ? 88 : 91;
      const rOuter = 95;
      const rad = (angle * Math.PI) / 180;
      const x1 = 100 + rInner * Math.cos(rad);
      const y1 = 100 + rInner * Math.sin(rad);
      const x2 = 100 + rOuter * Math.cos(rad);
      const y2 = 100 + rOuter * Math.sin(rad);
      return { angle, x1, y1, x2, y2, isMajor };
    });
  }, []);

  // Generate 10 power cell segments forming the classic Arc Reactor ring
  const powerCells = useMemo(() => {
    return Array.from({ length: 10 }).map((_, i) => {
      const angleDeg = (i * 360) / 10;
      const rad = (angleDeg * Math.PI) / 180;
      const r = 58;
      const cx = 100 + r * Math.cos(rad);
      const cy = 100 + r * Math.sin(rad);
      return { cx, cy, angleDeg, i };
    });
  }, []);

  // Generate 16 radial audio spectrum spokes for the speaking state
  const speakingSpokes = useMemo(() => {
    return Array.from({ length: 16 }).map((_, i) => {
      const angle = (i * 360) / 16;
      const rad = (angle * Math.PI) / 180;
      const baseR = 34;
      const length = 8 + ((i % 4) * 4);
      const x1 = 100 + baseR * Math.cos(rad);
      const y1 = 100 + baseR * Math.sin(rad);
      const x2 = 100 + (baseR + length) * Math.cos(rad);
      const y2 = 100 + (baseR + length) * Math.sin(rad);
      return { x1, y1, x2, y2, angle, i };
    });
  }, []);

  const stateClass = `cr-state-${state}`;

  const stateLabelMap: Record<VoiceAssistantState, string> = {
    stopped: 'Offline / Standby',
    wake_listening: 'Listening for "Hey Jarvis"',
    acknowledging: 'Acknowledging',
    recording_command: 'Recording Command',
    transcribing: 'Transcribing Audio',
    thinking: 'Neural Processing',
    speaking: 'Speaking Response',
    error: 'System Alert',
  };

  return (
    <div
      className={`companion-reactor-root ${stateClass} ${interactive ? 'is-interactive' : ''}`}
      onClick={interactive ? onClick : undefined}
      style={{ width: `${size}px`, height: `${size}px` }}
      title={`J.A.R.V.I.S. Core: ${stateLabelMap[state]}`}
      role="img"
      aria-label={`J.A.R.V.I.S. Arc Reactor Status: ${stateLabelMap[state]}`}
    >
      {/* Ambient Radial Bloom */}
      <div className={`cr-ambient-bloom ${stateClass}`} />

      {/* Main Multi-Layer SVG Arc Reactor */}
      <svg
        className={`cr-svg ${stateClass}`}
        viewBox="0 0 200 200"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          {/* Cyan Glow Filter */}
          <filter id="crCyanGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" result="blur1" />
            <feGaussianBlur stdDeviation="1" result="blur2" />
            <feMerge>
              <feMergeNode in="blur1" />
              <feMergeNode in="blur2" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Amber / Red Alert Filter */}
          <filter id="crAlertGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3.5" result="blurAlert" />
            <feMerge>
              <feMergeNode in="blurAlert" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Core Radial Gradient */}
          <radialGradient id="crCoreGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
            <stop offset="35%" stopColor="#00f0ff" stopOpacity="0.85" />
            <stop offset="70%" stopColor="#0284c7" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#050b18" stopOpacity="0" />
          </radialGradient>

          {/* Thinking Violet/Cyan Gradient */}
          <radialGradient id="crThinkingGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
            <stop offset="35%" stopColor="#a855f7" stopOpacity="0.85" />
            <stop offset="75%" stopColor="#00f0ff" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#050b18" stopOpacity="0" />
          </radialGradient>

          {/* Error Amber Gradient */}
          <radialGradient id="crErrorGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.9" />
            <stop offset="40%" stopColor="#f59e0b" stopOpacity="0.85" />
            <stop offset="80%" stopColor="#ef4444" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#050b18" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* 1. Outer Chassis Ring & Telemetry Ticks */}
        <g className="cr-chassis-layer">
          <circle
            cx="100"
            cy="100"
            r="96"
            className="cr-outer-boundary"
            stroke="rgba(0, 240, 255, 0.2)"
            strokeWidth="1"
          />
          {outerTicks.map((tick, i) => (
            <line
              key={i}
              x1={tick.x1}
              y1={tick.y1}
              x2={tick.x2}
              y2={tick.y2}
              className={`cr-tick ${tick.isMajor ? 'cr-tick-major' : 'cr-tick-minor'}`}
              stroke={tick.isMajor ? 'rgba(0, 240, 255, 0.8)' : 'rgba(0, 240, 255, 0.3)'}
              strokeWidth={tick.isMajor ? '1.5' : '1'}
            />
          ))}
        </g>

        {/* 2. Counter-Rotating Segmented Outer Armor Ring */}
        <g className="cr-outer-armor-ring">
          <circle
            cx="100"
            cy="100"
            r="82"
            stroke="rgba(0, 240, 255, 0.5)"
            strokeWidth="2.5"
            strokeDasharray="24 12 48 12 16 14"
            className="cr-armor-ring-dash"
            filter="url(#crCyanGlow)"
          />
          <circle
            cx="100"
            cy="100"
            r="74"
            stroke="rgba(56, 189, 248, 0.3)"
            strokeWidth="1"
            strokeDasharray="8 6 16 8"
            className="cr-counter-ring-dash"
          />
        </g>

        {/* 3. 10 Arc Reactor Magnetic Coil Nodes */}
        <g className="cr-reactor-cells">
          {powerCells.map((cell) => (
            <g
              key={cell.i}
              className={`cr-cell-group cr-cell-${cell.i}`}
              transform={`rotate(${cell.angleDeg} 100 100)`}
            >
              {/* Coil Base */}
              <rect
                x="94"
                y="40"
                width="12"
                height="10"
                rx="2"
                className="cr-coil-body"
                fill="rgba(0, 240, 255, 0.2)"
                stroke="rgba(0, 240, 255, 0.7)"
                strokeWidth="1.2"
              />
              {/* Core Emitter Pip */}
              <circle
                cx="100"
                cy="45"
                r="2"
                className="cr-coil-core"
                fill="#ffffff"
              />
            </g>
          ))}
        </g>

        {/* 4. Middle Energy Track (transcribing/thinking particle orbital) */}
        <g className="cr-orbital-track">
          <circle
            cx="100"
            cy="100"
            r="50"
            stroke="rgba(0, 240, 255, 0.35)"
            strokeWidth="1.2"
            strokeDasharray="4 6"
            className="cr-orbit-ring"
          />
          {/* Orbital Satellite Node */}
          <circle
            cx="100"
            cy="50"
            r="3"
            className="cr-orbital-node"
            fill="var(--color-primary, #00f0ff)"
            filter="url(#crCyanGlow)"
          />
        </g>

        {/* 5. Inner Core Iris & Active Waveform Blades (Speaking / Thinking / Recording) */}
        <g className="cr-iris-layer">
          <circle
            cx="100"
            cy="100"
            r="36"
            className="cr-iris-boundary"
            stroke="rgba(0, 240, 255, 0.6)"
            strokeWidth="1.5"
            fill="rgba(3, 7, 18, 0.75)"
          />

          {/* Radial Waveform Spokes (animated when speaking / listening) */}
          {state === 'speaking' && (
            <g className="cr-speaking-spokes">
              {speakingSpokes.map((spoke) => (
                <line
                  key={spoke.i}
                  x1={spoke.x1}
                  y1={spoke.y1}
                  x2={spoke.x2}
                  y2={spoke.y2}
                  className="cr-spectrum-line"
                  stroke="#00f0ff"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              ))}
            </g>
          )}

          {/* Central Reactor Glowing Orb */}
          <circle
            cx="100"
            cy="100"
            r="28"
            className="cr-central-orb"
            fill={
              state === 'error'
                ? 'url(#crErrorGrad)'
                : state === 'thinking'
                ? 'url(#crThinkingGrad)'
                : 'url(#crCoreGrad)'
            }
            filter={state === 'error' ? 'url(#crAlertGlow)' : 'url(#crCyanGlow)'}
          />

          {/* Core Center Glyphs depending on State */}
          {state === 'recording_command' && (
            <g className="cr-mic-glyph" transform="translate(93, 90)">
              {/* Mic Icon in Core */}
              <rect x="4" y="2" width="6" height="10" rx="3" fill="#ffffff" />
              <path
                d="M1 8C1 11.3137 3.68629 14 7 14C10.3137 14 13 11.3137 13 8"
                stroke="#ffffff"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
              <line x1="7" y1="14" x2="7" y2="18" stroke="#ffffff" strokeWidth="1.5" />
            </g>
          )}

          {state === 'error' && (
            <g className="cr-alert-glyph" transform="translate(92, 91)">
              {/* Alert Exclamation Triangle */}
              <path
                d="M8 2L15 15H1L8 2Z"
                fill="none"
                stroke="#ffffff"
                strokeWidth="1.5"
                strokeLinejoin="round"
              />
              <line x1="8" y1="6" x2="8" y2="10" stroke="#ffffff" strokeWidth="1.8" strokeLinecap="round" />
              <circle cx="8" cy="12.5" r="1" fill="#ffffff" />
            </g>
          )}

          {state === 'stopped' && (
            <g className="cr-standby-glyph" transform="translate(92, 92)">
              <circle cx="8" cy="8" r="6" stroke="rgba(148, 163, 184, 0.6)" strokeWidth="1.5" fill="none" />
              <line x1="8" y1="3" x2="8" y2="7" stroke="rgba(148, 163, 184, 0.8)" strokeWidth="1.5" strokeLinecap="round" />
            </g>
          )}

          {state === 'wake_listening' && (
            <g className="cr-radar-sweep">
              <circle cx="100" cy="100" r="14" fill="none" stroke="rgba(0, 240, 255, 0.4)" strokeWidth="1" />
              <circle cx="100" cy="100" r="6" fill="#00f0ff" className="cr-center-dot" />
            </g>
          )}
        </g>
      </svg>
    </div>
  );
};
