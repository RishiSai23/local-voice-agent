import type { FC } from 'react';
import type { SystemTelemetry as SystemTelemetryType } from '../types';
import { CpuIcon, SoundWaveIcon, DatabaseIcon, MemoryBrainIcon } from './Icons';

interface SystemTelemetryProps {
  telemetry: SystemTelemetryType;
}

export const SystemTelemetry: FC<SystemTelemetryProps> = ({ telemetry }) => {
  return (
    <div
      className="hud-glass-panel hud-corner-brackets"
      style={{
        padding: '12px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        width: '100%',
      }}
    >
      {/* Telemetry Header */}
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
          [ TELEMETRY &amp; HARDWARE SENSORS ]
        </span>
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10px',
            color: '#10b981',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
        >
          <span
            style={{
              width: '5px',
              height: '5px',
              borderRadius: '50%',
              backgroundColor: '#10b981',
              boxShadow: '0 0 6px #10b981',
            }}
          />
          LOCAL SYNC
        </span>
      </div>

      {/* Metric Cards Grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
          gap: '8px',
        }}
      >
        {/* Metric 1: Local LLM */}
        <div
          style={{
            padding: '8px 10px',
            borderRadius: '6px',
            background: 'rgba(0, 240, 255, 0.04)',
            border: '1px solid rgba(0, 240, 255, 0.12)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--color-primary)', marginBottom: '2px' }}>
            <CpuIcon style={{ width: '13px', height: '13px' }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9.5px', color: 'var(--text-muted)' }}>LOCAL LLM</span>
          </div>
          <div style={{ fontFamily: 'var(--font-hud)', fontSize: '14px', fontWeight: 600, color: '#ffffff' }}>
            {telemetry.modelName}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '8.5px', color: 'var(--color-primary-bright)', marginTop: '1px' }}>
            {telemetry.modelContext}
          </div>
        </div>

        {/* Metric 2: Whisper ASR */}
        <div
          style={{
            padding: '8px 10px',
            borderRadius: '6px',
            background: 'rgba(0, 240, 255, 0.04)',
            border: '1px solid rgba(0, 240, 255, 0.12)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--color-primary)', marginBottom: '2px' }}>
            <SoundWaveIcon style={{ width: '13px', height: '13px' }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9.5px', color: 'var(--text-muted)' }}>WHISPER ASR</span>
          </div>
          <div style={{ fontFamily: 'var(--font-hud)', fontSize: '14px', fontWeight: 600, color: '#ffffff' }}>
            {telemetry.whisperEngine}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '8.5px', color: 'var(--text-secondary)', marginTop: '1px' }}>
            16 kHz Native C++
          </div>
        </div>

        {/* Metric 3: Piper Neural TTS */}
        <div
          style={{
            padding: '8px 10px',
            borderRadius: '6px',
            background: 'rgba(0, 240, 255, 0.04)',
            border: '1px solid rgba(0, 240, 255, 0.12)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--color-primary)', marginBottom: '2px' }}>
            <MemoryBrainIcon style={{ width: '13px', height: '13px' }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9.5px', color: 'var(--text-muted)' }}>PIPER TTS</span>
          </div>
          <div style={{ fontFamily: 'var(--font-hud)', fontSize: '14px', fontWeight: 600, color: '#ffffff' }}>
            {telemetry.ttsEngine}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '8.5px', color: 'var(--text-secondary)', marginTop: '1px' }}>
            ONNX Neural Engine
          </div>
        </div>

        {/* Metric 4: SQLite Memory Bank */}
        <div
          style={{
            padding: '8px 10px',
            borderRadius: '6px',
            background: 'rgba(0, 240, 255, 0.04)',
            border: '1px solid rgba(0, 240, 255, 0.12)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--color-primary)', marginBottom: '2px' }}>
            <DatabaseIcon style={{ width: '13px', height: '13px' }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9.5px', color: 'var(--text-muted)' }}>MEMORY BANK</span>
          </div>
          <div style={{ fontFamily: 'var(--font-hud)', fontSize: '14px', fontWeight: 600, color: '#ffffff' }}>
            {telemetry.activeMemoriesCount} Records
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '8.5px', color: 'var(--color-primary-bright)', marginTop: '1px' }}>
            SQLite Persistent
          </div>
        </div>
      </div>
    </div>
  );
};
