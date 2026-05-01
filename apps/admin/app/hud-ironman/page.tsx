'use client';

/**
 * /hud-ironman — mission-control HUD dashboard, admin-only.
 *
 * Design ported from Claude Design handoff (globe-loader/project/Agentic Platform HUD.html).
 * Layout is a fixed 1920×1080 "stage" scaled to fit the viewport, so it looks
 * identical at any resolution (including 4K office monitors).
 */

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated, silentRefresh } from '@/utils/auth';
import styles from './hud.module.css';
import {
  HudBackground,
  HudGraphFrame,
  HudProjectBar,
  HudRoster,
  HudTicker,
  HudTopbar,
  HudTweaks,
  HudWorkflows,
  type TweakValues,
} from './components/HudChrome';
import { HudGraph, type HudGraphHandle } from './components/HudGraph';
import { HudInspector } from './components/HudInspector';
import { useHudData } from './lib/use-hud-data';
import type { HudActor, HudAgent, HudWorkflow } from './lib/hud-data';

const TWEAK_STORAGE_KEY = 'sagewai.hud-ironman.tweaks';
const DEFAULT_TWEAKS: TweakValues = {
  flowSpeed: 1,
  msgDensity: 1,
  scanlines: 1,
  accentHue: 190,
};

export default function HudIronmanPage() {
  const router = useRouter();
  const [authReady, setAuthReady] = useState(false);

  // Auth guard — admin-only, behind sagewai_auth cookie.
  useEffect(() => {
    if (isAuthenticated()) {
      setAuthReady(true);
      return;
    }
    silentRefresh().then((token) => {
      if (token) setAuthReady(true);
      else router.replace('/login?next=/hud-ironman');
    });
  }, [router]);

  return authReady ? <HudStage /> : <HudAuthGate />;
}

function HudAuthGate() {
  return (
    <div
      style={{
        background: '#02060a',
        color: '#6a8a9c',
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'var(--font-jetbrains), ui-monospace, monospace',
        fontSize: 12,
        letterSpacing: '0.22em',
      }}
    >
      AUTHENTICATING · HUD-IRONMAN · STAND BY…
    </div>
  );
}

