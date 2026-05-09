'use client';

import { useState } from 'react';
import { Loader2, Send } from 'lucide-react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { adminApi } from '@/utils/api';
import type { AutopilotGoalResponse } from '@/utils/types';
import { AutopilotPlanPreview } from './autopilot-plan-preview';

interface AutopilotGoalInputProps {
  onMissionApproved?: () => void;
  /** Pre-fill the goal text (e.g. from a sample-goal pill click). */
  initialGoal?: string;
}

export function AutopilotGoalInput({ onMissionApproved, initialGoal }: AutopilotGoalInputProps) {
  const [goal, setGoal] = useState(initialGoal ?? '');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AutopilotGoalResponse | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const reduced = useReducedMotion();
  const tx = reduced
    ? {}
    : {
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -8 },
        transition: { duration: 0.18 },
      };

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!goal.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setSelectedCandidateId(null);
    try {
      const response = await adminApi.submitAutopilotGoal(goal.trim());
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit goal.');
    } finally {
      setLoading(false);
    }
  }

  function handleRetry() {
    setRetrying(true);
    setResult(null);
    setRetrying(false);
  }

  function handleCancel() {
    setResult(null);
    setSelectedCandidateId(null);
  }

  function handleApproved() {
    setResult(null);
    setGoal('');
    setSelectedCandidateId(null);
    onMissionApproved?.();
  }

  const selectedCandidate = result?.candidates.find((c) => c.id === selectedCandidateId);

  return (
    <div className="space-y-4">
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Describe your goal in plain English… e.g. 'Summarise all Slack messages from last week'"
          disabled={loading}
          className="flex-1 px-4 py-2.5 rounded-lg border border-border bg-bg-surface text-sm outline-none focus:border-primary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <button
          type="submit"
          disabled={!goal.trim() || loading}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-white text-sm font-semibold border-none cursor-pointer hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? (
            <Loader2 size={15} className="animate-spin" />
          ) : (
            <Send size={15} />
          )}
          {loading ? 'Routing…' : 'Submit'}
        </button>
      </form>

      {error && (
        <div className="flex items-start gap-3 bg-error/5 border border-error/20 rounded-lg px-4 py-3">
          <p className="text-sm text-error m-0">{error}</p>
        </div>
      )}

      <AnimatePresence mode="popLayout" initial={false}>
        {result && result.routing_result === 'auto_routed' && result.blueprint && result.mission_id && (
          <motion.div key="auto-routed" data-testid="routing-preview" {...tx}>
            <AutopilotPlanPreview
              blueprint={result.blueprint}
              missionId={result.mission_id}
              onApproved={handleApproved}
              onCancel={handleCancel}
            />
          </motion.div>
        )}

        {result && result.routing_result === 'picker_needed' && (
          <motion.div key="picker" data-testid="routing-picker" {...tx} className="space-y-3">
          <p className="text-sm text-text-secondary m-0">
            Multiple blueprints could match your goal. Select the best fit:
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            {result.candidates.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                onClick={() => setSelectedCandidateId(candidate.id)}
                className={`text-left p-4 rounded-lg border-2 transition-colors cursor-pointer bg-bg-surface ${
                  selectedCandidateId === candidate.id
                    ? 'border-primary'
                    : 'border-border hover:border-primary/40'
                }`}
              >
                <p className="text-sm font-semibold text-text-primary m-0 mb-1">{candidate.title}</p>
                <p className="text-xs text-text-muted m-0">{candidate.category}</p>
                <span className="inline-block mt-2 text-[10px] font-semibold uppercase tracking-wide bg-bg-subtle text-text-secondary px-2 py-0.5 rounded">
                  {candidate.mode}
                </span>
              </button>
            ))}
          </div>
          {selectedCandidate && result.mission_id && (
            <AutopilotPlanPreview
              blueprint={selectedCandidate}
              missionId={result.mission_id}
              onApproved={handleApproved}
              onCancel={handleCancel}
            />
          )}
          </motion.div>
        )}

        {result && result.routing_result === 'synthesis_needed' && (
          <motion.div
            key="synthesis-needed"
            data-testid="routing-synthesis"
            {...tx}
            className="flex items-start gap-4 bg-bg-subtle border border-border rounded-lg px-4 py-4"
          >
            <div className="flex-1">
              <p className="text-sm font-medium text-text-primary m-0 mb-1">
                No matching blueprint found
              </p>
              <p className="text-sm text-text-secondary m-0">
                The service will generate a custom blueprint — this may take a moment.
              </p>
            </div>
            <button
              type="button"
              onClick={handleRetry}
              disabled={retrying}
              className="shrink-0 px-3 py-2 text-sm font-medium rounded-lg border border-border bg-bg-surface cursor-pointer hover:bg-bg-subtle transition-colors disabled:opacity-50"
            >
              {retrying ? 'Checking…' : 'Retry'}
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
