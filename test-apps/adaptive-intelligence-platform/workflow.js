export const ACTIONS = Object.freeze({
  CONNECT_SAMPLE_S3: "CONNECT_SAMPLE_S3",
  CONFIRM_DATASET: "CONFIRM_DATASET",
  ANSWER_BUSINESS_QUESTIONS: "ANSWER_BUSINESS_QUESTIONS",
  APPROVE_TASK_CONTRACT: "APPROVE_TASK_CONTRACT",
  RUN_CANDIDATE_TRAINING: "RUN_CANDIDATE_TRAINING",
  ACCEPT_CHAMPION: "ACCEPT_CHAMPION",
  DEPLOY_CHAMPION: "DEPLOY_CHAMPION",
  CAPTURE_FEEDBACK: "CAPTURE_FEEDBACK",
  REQUEST_MANUAL_RETRAIN: "REQUEST_MANUAL_RETRAIN",
  RESET_WORKFLOW: "RESET_WORKFLOW",
});

export const STEPS = Object.freeze([
  {
    id: "s3-connect",
    shortTitle: "Connect",
    title: "Connect a sample S3 prefix",
  },
  {
    id: "dataset-discovery",
    shortTitle: "Discover",
    title: "Review discovered dataset structure",
  },
  {
    id: "business-questions",
    shortTitle: "Questions",
    title: "Answer the minimum business questions",
  },
  {
    id: "task-contract",
    shortTitle: "Contract",
    title: "Approve the ML Task Contract",
  },
  {
    id: "candidate-training",
    shortTitle: "Train",
    title: "Run deterministic candidate training",
  },
  {
    id: "champion-evidence",
    shortTitle: "Champion",
    title: "Review champion evidence",
  },
  {
    id: "deploy-feedback-retrain",
    shortTitle: "Improve",
    title: "Deploy, capture feedback, retrain",
  },
]);

export const SAMPLE_S3_CONNECTION = deepFreeze({
  bucket: "s3://sagewai-sample-retail-lake",
  prefix: "orders/2026-q2/",
  region: "us-east-1",
  accessMode: "Simulated read-only catalog scan",
  credentialNote: "No credentials, uploads, or AWS calls are used.",
});

export const DATASET_DISCOVERY = deepFreeze({
  name: "retail_orders_enriched",
  source: "Simulated S3 Parquet manifest",
  grain: "One row per customer order",
  rows: 24816,
  timeColumn: "order_created_at",
  candidateOutcomeColumn: "late_delivery",
  columns: [
    { name: "order_id", type: "string", role: "identifier" },
    { name: "order_created_at", type: "timestamp", role: "decision time" },
    { name: "promised_delivery_date", type: "date", role: "decision input" },
    { name: "destination_region", type: "category", role: "decision input" },
    { name: "shipping_method", type: "category", role: "decision input" },
    { name: "item_count", type: "integer", role: "decision input" },
    { name: "warehouse_backlog", type: "integer", role: "decision input" },
    { name: "carrier_capacity_score", type: "number", role: "decision input" },
    { name: "customer_priority_tier", type: "category", role: "decision input" },
    { name: "actual_delivery_date", type: "date", role: "leakage exclusion" },
    { name: "carrier_delay_reason", type: "category", role: "leakage exclusion" },
    { name: "refund_issued", type: "boolean", role: "leakage exclusion" },
    { name: "support_ticket_after_ship", type: "boolean", role: "leakage exclusion" },
    { name: "late_delivery", type: "boolean", role: "candidate outcome" },
  ],
});

export const DEFAULT_BUSINESS_ANSWERS = deepFreeze({
  businessGoal: "Prioritize at-risk orders before shipping allocation.",
  decisionPoint: "Before shipping allocation",
  actionOwner: "Operations review queue",
  successMetric: "Reduce preventable late deliveries while keeping review volume manageable.",
  uncertainCasePolicy: "Route low-confidence predictions to a human operations reviewer.",
});

