'use client';

/**
 * HudInspector — right panel "agent dossier".
 *
 * Shows either an empty state or a full dossier for the selected agent / actor:
 * header, LLM config, system prompt, tools, skills, telemetry, sparkline,
 * connected systems (with deep-links), and connected peers.
 */

import styles from '../hud.module.css';
import {
  formatUptime,
  roleColor,
  type HudActor,
  type HudActorKind,
  type HudAgent,
  type HudLink,
} from '../lib/hud-data';

interface Props {
  agent: HudAgent | null;
  actor: HudActor | null;
  allAgents: HudAgent[];
  allActors: HudActor[];
  links: HudLink[];
}

export function HudInspector({ agent, actor, allAgents, allActors, links }: Props) {
  if (!agent && !actor) {
    return (
      <div className={`${styles.panel} ${styles.panelInspector}`}>
        <div className={styles.panelHeader}>
          <div className={styles.panelTitle}>AGENT DOSSIER</div>
          <div className={styles.panelId}>INSP-Δ</div>
        </div>
        <div className={styles.inspectorBody}>
          <div className={styles.inspHead}>
            <div className={styles.inspRole}>NO TARGET ACQUIRED</div>
            <div className={styles.inspName}>— — —</div>
            <div className={styles.inspId}>SELECT AN AGENT TO VIEW DOSSIER</div>
          </div>
        </div>
      </div>
    );
  }

  if (actor) {
    return (
      <div className={`${styles.panel} ${styles.panelInspector}`}>
        <div className={styles.panelHeader}>
          <div className={styles.panelTitle}>SERVICE DOSSIER</div>
          <div className={styles.panelId}>INSP-Δ</div>
        </div>
        <div className={styles.inspectorBody}>
          <ActorHead actor={actor} />
          <div className={styles.inspScroll}>
            <Section title="OVERVIEW">
              <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink)', lineHeight: 1.6 }}>{actor.desc}</p>
            </Section>
            <Section title="TELEMETRY">
              <div className={styles.kvGrid}>
                <div className="k">STATUS</div>
                <div className="v cyan">{actor.status.toUpperCase()}</div>
                <div className="k">VERSION</div>
                <div className="v">{actor.version}</div>
                <div className="k">THROUGHPUT</div>
                <div className="v">{actor.throughput}/s</div>
                <div className="k">ERROR RATE</div>
                <div className="v amber">{(actor.errorRate * 100).toFixed(2)}%</div>
                <div className="k">UPTIME</div>
                <div className="v">{formatUptime(actor.uptime)}</div>
                {actor.rows != null && (
                  <>
                    <div className="k">ROWS</div>
                    <div className="v">{actor.rows.toLocaleString()}</div>
                  </>
                )}
                {actor.qps != null && (
                  <>
                    <div className="k">QPS</div>
                    <div className="v">{actor.qps}</div>
                  </>
                )}
                {actor.eventsPerSec != null && (
                  <>
                    <div className="k">EVENTS/S</div>
                    <div className="v">{actor.eventsPerSec}</div>
                  </>
                )}
                {actor.activeJobs != null && (
                  <>
                    <div className="k">ACTIVE JOBS</div>
                    <div className="v">{actor.activeJobs}</div>
                  </>
                )}
                {actor.blockedToday != null && (
                  <>
                    <div className="k">BLOCKED/24H</div>
                    <div className="v red">{actor.blockedToday}</div>
                  </>
                )}
              </div>
            </Section>
            <Section title="CONNECTED AGENTS">
              <div className={styles.chipRow}>
                {agentsLinkedToActor(actor.id, allAgents, links).map((a) => (
                  <span
                    key={a.id}
                    className={styles.chip}
                    style={{ color: roleColor(a.role), borderColor: `${roleColor(a.role)}44` }}
                  >
                    {a.name}
                  </span>
                ))}
              </div>
            </Section>
          </div>
        </div>
      </div>
    );
  }

  // agent branch
  const a = agent!;
  const color = roleColor(a.role);
  const statusColor =
    a.status === 'alert'
      ? 'var(--red)'
      : a.status === 'thinking'
      ? 'var(--amber)'
      : a.status === 'idle'
      ? 'var(--ink-dim)'
      : 'var(--green)';
  const connected = connectedSystems(a.id, allActors, links);
  const peers = peersOf(a.id, allAgents, links);
  return (
    <div className={`${styles.panel} ${styles.panelInspector}`}>
      <div className={styles.panelHeader}>
        <div className={styles.panelTitle}>AGENT DOSSIER</div>
        <div className={styles.panelId}>INSP-Δ</div>
      </div>
      <div className={styles.inspectorBody}>
        <div className={styles.inspHead}>
          <div className={styles.inspRole} style={{ color }}>
            {a.role.toUpperCase()} · {a.version}
          </div>
          <div className={styles.inspName}>{a.name}</div>
          <div className={styles.inspId}>
            {a.id} // UPTIME {formatUptime(a.uptime)}
          </div>
          <div className={styles.inspStatus} style={{ color: statusColor }}>
            <span
              style={{
                display: 'inline-block',
                width: 6,
                height: 6,
                background: statusColor,
                boxShadow: `0 0 6px ${statusColor}`,
              }}
            />
            STATUS: {a.status.toUpperCase()}
          </div>
        </div>
        <div className={styles.inspScroll}>
          <Section title="OVERVIEW">
            <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink)', lineHeight: 1.6 }}>{a.desc}</p>
          </Section>
          <Section title="LLM CONFIG">
            <div className={styles.kvGrid}>
              <div className="k">MODEL</div>
              <div className="v cyan">{a.model}</div>
              <div className="k">TEMPERATURE</div>
              <div className="v">{a.temperature.toFixed(2)}</div>
              <div className="k">TOP_P</div>
              <div className="v">{a.topP.toFixed(2)}</div>
              <div className="k">MAX TOKENS</div>
              <div className="v">{a.maxTokens.toLocaleString()}</div>
              <div className="k">CTX USED</div>
              <div className="v">
                {a.contextSize.toLocaleString()} / {a.maxTokens.toLocaleString()}
              </div>
            </div>
          </Section>
          <Section title="SYSTEM PROMPT">
            <pre className={styles.promptBox}>{a.systemPrompt}</pre>
          </Section>
          <Section title={`TOOLS (${a.tools.length})`}>
            <div className={styles.chipRow}>
              {a.tools.map((t) => (
                <span key={t} className={styles.chip}>
                  {t}
                </span>
              ))}
            </div>
          </Section>
          <Section title={`SKILLS (${a.skills.length})`}>
            <div className={styles.chipRow}>
              {a.skills.map((s) => (
                <span key={s} className={`${styles.chip} ${styles.chipSkill}`}>
                  {s}
                </span>
              ))}
            </div>
          </Section>
          <Section title="TELEMETRY · 24H">
            <div className={styles.kvGrid}>
              <div className="k">CALLS</div>
              <div className="v cyan">{a.calls24h.toLocaleString()}</div>
              <div className="k">AVG LATENCY</div>
              <div className="v">{a.avgLatencyMs} ms</div>
              <div className="k">P99 LATENCY</div>
              <div className="v amber">{a.p99LatencyMs} ms</div>
              <div className="k">THROUGHPUT</div>
              <div className="v">{a.tokensPerSec} tok/s</div>
              <div className="k">ERROR RATE</div>
              <div className={`v ${a.errorRate > 0.02 ? 'amber' : 'green'}`}>
                {(a.errorRate * 100).toFixed(2)}%
              </div>
              <div className="k">COST TODAY</div>
              <div className="v">${a.costToday.toFixed(2)}</div>
            </div>
          </Section>
          <Section title="LATENCY PROFILE">
            <svg className={styles.spark} viewBox="0 0 300 48" preserveAspectRatio="none">
              <SparkPath seed={a.id} w={300} h={48} />
            </svg>
          </Section>
          <Section title="CONNECTED SYSTEMS">
            <div className={styles.sysLinks}>
              {connected.length === 0 ? (
                <div className={styles.sysEmpty}>NO SYSTEM LINKS</div>
              ) : (
                connected.map(({ actor, edge }) => (
                  <SystemCard key={actor.id} actor={actor} edge={edge} agentId={a.id} />
                ))
              )}
            </div>
          </Section>
          <Section title="CONNECTED PEERS">
            <div className={styles.chipRow}>
              {peers.map((p) => (
                <span
                  key={p.id}
                  className={styles.chip}
                  style={{
                    color: roleColor(p.role),
                    borderColor: `${roleColor(p.role)}44`,
                  }}
                >
                  {p.name}
                </span>
              ))}
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