/* ─── Stage: 1920×1080 canvas scaled to viewport ─── */
function HudStage() {
  const router = useRouter();
  const scalerRef = useRef<HTMLDivElement | null>(null);
  const graphHandleRef = useRef<HudGraphHandle | null>(null);

  // Rescale on resize
  useEffect(() => {
    const scale = () => {
      const el = scalerRef.current;
      if (!el) return;
      const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
      const x = (window.innerWidth - 1920 * s) / 2;
      const y = (window.innerHeight - 1080 * s) / 2;
      el.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
    };
    scale();
    window.addEventListener('resize', scale);
    return () => window.removeEventListener('resize', scale);
  }, []);

  // Esc to exit
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') router.push('/');
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [router]);

  // Tweak panel toggle: ` (backtick) opens/closes; persisted via localStorage
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [tweaks, setTweaks] = useState<TweakValues>(DEFAULT_TWEAKS);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(TWEAK_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        setTweaks({
          flowSpeed: typeof parsed.flowSpeed === 'number' ? parsed.flowSpeed : DEFAULT_TWEAKS.flowSpeed,
          msgDensity: typeof parsed.msgDensity === 'number' ? parsed.msgDensity : DEFAULT_TWEAKS.msgDensity,
          scanlines: typeof parsed.scanlines === 'number' ? parsed.scanlines : DEFAULT_TWEAKS.scanlines,
          accentHue: typeof parsed.accentHue === 'number' ? parsed.accentHue : DEFAULT_TWEAKS.accentHue,
        });
      }
    } catch {
      // localStorage blocked — ignore
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(TWEAK_STORAGE_KEY, JSON.stringify(tweaks));
    } catch {
      /* ignore */
    }
  }, [tweaks]);

  // Ticker toggle — backtick key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === '`') setTweaksOpen((v) => !v);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // Push flow speed + density into the graph imperatively
  useEffect(() => {
    graphHandleRef.current?.setParams({ speed: tweaks.flowSpeed, density: tweaks.msgDensity });
  }, [tweaks.flowSpeed, tweaks.msgDensity]);

  // Live data
  const { agents, actors, projects, workflows, links, kpis, ticker, mode } = useHudData();
  // Actors are infrastructure decoration — they only make sense when agents exist
  // to connect to them. In a live but empty fleet we hide them too.
  const graphAgents = agents;
  const graphActors = mode === 'live' && agents.length === 0 ? [] : actors;
  const graphLinks = mode === 'live' && agents.length === 0 ? [] : links;
  const graphEmpty = graphAgents.length === 0 && graphActors.length === 0;

  // React-owned selection + filter state
  const [currentProjectId, setCurrentProjectId] = useState<string>('prj-all');
  const [selectedNode, setSelectedNode] = useState<HudAgent | HudActor | null>(null);
  const [activeWorkflowId, setActiveWorkflowId] = useState<string | null>(null);

  const selectedAgent = selectedNode?.nodeClass === 'agent' ? (selectedNode as HudAgent) : null;
  const selectedActor = selectedNode?.nodeClass === 'actor' ? (selectedNode as HudActor) : null;

  // Auto-select first agent on initial load
  useEffect(() => {
    if (!selectedNode && agents.length) {
      const preferred = agents.find((a) => a.role === 'orchestrator') ?? agents[0];
      setSelectedNode(preferred);
      graphHandleRef.current?.setSelected(preferred.id);
    }
  }, [agents, selectedNode]);

  // When the project filter changes, sync it to the graph
  useEffect(() => {
    graphHandleRef.current?.setProjectFilter(currentProjectId === 'prj-all' ? null : currentProjectId);
  }, [currentProjectId]);

  const handleSelectAgent = useCallback((a: HudAgent) => {
    setSelectedNode(a);
    graphHandleRef.current?.setSelected(a.id);
  }, []);

  const handleGraphSelect = useCallback((n: HudAgent | HudActor | null) => {
    setSelectedNode(n);
  }, []);

  const handleToggleWorkflow = useCallback((wf: HudWorkflow) => {
    setActiveWorkflowId((prev) => {
      const next = prev === wf.id ? null : wf.id;
      graphHandleRef.current?.highlightWorkflow(next ? wf : null);
      return next;
    });
  }, []);

  // Scope derivations
  const inScopeAgents = useMemo(
    () =>
      currentProjectId === 'prj-all'
        ? agents
        : agents.filter((a) => (a.projects ?? []).includes(currentProjectId)),
    [agents, currentProjectId],
  );
  const totalCalls = useMemo(() => inScopeAgents.reduce((s, a) => s + a.calls24h, 0), [inScopeAgents]);
  const avgErrRate = useMemo(() => {
    if (!inScopeAgents.length) return 0;
    return inScopeAgents.reduce((s, a) => s + a.errorRate, 0) / inScopeAgents.length;
  }, [inScopeAgents]);

  // Accent hue CSS variable
  const rootStyle: CSSProperties = {
    ['--cyan' as string]: `hsl(${tweaks.accentHue}, 100%, 70%)`,
  };

  return (
    <div className={styles.root} style={rootStyle}>
      <div ref={scalerRef} className={styles.scaler}>
        <div className={styles.stage}>
          <HudBackground scanlinesOpacity={tweaks.scanlines} />
          <HudTopbar kpis={kpis} mode={mode} />
          <HudProjectBar
            projects={projects}
            currentProjectId={currentProjectId}
            agentCount={agents.length}
            actorCount={actors.length}
            inScopeAgents={inScopeAgents}
            totalCalls={totalCalls}
            avgErrRate={avgErrRate}
            onSelect={setCurrentProjectId}
          />
          <HudRoster
            agents={agents}
            projects={projects}
            selectedId={selectedNode?.id ?? null}
            currentProjectId={currentProjectId}
            onSelect={handleSelectAgent}
          />
          <HudWorkflows
            workflows={workflows}
            agents={agents}
            actors={actors}
            currentProjectId={currentProjectId}
            activeWorkflowId={activeWorkflowId}
            onToggle={handleToggleWorkflow}
          />
          <div className={styles.graphArea}>
            <HudGraphFrame empty={graphEmpty} />
            <HudGraph
              ref={graphHandleRef}
              agents={graphAgents}
              actors={graphActors}
              links={graphLinks}
              onSelectAgent={handleGraphSelect}
            />
          </div>
          <HudInspector
            agent={selectedAgent}
            actor={selectedActor}
            allAgents={agents}
            allActors={actors}
            links={links}
          />
          <HudTicker events={ticker} />
          <HudTweaks open={tweaksOpen} values={tweaks} onChange={setTweaks} />
        </div>
      </div>
    </div>
  );
}