export const ML_TASK_CONTRACT = deepFreeze({
  task: "Predict late_delivery before shipping allocation for each order.",
  businessGoal: DEFAULT_BUSINESS_ANSWERS.businessGoal,
  decisionPoint: DEFAULT_BUSINESS_ANSWERS.decisionPoint,
  decisionTimeInputs: [
    "order_created_at",
    "promised_delivery_date",
    "destination_region",
    "shipping_method",
    "item_count",
    "warehouse_backlog",
    "carrier_capacity_score",
    "customer_priority_tier",
  ],
  leakageExclusions: [
    "actual_delivery_date",
    "carrier_delay_reason",
    "refund_issued",
    "support_ticket_after_ship",
  ],
  temporalSplit: {
    train: "2026-04-01 through 2026-05-15",
    validation: "2026-05-16 through 2026-05-31",
    test: "2026-06-01 through 2026-06-30",
    timeColumn: "order_created_at",
  },
  objective: {
    primaryMetric: "F1 score on late deliveries",
    minimumQuality: "F1 >= 0.78 on the June 2026 test window",
    tieBreakers: [
      "lower p95 latency",
      "lower cost per 1,000 decisions",
      "simpler model class",
    ],
  },
  latencyConstraint: {
    p95Ms: 120,
    hardLimitMs: 150,
  },
  humanFallback:
    "Route predictions below 0.62 confidence to the simulated operations review queue.",
});

export const CANDIDATE_COMPARISON = deepFreeze([
  {
    id: "rules-baseline",
    name: "Rules baseline",
    modelClass: "Threshold rules",
    quality: { f1: 0.69, precision: 0.64, recall: 0.75 },
    latency: { p95Ms: 18 },
    cost: { costPer1k: 0.02 },
    decision: "Kept as explainable fallback",
  },
  {
    id: "regularized-logistic",
    name: "Regularized logistic model",
    modelClass: "Linear classifier",
    quality: { f1: 0.79, precision: 0.77, recall: 0.81 },
    latency: { p95Ms: 34 },
    cost: { costPer1k: 0.07 },
    decision: "Meets quality and latency",
  },
  {
    id: "calibrated-gbt",
    name: "Calibrated gradient boosted trees",
    modelClass: "Tree ensemble",
    quality: { f1: 0.84, precision: 0.82, recall: 0.86 },
    latency: { p95Ms: 71 },
    cost: { costPer1k: 0.18 },
    decision: "Selected champion",
  },
  {
    id: "deep-sequence-model",
    name: "Deep sequence model",
    modelClass: "Neural sequence model",
    quality: { f1: 0.87, precision: 0.85, recall: 0.89 },
    latency: { p95Ms: 188 },
    cost: { costPer1k: 1.2 },
    decision: "Rejected: violates latency hard limit",
  },
]);

const REQUIRED_BUSINESS_ANSWERS = Object.freeze([
  "businessGoal",
  "decisionPoint",
  "actionOwner",
  "successMetric",
  "uncertainCasePolicy",
]);

export function createInitialState() {
  return {
    stepIndex: 0,
    connection: {
      status: "not-connected",
      config: null,
    },
    dataset: null,
    businessAnswers: null,
    taskContract: null,
    contractApproval: {
      status: "not-approved",
    },
    training: {
      status: "not-started",
      round: 0,
      runId: null,
      candidates: [],
      championId: null,
    },
    deployment: {
      status: "not-ready",
      endpointLabel: null,
    },
    feedback: {
      status: "not-captured",
      examples: 0,
      acceptedCorrections: 0,
    },
    retraining: {
      status: "idle",
      runId: null,
      reason: null,
    },
    history: [],
  };
}

export function getCurrentStep(state) {
  const step = STEPS[state.stepIndex];
  if (!step) {
    throw new Error(`Unknown workflow step index: ${state.stepIndex}`);
  }
  return step;
}