function ActorHead({ actor }: { actor: HudActor }) {
  const color = actorColorFor(actor.kind);
  const statusColor =
    actor.status === 'alert'
      ? 'var(--red)'
      : actor.status === 'thinking'
      ? 'var(--amber)'
      : actor.status === 'idle'
      ? 'var(--ink-dim)'
      : 'var(--green)';
  return (
    <div className={styles.inspHead}>
      <div className={styles.inspRole} style={{ color }}>
        {actor.kind.toUpperCase()} · {actor.version}
      </div>
      <div className={styles.inspName}>{actor.name}</div>
      <div className={styles.inspId}>
        {actor.id} // UPTIME {formatUptime(actor.uptime)}
      </div>
      <div className={styles.inspStatus} style={{ color: statusColor }}>
        <span style={{ display: 'inline-block', width: 6, height: 6, background: statusColor, boxShadow: `0 0 6px ${statusColor}` }} />
        STATUS: {actor.status.toUpperCase()}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={styles.inspSection}>
      <div className={styles.inspSectionTitle}>{title}</div>
      {children}
    </div>
  );
}

function SystemCard({
  actor,
  edge,
  agentId,
}: {
  actor: HudActor;
  edge: string;
  agentId: string;
}) {
  const icons: Record<HudActorKind, string> = {
    memory: '◎',
    logs: '◆',
    learning: '▥',
    safety: '⬟',
    tools: '▤',
    infra: '▥',
  };
  const kindClass =
    actor.kind === 'memory' ? styles.sysCardMemory
    : actor.kind === 'logs' ? styles.sysCardLogs
    : actor.kind === 'learning' ? styles.sysCardLearning
    : actor.kind === 'safety' ? styles.sysCardSafety
    : actor.kind === 'tools' ? styles.sysCardTools
    : styles.sysCardInfra;
  const links = systemLinks(actor, agentId);
  const metric = actorMetric(actor);
  return (
    <div className={`${styles.sysCard} ${kindClass}`}>
      <div className={styles.sysCardHead}>
        <span className={styles.sysGlyph}>{icons[actor.kind]}</span>
        <div className={styles.sysCardTitle}>
          <div className={styles.sysName}>{actor.name}</div>
          <div className={styles.sysSub2}>
            {actor.kind.toUpperCase()} · via <span className={styles.sysEdge}>{edge}</span>
          </div>
        </div>
        <div className={styles.sysMetric}>{metric}</div>
      </div>
      <div className={styles.sysCardDesc}>{actor.desc}</div>
      <div className={styles.sysCardLinks}>
        {links.map((l) => (
          <a key={l.href} className={styles.sysLink} href={l.href} target="_blank" rel="noopener noreferrer" title={l.title}>
            <span className={styles.sysLinkIcon}>{l.glyph}</span>
            {l.label}
          </a>
        ))}
      </div>
    </div>
  );
}

