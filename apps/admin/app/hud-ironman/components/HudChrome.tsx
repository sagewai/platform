'use client';

/**
 * HUD chrome — all the non-graph visual elements.
 * Small, stateless, pixel-matched to the handoff.
 */

import type { CSSProperties } from 'react';
import styles from '../hud.module.css';
import {
  roleColor,
  type HudAgent,
  type HudProject,
  type HudRoleMeta,
  type HudTickerEvent,
  type HudWorkflow,
} from '../lib/hud-data';
import type { HudKpis } from '../lib/use-hud-data';

/* ─── Background layers (grid, hex, scanlines, vignette, corner frames) ─── */
export function HudBackground({ scanlinesOpacity }: { scanlinesOpacity: number }) {
  return (
    <>
      <div className={styles.bgGrid} />
      <svg className={styles.bgHex} viewBox="0 0 1920 1080" preserveAspectRatio="none">
        <defs>
          <pattern id="hex-pattern" x="0" y="0" width="60" height="52" patternUnits="userSpaceOnUse">
            <polygon points="30,2 58,18 58,50 30,66 2,50 2,18" fill="none" stroke="#6be7ff" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width="1920" height="1080" fill="url(#hex-pattern)" />
      </svg>
      <div className={styles.bgScan} style={{ opacity: scanlinesOpacity }} />
      <div className={styles.bgVignette} />
      <div className={`${styles.frameCorner} ${styles.frameCornerTl}`} />
      <div className={`${styles.frameCorner} ${styles.frameCornerTr}`} />
      <div className={`${styles.frameCorner} ${styles.frameCornerBl}`} />
      <div className={`${styles.frameCorner} ${styles.frameCornerBr}`} />
    </>
  );
}