export function reduceWorkflow(state, action) {
  if (!action || typeof action.type !== "string") {
    throw new Error("Workflow action must include a string type.");
  }

  if (action.type === ACTIONS.RESET_WORKFLOW) {
    return createInitialState();
  }

  const draft = clone(state);

  switch (action.type) {
    case ACTIONS.CONNECT_SAMPLE_S3: {
      requireStep(draft, "s3-connect", action.type);
      const config = { ...SAMPLE_S3_CONNECTION, ...(action.connection || {}) };
      validateS3Config(config);
      draft.connection = {
        status: "connected",
        config,
      };
      draft.dataset = clone(DATASET_DISCOVERY);
      draft.stepIndex = 1;
      break;
    }
    case ACTIONS.CONFIRM_DATASET: {
      requireStep(draft, "dataset-discovery", action.type);
      draft.stepIndex = 2;
      break;
    }
    case ACTIONS.ANSWER_BUSINESS_QUESTIONS: {
      requireStep(draft, "business-questions", action.type);
      const answers = { ...DEFAULT_BUSINESS_ANSWERS, ...(action.answers || {}) };
      validateBusinessAnswers(answers);
      draft.businessAnswers = answers;
      draft.taskContract = buildTaskContract(answers);
      draft.stepIndex = 3;
      break;
    }
    case ACTIONS.APPROVE_TASK_CONTRACT: {
      requireStep(draft, "task-contract", action.type);
      draft.contractApproval = {
        status: "approved",
        approver: "Simulated product owner",
      };
      draft.stepIndex = 4;
      break;
    }
    case ACTIONS.RUN_CANDIDATE_TRAINING: {
      requireStep(draft, "candidate-training", action.type);
      const candidates = clone(CANDIDATE_COMPARISON);
      draft.training = {
        status: "completed",
        round: 1,
        runId: "candidate-training-001",
        candidates,
        championId: selectChampion(candidates).id,
      };
      draft.stepIndex = 5;
      break;
    }
    case ACTIONS.ACCEPT_CHAMPION: {
      requireStep(draft, "champion-evidence", action.type);
      requireChampion(draft);
      draft.deployment = {
        status: "ready",
        endpointLabel: "simulated /score-late-delivery endpoint",
      };
      draft.stepIndex = 6;
      break;
    }
    case ACTIONS.DEPLOY_CHAMPION: {
      requireStep(draft, "deploy-feedback-retrain", action.type);
      requireChampion(draft);
      if (!["ready", "deployed"].includes(draft.deployment.status)) {
        throw new Error("DEPLOY_CHAMPION requires a champion ready for deployment.");
      }
      draft.deployment = {
        ...draft.deployment,
        status: "deployed",
      };
      break;
    }
    case ACTIONS.CAPTURE_FEEDBACK: {
      requireStep(draft, "deploy-feedback-retrain", action.type);
      if (draft.deployment.status !== "deployed") {
        throw new Error("CAPTURE_FEEDBACK requires a simulated deployed champion.");
      }
      draft.feedback = {
        status: "captured",
        examples: 32,
        acceptedCorrections: 9,
      };
      break;
    }
    case ACTIONS.REQUEST_MANUAL_RETRAIN: {
      requireStep(draft, "deploy-feedback-retrain", action.type);
      if (draft.feedback.status !== "captured") {
        throw new Error("REQUEST_MANUAL_RETRAIN requires captured feedback.");
      }
      draft.training = {
        ...draft.training,
        round: draft.training.round + 1,
      };
      draft.retraining = {
        status: "queued",
        runId: "manual-retrain-001",
        reason: `${draft.feedback.examples} simulated feedback examples captured`,
      };
      break;
    }
    default:
      throw new Error(`Unsupported workflow action: ${action.type}`);
  }

  draft.history = [...draft.history, action.type];
  return draft;
}

export function selectChampion(candidates) {
  const compliant = candidates.filter(
    (candidate) => candidate.latency.p95Ms <= ML_TASK_CONTRACT.latencyConstraint.hardLimitMs,
  );
  if (compliant.length === 0) {
    throw new Error("No candidate satisfies the latency hard limit.");
  }

  return [...compliant].sort((left, right) => {
    if (right.quality.f1 !== left.quality.f1) {
      return right.quality.f1 - left.quality.f1;
    }
    if (left.cost.costPer1k !== right.cost.costPer1k) {
      return left.cost.costPer1k - right.cost.costPer1k;
    }
    return left.latency.p95Ms - right.latency.p95Ms;
  })[0];
}

function buildTaskContract(answers) {
  return {
    ...clone(ML_TASK_CONTRACT),
    businessGoal: answers.businessGoal,
    decisionPoint: answers.decisionPoint,
    humanFallback:
      "Route predictions below 0.62 confidence to the simulated operations review queue.",
  };
}

function requireStep(state, expectedStepId, actionType) {
  const current = getCurrentStep(state);
  if (current.id !== expectedStepId) {
    throw new Error(
      `${actionType} requires step ${expectedStepId}; current step is ${current.id}.`,
    );
  }
}

function requireChampion(state) {
  if (!state.training.championId) {
    throw new Error("A champion candidate is required for this action.");
  }
}

function validateS3Config(config) {
  if (!config.bucket.startsWith("s3://")) {
    throw new Error("Sample bucket must use an s3:// URI.");
  }
  if (!config.prefix || config.prefix.includes("..")) {
    throw new Error("Sample prefix must be a bounded non-secret path.");
  }
}

function validateBusinessAnswers(answers) {
  for (const key of REQUIRED_BUSINESS_ANSWERS) {
    if (typeof answers[key] !== "string" || answers[key].trim() === "") {
      throw new Error(`Missing required business answer: ${key}`);
    }
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function deepFreeze(value) {
  if (value && typeof value === "object") {
    for (const nested of Object.values(value)) {
      deepFreeze(nested);
    }
    Object.freeze(value);
  }
  return value;
}