function actorColorFor(kind: HudActorKind): string {
  const map: Record<HudActorKind, string> = {
    memory: 'var(--green)',
    logs: 'var(--violet)',
    learning: 'var(--amber)',
    safety: 'var(--red)',
    tools: 'var(--cyan)',
    infra: '#7fb7d6',
  };
  return map[kind];
}

function actorMetric(a: HudActor): string {
  if (a.kind === 'memory') return `${a.qps ?? 0} qps`;
  if (a.kind === 'logs') return `${a.eventsPerSec ?? 0}/s`;
  if (a.kind === 'learning') return `${a.activeJobs ?? 0} jobs`;
  if (a.kind === 'safety') return `${a.blockedToday ?? 0} blk/24h`;
  return a.version;
}

function peersOf(id: string, agents: HudAgent[], links: HudLink[]): HudAgent[] {
  const ids = new Set<string>();
  links.forEach((l) => {
    if (l.source === id) ids.add(l.target);
    if (l.target === id) ids.add(l.source);
  });
  return agents.filter((a) => ids.has(a.id));
}

function agentsLinkedToActor(
  actorId: string,
  agents: HudAgent[],
  links: HudLink[],
): HudAgent[] {
  const ids = new Set<string>();
  links.forEach((l) => {
    if (l.source === actorId) ids.add(l.target);
    if (l.target === actorId) ids.add(l.source);
  });
  return agents.filter((a) => ids.has(a.id));
}

function connectedSystems(
  agentId: string,
  actors: HudActor[],
  links: HudLink[],
): Array<{ actor: HudActor; edge: string }> {
  const actorIds = new Set(actors.map((a) => a.id));
  const out = new Map<string, { actor: HudActor; edge: string }>();
  links.forEach((l) => {
    let other: string | null = null;
    if (l.source === agentId && actorIds.has(l.target)) other = l.target;
    else if (l.target === agentId && actorIds.has(l.source)) other = l.source;
    if (other && !out.has(other)) {
      const actor = actors.find((x) => x.id === other);
      if (actor) out.set(other, { actor, edge: l.kind });
    }
  });
  return [...out.values()];
}

