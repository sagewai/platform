import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIONS,
  CANDIDATE_COMPARISON,
  DEFAULT_BUSINESS_ANSWERS,
  ML_TASK_CONTRACT,
  SAMPLE_S3_CONNECTION,
  STEPS,
  createInitialState,
  getCurrentStep,
  reduceWorkflow,
  selectChampion,
} from "./workflow.js";

function advance(actionTypes) {
  return actionTypes.reduce(
    (state, actionType) => reduceWorkflow(state, { type: actionType }),
    createInitialState(),
  );
}

function advanceWithAnswers() {
  let state = createInitialState();
  state = reduceWorkflow(state, { type: ACTIONS.CONNECT_SAMPLE_S3 });
  state = reduceWorkflow(state, { type: ACTIONS.CONFIRM_DATASET });
  state = reduceWorkflow(state, {
    type: ACTIONS.ANSWER_BUSINESS_QUESTIONS,
    answers: DEFAULT_BUSINESS_ANSWERS,
  });
  return state;
}

test("valid actions move through the full seven-step journey", () => {
  let state = createInitialState();

  assert.equal(getCurrentStep(state).id, STEPS[0].id);
  assert.equal(state.connection.status, "not-connected");

  state = reduceWorkflow(state, { type: ACTIONS.CONNECT_SAMPLE_S3 });
  assert.equal(getCurrentStep(state).id, "dataset-discovery");
  assert.deepEqual(state.connection.config, SAMPLE_S3_CONNECTION);
  assert.equal(state.dataset.candidateOutcomeColumn, "late_delivery");

  state = reduceWorkflow(state, { type: ACTIONS.CONFIRM_DATASET });
  assert.equal(getCurrentStep(state).id, "business-questions");

  state = reduceWorkflow(state, {
    type: ACTIONS.ANSWER_BUSINESS_QUESTIONS,
    answers: DEFAULT_BUSINESS_ANSWERS,
  });
  assert.equal(getCurrentStep(state).id, "task-contract");
  assert.equal(state.taskContract.task, ML_TASK_CONTRACT.task);

  state = reduceWorkflow(state, { type: ACTIONS.APPROVE_TASK_CONTRACT });
  assert.equal(getCurrentStep(state).id, "candidate-training");
  assert.equal(state.contractApproval.status, "approved");

  state = reduceWorkflow(state, { type: ACTIONS.RUN_CANDIDATE_TRAINING });
  assert.equal(getCurrentStep(state).id, "champion-evidence");
  assert.deepEqual(state.training.candidates, CANDIDATE_COMPARISON);
  assert.equal(state.training.championId, "calibrated-gbt");

  state = reduceWorkflow(state, { type: ACTIONS.ACCEPT_CHAMPION });
  assert.equal(getCurrentStep(state).id, "deploy-feedback-retrain");
  assert.equal(state.deployment.status, "ready");

  state = reduceWorkflow(state, { type: ACTIONS.DEPLOY_CHAMPION });
  assert.equal(state.deployment.status, "deployed");

  state = reduceWorkflow(state, { type: ACTIONS.CAPTURE_FEEDBACK });
  assert.equal(state.feedback.status, "captured");
  assert.equal(state.feedback.examples, 32);
});

test("invalid actions are rejected without mutating state", () => {
  const initial = createInitialState();

  assert.throws(
    () => reduceWorkflow(initial, { type: ACTIONS.CONFIRM_DATASET }),
    /requires step dataset-discovery/,
  );
  assert.deepEqual(initial, createInitialState());

  const questionsState = advance([
    ACTIONS.CONNECT_SAMPLE_S3,
    ACTIONS.CONFIRM_DATASET,
  ]);
  assert.throws(
    () =>
      reduceWorkflow(questionsState, {
        type: ACTIONS.ANSWER_BUSINESS_QUESTIONS,
        answers: { businessGoal: "" },
      }),
    /Missing required business answer: businessGoal/,
  );

  assert.throws(
    () => reduceWorkflow(questionsState, { type: ACTIONS.RUN_CANDIDATE_TRAINING }),
    /requires step candidate-training/,
  );

  const championState = reduceWorkflow(
    reduceWorkflow(advanceWithAnswers(), { type: ACTIONS.APPROVE_TASK_CONTRACT }),
    { type: ACTIONS.RUN_CANDIDATE_TRAINING },
  );
  assert.throws(
    () => reduceWorkflow(championState, { type: ACTIONS.REQUEST_MANUAL_RETRAIN }),
    /requires step deploy-feedback-retrain/,
  );

  assert.throws(
    () => reduceWorkflow(createInitialState(), { type: "LAUNCH_REAL_AWS_JOB" }),
    /Unsupported workflow action/,
  );
});

