import { type FC, useMemo } from 'react';
import type { CoreState } from '../types';
import './AiCore.css';

interface AiCoreProps {
  state: CoreState;
  onCycleState?: () => void;
}

export const AiCore: FC<AiCoreProps> = ({ state, onCycleState }) => {
  // Generate 60 radial ticks for the outer technical HUD ring
  const ticks = useMemo(() => {
    return Array.from({ length: 60 }).map((_, i) => {
      const angle = (i * 360) / 60;
      const isMajor = i % 5 === 0;
      const rInner = isMajor ? 168 : 172;
      const rOuter = 178;
      const rad = (angle * Math.PI) / 180;
      const x1 = 200 + rInner * Math.cos(rad);
      const y1 = 200 + rInner * Math.sin(rad);
      const x2 = 200 + rOuter * Math.cos(rad);
      const y2 = 200 + rOuter * Math.sin(rad);
      return { angle, x1, y1, x2, y2, isMajor };
    });
  }, []);

  // Generate 24 radial waveform equalizer spokes for speaking/listening states
  const spectrumSpokes = useMemo(() => {
    return Array.from({ length: 24 }).map((_, i) => {
      const angle = (i * 360) / 24;
      const rad = (angle * Math.PI) / 180;
      const baseR = 92;
      const length = state === 'speaking'
        ? 12 + ((i % 4) * 5)
        : state === 'listening'
        ? 8 + ((i % 3) * 4)
        : 4;
      const x1 = 200 + baseR * Math.cos(rad);
      const y1 = 200 + baseR * Math.sin(rad);
      const x2 = 200 + (baseR + length) * Math.cos(rad);
      const y2 = 200 + (baseR + length) * Math.sin(rad);
      return { x1, y1, x2, y2, angle };
    });
  }, [state]);

  const stateClass = `state-${state}`;

  return (
    <div className="ai-core-container" onClick={onCycleState} title="Click to cycle intelligence states (Demo)">
      {/* Background Ambient Atmospheric Bloom */}
      <div className={`core-ambient-bloom ${stateClass}`} />

      {/* Main Multi-Layer SVG Arc Reactor */}
      <svg
        className={`ai-core-svg ${stateClass}`}
        viewBox="0 0 400 400"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        role="img"
        aria-label={`J.A.R.V.I.S. Neural Core (${state.toUpperCase()})`}
      >
        <defs>
          {/* Cyan Glow Filter */}
          <filter id="neonGlow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="4.5" result="blur1" />
            <feGaussianBlur stdDeviation="1.5" result="blur2" />
            <feMerge>
              <feMergeNode in="blur1" />
              <feMergeNode in="blur2" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Violet Flare Filter for Thinking state */}
          <filter id="violetGlow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="6" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Luminous Reactor Nucleus Gradient */}
          <radialGradient id="nucleusGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="1" />
            <stop offset="35%" stopColor="#7dd3fc" stopOpacity="0.95" />
            <stop offset="65%" stopColor={state === 'thinking' ? '#c084fc' : '#00f0ff'} stopOpacity="0.8" />
            <stop offset="100%" stopColor={state === 'thinking' ? '#7e22ce' : '#0369a1'} stopOpacity="0.1" />
          </radialGradient>

          {/* Inner Iris Gradient */}
          <radialGradient id="irisGrad" cx="50%" cy="50%" r="50%">
            <stop offset="40%" stopColor="rgba(0, 240, 255, 0.05)" />
            <stop offset="100%" stopColor="rgba(0, 240, 255, 0.22)" />
          </radialGradient>
        </defs>

        {/* --- LAYER 1: ACOUSTIC EXPANSION WAVES (Smooth Opacity Transition) --- */}
        <g className="core-acoustic-waves">
          <circle cx="200" cy="200" r="70" stroke="var(--color-primary)" strokeOpacity="0.8" fill="none" className="ripple-wave-1" />
          <circle cx="200" cy="200" r="55" stroke="var(--color-primary-bright)" strokeOpacity="0.7" fill="none" className="ripple-wave-2" />
          <circle cx="200" cy="200" r="40" stroke="var(--color-accent-violet)" strokeOpacity="0.6" fill="none" className="ripple-wave-3" />
        </g>

        {/* --- LAYER 2: OUTER TECHNICAL HUD RETICLE (Ring 1) --- */}
        <g className="ring-compass">
          {/* Technical Outer Circle */}
          <circle cx="200" cy="200" r="182" stroke="var(--color-primary)" strokeWidth="1" strokeOpacity="0.25" strokeDasharray="3 6" />
          <circle cx="200" cy="200" r="178" stroke="var(--color-primary-bright)" strokeWidth="1.5" strokeOpacity="0.5" />

          {/* Precision Degree Ticks */}
          {ticks.map((t, idx) => (
            <line
              key={idx}
              x1={t.x1}
              y1={t.y1}
              x2={t.x2}
              y2={t.y2}
              stroke={t.isMajor ? 'var(--color-primary)' : 'var(--color-primary-bright)'}
              strokeWidth={t.isMajor ? 1.75 : 0.8}
              strokeOpacity={t.isMajor ? 0.9 : 0.4}
            />
          ))}

          {/* Compass Micro-Notations */}
          <text x="200" y="15" fill="var(--color-primary)" fillOpacity="0.85" fontSize="8" fontFamily="var(--font-mono)" textAnchor="middle" letterSpacing="1">000°</text>
          <text x="388" y="203" fill="var(--color-primary)" fillOpacity="0.85" fontSize="8" fontFamily="var(--font-mono)" textAnchor="middle" letterSpacing="1">090°</text>
          <text x="200" y="392" fill="var(--color-primary)" fillOpacity="0.85" fontSize="8" fontFamily="var(--font-mono)" textAnchor="middle" letterSpacing="1">180°</text>
          <text x="12" y="203" fill="var(--color-primary)" fillOpacity="0.85" fontSize="8" fontFamily="var(--font-mono)" textAnchor="middle" letterSpacing="1">270°</text>
        </g>

        {/* --- LAYER 3A: SEGMENTED LUMINOUS ARCS (Ring 2) --- */}
        <g className="ring-arcs">
          {/* Segment A */}
          <path
            d="M 200 48 A 152 152 0 0 1 352 200"
            stroke="var(--color-primary)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray="25 15 50 15"
            filter="url(#neonGlow)"
          />
          {/* Segment B */}
          <path
            d="M 200 352 A 152 152 0 0 1 48 200"
            stroke={state === 'thinking' ? 'var(--color-accent-violet)' : 'var(--color-primary)'}
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray="40 20 20 20"
            filter={state === 'thinking' ? 'url(#violetGlow)' : 'url(#neonGlow)'}
          />
          {/* Segment C */}
          <path
            d="M 320 80 A 152 152 0 0 1 352 140"
            stroke="var(--color-primary-bright)"
            strokeWidth="2"
            strokeLinecap="round"
          />
          <path
            d="M 80 320 A 152 152 0 0 1 48 260"
            stroke="var(--color-primary-bright)"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </g>

        {/* --- LAYER 3B: QUANTUM ENERGY FLUX ACCELERATOR (Thinking State Overlay) --- */}
        <g className="ring-thinking-flux">
          <circle cx="200" cy="200" r="142" stroke="var(--color-accent-violet)" strokeWidth="2" strokeDasharray="25 15 8 15" filter="url(#violetGlow)" />
          <circle cx="200" cy="200" r="118" stroke="#c084fc" strokeWidth="1.5" strokeDasharray="18 36" filter="url(#violetGlow)" />
          <path d="M 200 58 L 200 78 M 342 200 L 322 200 M 200 342 L 200 322 M 58 200 L 78 200" stroke="#f3e8ff" strokeWidth="2" strokeLinecap="round" />
        </g>

        {/* --- LAYER 4: ORBITAL SATELLITES & QUANTUM NODES (Ring 3) --- */}
        <g className="ring-orbitals">
          <circle cx="200" cy="200" r="126" stroke="var(--color-primary)" strokeWidth="1" strokeOpacity="0.3" strokeDasharray="16 8" />
          
          {/* Orbital Diamond Node 1 */}
          <polygon
            points="200,70 205,74 200,78 195,74"
            fill="#ffffff"
            stroke="var(--color-primary)"
            strokeWidth="1.5"
            filter="url(#neonGlow)"
          />
          {/* Orbital Diamond Node 2 */}
          <polygon
            points="330,200 326,205 322,200 326,195"
            fill="#ffffff"
            stroke="var(--color-primary)"
            strokeWidth="1.5"
            filter="url(#neonGlow)"
          />
          {/* Orbital Diamond Node 3 */}
          <polygon
            points="200,330 195,326 200,322 205,326"
            fill="#ffffff"
            stroke="var(--color-primary)"
            strokeWidth="1.5"
            filter="url(#neonGlow)"
          />
          {/* Orbital Diamond Node 4 */}
          <polygon
            points="70,200 74,195 78,200 74,205"
            fill="#ffffff"
            stroke="var(--color-primary)"
            strokeWidth="1.5"
            filter="url(#neonGlow)"
          />
        </g>

        {/* --- LAYER 5: SPECTRUM EQUALIZER SPOKES --- */}
        <g className="ring-spectrum">
          <circle cx="200" cy="200" r="92" stroke="var(--color-primary)" strokeWidth="1.5" strokeOpacity="0.4" />
          {spectrumSpokes.map((s, idx) => (
            <line
              key={idx}
              x1={s.x1}
              y1={s.y1}
              x2={s.x2}
              y2={s.y2}
              stroke={state === 'thinking' ? 'var(--color-accent-violet)' : 'var(--color-primary-bright)'}
              strokeWidth="2.5"
              strokeLinecap="round"
              className="frequency-spoke"
              filter="url(#neonGlow)"
            />
          ))}
        </g>

        {/* --- LAYER 6: INNER GEOMETRIC STATOR & IRIS (Ring 5) --- */}
        <g className="ring-iris">
          <circle cx="200" cy="200" r="74" fill="url(#irisGrad)" stroke="var(--color-primary)" strokeWidth="1.5" strokeOpacity="0.6" strokeDasharray="12 4" />
          {/* Stator Tri-Segments */}
          <path d="M 200 134 L 200 148" stroke="var(--color-primary)" strokeWidth="3" strokeLinecap="round" />
          <path d="M 257 167 L 245 174" stroke="var(--color-primary)" strokeWidth="3" strokeLinecap="round" />
          <path d="M 257 233 L 245 226" stroke="var(--color-primary)" strokeWidth="3" strokeLinecap="round" />
          <path d="M 200 266 L 200 252" stroke="var(--color-primary)" strokeWidth="3" strokeLinecap="round" />
          <path d="M 143 233 L 155 226" stroke="var(--color-primary)" strokeWidth="3" strokeLinecap="round" />
          <path d="M 143 167 L 155 174" stroke="var(--color-primary)" strokeWidth="3" strokeLinecap="round" />
        </g>

        {/* --- LAYER 7: LUMINOUS ARC REACTOR NUCLEUS (Center) --- */}
        <g className="core-nucleus">
          {/* Plasma Sphere */}
          <circle
            cx="200"
            cy="200"
            r="44"
            fill="url(#nucleusGrad)"
            filter={state === 'thinking' ? 'url(#violetGlow)' : 'url(#neonGlow)'}
          />
          {/* Inner Geometric Lattice */}
          <polygon
            points="200,166 230,217 170,217"
            stroke="#ffffff"
            strokeWidth="1.5"
            strokeOpacity="0.9"
            fill="none"
          />
          <polygon
            points="200,234 170,183 230,183"
            stroke={state === 'thinking' ? '#f3e8ff' : '#7dd3fc'}
            strokeWidth="1.2"
            strokeOpacity="0.75"
            fill="none"
          />
          {/* Central High-Intensity Singularity Flare */}
          <circle cx="200" cy="200" r="14" fill="#ffffff" filter="url(#neonGlow)" />
          <circle cx="200" cy="200" r="6" fill="#ffffff" />
        </g>
      </svg>
    </div>
  );
};