function systemLinks(actor: HudActor, agentId: string): Array<{
  glyph: string;
  label: string;
  title: string;
  href: string;
}> {
  const q = encodeURIComponent(agentId);
  const base = {
    grafana: 'https://grafana.sagewai.internal',
    victoria: 'https://logs.sagewai.internal',
    admin: '',
  };
  const L: Array<{ glyph: string; label: string; title: string; href: string }> = [];
  if (actor.kind === 'memory') {
    if (actor.id === 'VEC-STORE') {
      L.push({ glyph: '◎', label: 'OPEN VECTOR STORE', title: 'Admin → Memory → Vector', href: `${base.admin}/memory` });
      L.push({ glyph: '⌕', label: 'SEARCH EMBEDDINGS', title: 'Vector search console', href: `${base.admin}/memory?q=&agent=${q}` });
    } else {
      L.push({ glyph: '◎', label: 'OPEN GRAPH', title: 'Admin → Memory → Graph', href: `${base.admin}/memory` });
      L.push({ glyph: '⌕', label: 'QUERY ENTITIES', title: 'Entity browser', href: `${base.admin}/memory?agent=${q}` });
    }
    L.push({ glyph: '📈', label: 'GRAFANA · STORAGE', title: 'Grafana storage dashboard', href: `${base.grafana}/d/memory-storage?var-agent=${q}` });
  } else if (actor.kind === 'logs') {
    if (actor.id === 'RUN-LOGS') {
      L.push({ glyph: '◆', label: 'RUN LOGS', title: 'Admin → Runs', href: `${base.admin}/runs?agent=${q}` });
      L.push({ glyph: '▤', label: 'VICTORIALOGS', title: 'Live tail (VictoriaLogs)', href: `${base.victoria}/select/logsql?query=agent%3D%22${q}%22` });
    } else {
      L.push({ glyph: '◆', label: 'PROMPT HISTORY', title: 'Admin → Observability', href: `${base.admin}/observability?agent=${q}` });
      L.push({ glyph: '▤', label: 'VICTORIALOGS · PROMPTS', title: 'VictoriaLogs · prompt_events', href: `${base.victoria}/select/logsql?query=_stream%3Dprompt_events+AND+agent%3D%22${q}%22` });
    }
    L.push({ glyph: '📈', label: 'GRAFANA · EVENTS', title: 'Grafana event dashboard', href: `${base.grafana}/d/events?var-agent=${q}` });
  } else if (actor.kind === 'learning') {
    const seg = actor.id === 'CORPUS' ? 'context' : actor.id === 'FINETUNE' ? 'training' : 'eval';
    L.push({ glyph: '▥', label: `OPEN ${actor.name}`, title: 'Admin → Training', href: `${base.admin}/${seg}` });
    L.push({ glyph: '📈', label: 'GRAFANA · JOBS', title: 'Grafana training dashboard', href: `${base.grafana}/d/training?var-agent=${q}` });
  } else if (actor.kind === 'safety') {
    L.push({ glyph: '⬟', label: `OPEN ${actor.name}`, title: 'Admin → Safety', href: `${base.admin}/safety` });
    L.push({ glyph: '▤', label: 'VICTORIALOGS · POLICY', title: 'Policy violations (VictoriaLogs)', href: `${base.victoria}/select/logsql?query=_stream%3Dpolicy+AND+agent%3D%22${q}%22` });
    L.push({ glyph: '📈', label: 'GRAFANA · BLOCKS', title: 'Grafana policy dashboard', href: `${base.grafana}/d/safety?var-agent=${q}` });
  } else if (actor.kind === 'tools') {
    L.push({ glyph: '▤', label: `OPEN ${actor.name}`, title: 'Admin → Tools', href: `${base.admin}/tools` });
    L.push({ glyph: '📈', label: 'GRAFANA · ROUTING', title: 'Grafana router dashboard', href: `${base.grafana}/d/tools?var-agent=${q}` });
  } else if (actor.kind === 'infra') {
    L.push({ glyph: '📈', label: 'GRAFANA · TELEMETRY', title: 'Telemetry overview', href: `${base.grafana}/d/telemetry?var-agent=${q}` });
    L.push({ glyph: '▤', label: 'VICTORIALOGS · TRACES', title: 'VictoriaLogs traces', href: `${base.victoria}/select/logsql?query=trace.agent%3D%22${q}%22` });
  }
  return L;
}

function SparkPath({ seed, w, h }: { seed: string; w: number; h: number }) {
  let s = 0;
  for (const c of seed) s = ((s * 31 + c.charCodeAt(0)) & 0xffff);
  const n = 48;
  const pts: Array<[number, number]> = [];
  let v = 0.5;
  for (let i = 0; i < n; i++) {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    v = Math.max(0.05, Math.min(0.95, v + ((s % 1000) / 1000 - 0.5) * 0.25));
    pts.push([(i / (n - 1)) * w, h - v * h]);
  }
  const d = 'M' + pts.map((p) => p.map((x) => x.toFixed(1)).join(',')).join(' L');
  const area = `${d} L ${w},${h} L 0,${h} Z`;
  return (
    <>
      <path d={area} fill="rgba(107,231,255,0.12)" />
      <path
        d={d}
        fill="none"
        stroke="var(--cyan)"
        strokeWidth="1.2"
        style={{ filter: 'drop-shadow(0 0 3px rgba(107,231,255,0.6))' }}
      />
    </>
  );
}