test("ML Task Contract includes the required deterministic contents", () => {
  const state = advanceWithAnswers();

  assert.equal(
    state.taskContract.task,
    "Predict late_delivery before shipping allocation for each order.",
  );
  assert.deepEqual(state.taskContract.decisionTimeInputs, [
    "order_created_at",
    "promised_delivery_date",
    "destination_region",
    "shipping_method",
    "item_count",
    "warehouse_backlog",
    "carrier_capacity_score",
    "customer_priority_tier",
  ]);
  assert.deepEqual(state.taskContract.leakageExclusions, [
    "actual_delivery_date",
    "carrier_delay_reason",
    "refund_issued",
    "support_ticket_after_ship",
  ]);
  assert.deepEqual(state.taskContract.temporalSplit, {
    train: "2026-04-01 through 2026-05-15",
    validation: "2026-05-16 through 2026-05-31",
    test: "2026-06-01 through 2026-06-30",
    timeColumn: "order_created_at",
  });
  assert.deepEqual(state.taskContract.objective, {
    primaryMetric: "F1 score on late deliveries",
    minimumQuality: "F1 >= 0.78 on the June 2026 test window",
    tieBreakers: [
      "lower p95 latency",
      "lower cost per 1,000 decisions",
      "simpler model class",
    ],
  });
  assert.deepEqual(state.taskContract.latencyConstraint, {
    p95Ms: 120,
    hardLimitMs: 150,
  });
  assert.equal(
    state.taskContract.humanFallback,
    "Route predictions below 0.62 confidence to the simulated operations review queue.",
  );
});

test("candidate comparison is deterministic and selects the compliant champion", () => {
  const approved = reduceWorkflow(advanceWithAnswers(), {
    type: ACTIONS.APPROVE_TASK_CONTRACT,
  });
  const trained = reduceWorkflow(approved, { type: ACTIONS.RUN_CANDIDATE_TRAINING });

  assert.deepEqual(
    trained.training.candidates.map((candidate) => ({
      id: candidate.id,
      f1: candidate.quality.f1,
      p95Ms: candidate.latency.p95Ms,
      costPer1k: candidate.cost.costPer1k,
      decision: candidate.decision,
    })),
    [
      {
        id: "rules-baseline",
        f1: 0.69,
        p95Ms: 18,
        costPer1k: 0.02,
        decision: "Kept as explainable fallback",
      },
      {
        id: "regularized-logistic",
        f1: 0.79,
        p95Ms: 34,
        costPer1k: 0.07,
        decision: "Meets quality and latency",
      },
      {
        id: "calibrated-gbt",
        f1: 0.84,
        p95Ms: 71,
        costPer1k: 0.18,
        decision: "Selected champion",
      },
      {
        id: "deep-sequence-model",
        f1: 0.87,
        p95Ms: 188,
        costPer1k: 1.2,
        decision: "Rejected: violates latency hard limit",
      },
    ],
  );
  assert.equal(selectChampion(CANDIDATE_COMPARISON).id, "calibrated-gbt");
  assert.equal(trained.training.runId, "candidate-training-001");
});

test("reset and manual retrain behavior are explicit and deterministic", () => {
  let state = reduceWorkflow(
    reduceWorkflow(
      reduceWorkflow(
        reduceWorkflow(advanceWithAnswers(), {
          type: ACTIONS.APPROVE_TASK_CONTRACT,
        }),
        { type: ACTIONS.RUN_CANDIDATE_TRAINING },
      ),
      { type: ACTIONS.ACCEPT_CHAMPION },
    ),
    { type: ACTIONS.DEPLOY_CHAMPION },
  );

  assert.throws(
    () => reduceWorkflow(state, { type: ACTIONS.REQUEST_MANUAL_RETRAIN }),
    /requires captured feedback/,
  );

  state = reduceWorkflow(state, { type: ACTIONS.CAPTURE_FEEDBACK });
  const retrain = reduceWorkflow(state, { type: ACTIONS.REQUEST_MANUAL_RETRAIN });

  assert.equal(retrain.retraining.status, "queued");
  assert.equal(retrain.retraining.runId, "manual-retrain-001");
  assert.equal(retrain.retraining.reason, "32 simulated feedback examples captured");
  assert.equal(retrain.training.round, 2);
  assert.equal(retrain.training.championId, "calibrated-gbt");

  assert.deepEqual(
    reduceWorkflow(retrain, { type: ACTIONS.RESET_WORKFLOW }),
    createInitialState(),
  );
});