/* ─── Topbar: logo + system readout + 5 KPIs ─── */
export function HudTopbar({ kpis, mode }: { kpis: HudKpis; mode: 'live' | 'demo' | 'loading' }) {
  const modeLabel =
    mode === 'live' ? 'LIVE' : mode === 'demo' ? 'DEMO · BACKEND OFFLINE' : 'SYNC';
  const modeColor =
    mode === 'live' ? 'var(--green)' : mode === 'demo' ? 'var(--amber)' : 'var(--ink-dim)';
  return (
    <div className={styles.topbar}>
      <div className={styles.logo}>
        <svg className={styles.logoMark} viewBox="0 0 52 52">
          <circle cx="26" cy="26" r="24" fill="none" stroke="#6be7ff" strokeWidth="1" />
          <circle cx="26" cy="26" r="18" fill="none" stroke="#6be7ff" strokeWidth="0.6" strokeDasharray="3 4">
            <animateTransform attributeName="transform" type="rotate" from="0 26 26" to="360 26 26" dur="12s" repeatCount="indefinite" />
          </circle>
          <polygon points="26,10 40,18 40,34 26,42 12,34 12,18" fill="none" stroke="#6be7ff" strokeWidth="1.2" />
          <polygon points="26,16 35,21 35,31 26,36 17,31 17,21" fill="#6be7ff" opacity="0.18" />
          <circle cx="26" cy="26" r="2.4" fill="#6be7ff" />
        </svg>
        <div className={styles.logoText}>
          <div
            style={{
              fontFamily: 'var(--cond)',
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: '0.32em',
              color: '#dff4ff',
              filter: 'drop-shadow(0 0 10px rgba(107,231,255,0.25))',
              lineHeight: 1,
            }}
          >
            SAGEWAI
          </div>
          <div className={styles.sysSub}>AGENTIC OPS · CMD.CTR · SAGEWAI CORE</div>
        </div>
      </div>
      <div className={styles.readout} style={{ position: 'relative', marginLeft: 30 }}>
        <div>
          <span className="hl">SEC</span> LINK OK · <span className="am">TLS·1.3</span> · AUTH <span className="hl">NOM</span>
        </div>
        <div style={{ marginTop: 4 }}>
          CLUSTER <span className="hl">US-WEST-3B</span> · NODE <span className="hl">N-0417</span> ·{' '}
          <span
            style={{
              display: 'inline-block',
              padding: '1px 6px',
              border: `1px solid ${modeColor}`,
              color: modeColor,
              letterSpacing: '0.22em',
              marginLeft: 4,
            }}
          >
            <span className={styles.blink} style={{ color: modeColor }}>●</span> {modeLabel}
          </span>
        </div>
      </div>
      <div className={styles.topbarKpis}>
        <Kpi label="ACTIVE AGENTS" value={String(kpis.activeAgents).padStart(2, '0')} trend="▲ 2 vs 1h" trendClass="trendUp" />
        <Kpi label="RUNS / 24H" value={kpis.runs24h.toLocaleString()} trend="▲ 18%" trendClass="trendUp" />
        <Kpi
          label="TOKENS / 24H"
          value={formatTokens(kpis.tokens24h)}
          valueClass="kpiValueGreen"
          trend="▲ 4%"
          trendClass="trendUp"
        />
        <Kpi
          label="SPEND TODAY"
          value={`$${kpis.spendToday.toFixed(2)}`}
          valueClass="kpiValueAmber"
          trend="budget 38%"
        />
        <Kpi
          label="ERRORS · 1H"
          value={String(kpis.errors1h).padStart(3, '0')}
          valueClass="kpiValueRed"
          trend="▼ 2"
          trendClass="trendDown"
        />
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  valueClass,
  trend,
  trendClass,
}: {
  label: string;
  value: string;
  valueClass?: 'kpiValueGreen' | 'kpiValueAmber' | 'kpiValueRed';
  trend: string;
  trendClass?: 'trendUp' | 'trendDown';
}) {
  const vc = valueClass ? styles[valueClass] : '';
  return (
    <div className={styles.kpi}>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={`${styles.kpiValue} ${vc}`}>{value}</div>
      <div className={styles.kpiTrend}>
        {trendClass ? <span className={styles[trendClass]}>{trend}</span> : trend}
      </div>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/* ─── Project tabs ─── */
export function HudProjectBar({
  projects,
  currentProjectId,
  agentCount,
  actorCount,
  inScopeAgents,
  totalCalls,
  avgErrRate,
  onSelect,
}: {
  projects: HudProject[];
  currentProjectId: string;
  agentCount: number;
  actorCount: number;
  inScopeAgents: HudAgent[];
  totalCalls: number;
  avgErrRate: number;
  onSelect: (id: string) => void;
}) {
  if (!projects.length) {
    return (
      <div className={styles.projectBar}>
        <div className={styles.projectBarLabel}>TENANT</div>
        <div className={styles.projectTabs} />
        <div className={styles.projectBarMeta}>
          <span>
            SCOPE <span className="v">—</span>
          </span>
        </div>
      </div>
    );
  }
  const current = projects.find((p) => p.id === currentProjectId) ?? projects[0];
  return (
    <div className={styles.projectBar}>
      <div className={styles.projectBarLabel}>TENANT</div>
      <div className={styles.projectTabs}>
        {projects.map((p) => {
          const all = p.id === 'prj-all';
          const aCount = all ? agentCount : inScopeAgents.filter((a) => (a.projects ?? []).includes(p.id)).length;
          const active = p.id === currentProjectId;
          const style = { ['--proj-col' as string]: p.color } as CSSProperties;
          return (
            <button
              key={p.id}
              type="button"
              className={`${styles.projectTab} ${active ? styles.projectTabActive : ''}`}
              style={style}
              onClick={() => onSelect(p.id)}
            >
              <span className={styles.pCode}>{p.code}</span>
              <span className={styles.pName}>{p.name}</span>
              <span className={styles.pCount}>
                {aCount}A · {all ? actorCount : actorCount}S
              </span>
            </button>
          );
        })}
      </div>
      <div className={styles.projectBarMeta}>
        <span>
          SCOPE{' '}
          <span className="v" style={{ color: current.color }}>
            {current.code}
          </span>
        </span>
        <span>
          AGENTS <span className="v">{inScopeAgents.length}</span>
        </span>
        <span>
          SERVICES <span className="v">{actorCount}</span>
        </span>
        <span>
          CALLS/24H <span className="v">{totalCalls.toLocaleString()}</span>
        </span>
        <span>
          ERR <span className="v">{(avgErrRate * 100).toFixed(2)}%</span>
        </span>
      </div>
    </div>
  );
}

/* ─── Agent roster ─── */
export function HudRoster({
  agents,
  projects,
  selectedId,
  currentProjectId,
  onSelect,
}: {
  agents: HudAgent[];
  projects: HudProject[];
  selectedId: string | null;
  currentProjectId: string;
  onSelect: (a: HudAgent) => void;
}) {
  return (
    <div className={`${styles.panel} ${styles.panelRoster}`}>
      <div className={styles.panelHeader}>
        <div className={styles.panelTitle}>AGENT ROSTER</div>
        <div className={styles.panelId}>
          {String(agents.length).padStart(2, '0')} UNITS
        </div>
      </div>
      <div className={styles.rosterScroll}>
        {agents.length === 0 && (
          <div
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 10.5,
              color: 'var(--ink-faint)',
              letterSpacing: '0.22em',
              textAlign: 'center',
              padding: '18px 12px',
              border: '1px dashed var(--ink-faint)',
              lineHeight: 1.7,
            }}
          >
            NO AGENTS REGISTERED
            <br />
            <span style={{ color: 'var(--ink-dim)', letterSpacing: '0.14em' }}>
              create one in /agents to populate the mesh
            </span>
          </div>
        )}
        {agents.map((a) => {
          const color = roleColor(a.role);
          const inScope = currentProjectId === 'prj-all' || (a.projects ?? []).includes(currentProjectId);
          const dotMod =
            a.status === 'idle'
              ? styles.agentDotIdle
              : a.status === 'thinking'
              ? styles.agentDotThinking
              : a.status === 'alert'
              ? styles.agentDotAlert
              : '';
          return (
            <div
              key={a.id}
              className={`${styles.agentRow} ${a.id === selectedId ? styles.agentRowSelected : ''} ${
                !inScope ? styles.agentRowOutOfScope : ''
              }`}
              onClick={() => onSelect(a)}
            >
              <span
                className={`${styles.agentDot} ${dotMod}`}
                style={{ background: color, color: color as string }}
              />
              <div>
                <div className={styles.agentName}>{a.name}</div>
                <div className={styles.agentRole}>
                  {a.role.toUpperCase()} · {a.model}
                </div>
                <div className={styles.agentProjs}>
                  {(a.projects ?? []).map((pid) => {
                    const p = projects.find((x) => x.id === pid);
                    if (!p) return null;
                    return (
                      <span
                        key={pid}
                        className={styles.projDot}
                        title={p.name}
                        style={{ background: p.color, color: p.color }}
                      />
                    );
                  })}
                </div>
              </div>
              <div className={styles.agentMeta}>
                {a.calls24h.toLocaleString()}
                <br />
                <span style={{ opacity: 0.6 }}>{a.avgLatencyMs}ms</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Workflows list ─── */
export function HudWorkflows({
  workflows,
  agents,
  actors,
  currentProjectId,
  activeWorkflowId,
  onToggle,
}: {
  workflows: HudWorkflow[];
  agents: HudAgent[];
  actors: { id: string; projects?: string[] }[];
  currentProjectId: string;
  activeWorkflowId: string | null;
  onToggle: (wf: HudWorkflow) => void;
}) {
  const allNodes = new Map([...agents, ...actors].map((n) => [n.id, n as { projects?: string[] }]));
  const inScope = (id: string) =>
    currentProjectId === 'prj-all' || (allNodes.get(id)?.projects ?? []).includes(currentProjectId);
  return (
    <div className={`${styles.panel} ${styles.panelWorkflows}`}>
      <div className={styles.panelHeader}>
        <div className={styles.panelTitle}>WORKFLOWS</div>
        <div className={styles.panelId}>
          {String(workflows.length).padStart(2, '0')} PIPELINES
        </div>
      </div>
      <div className={styles.wfScroll}>
        {workflows.length === 0 && (
          <div
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 10.5,
              color: 'var(--ink-faint)',
              letterSpacing: '0.22em',
              textAlign: 'center',
              padding: '18px 12px',
              border: '1px dashed var(--ink-faint)',
              lineHeight: 1.7,
            }}
          >
            NO WORKFLOWS SAVED
            <br />
            <span style={{ color: 'var(--ink-dim)', letterSpacing: '0.14em' }}>
              /workflows to assemble a pipeline
            </span>
          </div>
        )}
        {workflows.map((wf) => {
          const anyOut = wf.path.some((id) => !inScope(id));
          return (
            <div
              key={wf.id}
              className={`${styles.wfRow} ${wf.id === activeWorkflowId ? styles.wfRowSelected : ''} ${
                anyOut ? styles.wfRowOutOfScope : ''
              }`}
              onClick={() => onToggle(wf)}
            >
              <div className={styles.wfId}>{wf.id}</div>
              <div className={styles.wfStatus}>ACTIVE</div>
              <div className={styles.wfName}>{wf.name}</div>
              <div className={styles.wfPathMini}>{wf.path.join(' → ')}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Event ticker ─── */
export function HudTicker({ events }: { events: HudTickerEvent[] }) {
  return (
    <div className={`${styles.panel} ${styles.panelTicker}`}>
      <div className={styles.panelHeader}>
        <div className={styles.panelTitle}>EVENT STREAM</div>
        <div className={styles.panelId}>TAIL -F /var/sagewai/events.log</div>
      </div>
      <div className={styles.tickerBody}>
        {events.map((e) => {
          const tagClass = tagToClass(e.tag);
          const ts = new Date(e.ts);
          const h = String(ts.getHours()).padStart(2, '0');
          const m = String(ts.getMinutes()).padStart(2, '0');
          const s = String(ts.getSeconds()).padStart(2, '0');
          const ms = String(ts.getMilliseconds()).padStart(3, '0');
          return (
            <div key={`${e.ts}-${e.from}-${e.to}-${e.tag}`} className={styles.tickLine}>
              <span className={styles.tickTime}>
                {h}:{m}:{s}.{ms}
              </span>{' '}
              <span className={`${styles.tickTag} ${tagClass}`}>
                [{e.tag.padEnd(4, ' ')}]
              </span>{' '}
              <span className={styles.tickFrom}>{e.from}</span> →{' '}
              <span className={styles.tickTo}>{e.to}</span>{' '}
              <span className={styles.tickMsg}>{e.msg}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function tagToClass(tag: HudTickerEvent['tag']): string {
  switch (tag) {
    case 'NOM': return styles.tickTagNom;
    case 'ACK': return styles.tickTagAck;
    case 'WARN': return styles.tickTagWarn;
    case 'ERR': return styles.tickTagErr;
    default: return styles.tickTagRx;
  }
}

/* ─── Graph surround: frame, brackets, reticle, HUD labels ─── */
export function HudGraphFrame({ empty = false }: { empty?: boolean }) {
  return (
    <>
      <div className={styles.graphFrame} />
      <div className={`${styles.graphFrameBracket} ${styles.graphFrameBracketTl}`} />
      <div className={`${styles.graphFrameBracket} ${styles.graphFrameBracketTr}`} />
      <div className={`${styles.graphFrameBracket} ${styles.graphFrameBracketBl}`} />
      <div className={`${styles.graphFrameBracket} ${styles.graphFrameBracketBr}`} />
      <div className={styles.graphHudLabel} style={{ top: 8, left: 12 }}>
        TOPOLOGY · LIVE MESH
      </div>
      <div className={styles.graphHudLabel} style={{ top: 8, right: 12 }}>
        [F1] DRAG NODES · [CLICK] INSPECT · [WF] HIGHLIGHT PATH
      </div>
      <div className={styles.graphHudLabel} style={{ bottom: 8, left: 12 }}>
        PARTICLES: <span style={{ color: 'var(--cyan)' }}>●●●</span> MSG/S
      </div>
      <div className={styles.graphHudLabel} style={{ bottom: 8, right: 12 }}>
        ZOOM 1.00× · LAT <span style={{ color: 'var(--cyan)' }}>&lt;24ms</span>
      </div>
      <div className={styles.reticle}>
        <svg viewBox="0 0 1080 810" style={{ width: '100%', height: '100%' }}>
          <g transform="translate(540,405)">
            <g className={styles.reticleOuter}>
              <circle r="360" fill="none" stroke="#6be7ff" strokeOpacity="0.08" strokeWidth="1" strokeDasharray="2 8" />
              <circle r="380" fill="none" stroke="#6be7ff" strokeOpacity="0.06" strokeWidth="1" strokeDasharray="40 8 4 8" />
            </g>
            <g className={styles.reticleInner}>
              <circle r="220" fill="none" stroke="#6be7ff" strokeOpacity="0.15" strokeWidth="1" strokeDasharray="6 6" />
              <circle r="240" fill="none" stroke="#6be7ff" strokeOpacity="0.10" strokeWidth="1" strokeDasharray="60 12 4 12" />
              <line x1="-260" y1="0" x2="-210" y2="0" stroke="#6be7ff" strokeOpacity="0.4" />
              <line x1="210" y1="0" x2="260" y2="0" stroke="#6be7ff" strokeOpacity="0.4" />
              <line x1="0" y1="-260" x2="0" y2="-210" stroke="#6be7ff" strokeOpacity="0.4" />
              <line x1="0" y1="210" x2="0" y2="260" stroke="#6be7ff" strokeOpacity="0.4" />
            </g>
          </g>
        </svg>
      </div>
      {empty && (
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%, -50%)',
            textAlign: 'center',
            pointerEvents: 'none',
            fontFamily: 'var(--mono)',
            letterSpacing: '0.28em',
          }}
        >
          <div style={{ color: 'var(--ink-dim)', fontSize: 11, marginBottom: 6 }}>
            · NO TOPOLOGY ·
          </div>
          <div style={{ color: 'var(--ink-faint)', fontSize: 10 }}>
            AWAITING AGENT REGISTRATION
          </div>
        </div>
      )}
    </>
  );
}

/* ─── Tweaks panel (edit mode) ─── */
export interface TweakValues {
  flowSpeed: number;
  msgDensity: number;
  scanlines: number;
  accentHue: number;
}

export function HudTweaks({
  open,
  values,
  onChange,
}: {
  open: boolean;
  values: TweakValues;
  onChange: (next: TweakValues) => void;
}) {
  const update = <K extends keyof TweakValues>(k: K, v: number) => {
    onChange({ ...values, [k]: v });
  };
  return (
    <div className={`${styles.panel} ${styles.tweaksPanel} ${open ? styles.tweaksPanelOpen : ''}`}>
      <div className={styles.panelHeader}>
        <div className={styles.panelTitle}>TWEAKS</div>
        <div className={styles.panelId}>CFG-Ω</div>
      </div>
      <div className={styles.panelBody}>
        <TwRow label="FLOW SPEED" min={0.2} max={3} step={0.1} value={values.flowSpeed} onChange={(v) => update('flowSpeed', v)} fmt={(v) => `${v.toFixed(1)}×`} />
        <TwRow label="MSG DENSITY" min={0.2} max={3} step={0.1} value={values.msgDensity} onChange={(v) => update('msgDensity', v)} fmt={(v) => `${v.toFixed(1)}×`} />
        <TwRow label="SCANLINES" min={0} max={1} step={0.01} value={values.scanlines} onChange={(v) => update('scanlines', v)} fmt={(v) => (v > 0.01 ? 'ON' : 'OFF')} />
        <TwRow label="ACCENT HUE" min={0} max={360} step={1} value={values.accentHue} onChange={(v) => update('accentHue', v)} fmt={(v) => `${Math.round(v)}°`} />
      </div>
    </div>
  );
}

function TwRow({
  label,
  min,
  max,
  step,
  value,
  onChange,
  fmt,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
  fmt: (v: number) => string;
}) {
  return (
    <div className={styles.twRow}>
      <label>{label}</label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
      <span className="v">{fmt(value)}</span>
    </div>
  );
}

// Unused export kept for API parity with the roles mapping in the handoff
export { roleColor as exportedRoleColor };
export type { HudRoleMeta };
