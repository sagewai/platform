import {
  ACTIONS,
  DEFAULT_BUSINESS_ANSWERS,
  ML_TASK_CONTRACT,
  SAMPLE_S3_CONNECTION,
  STEPS,
  createInitialState,
  getCurrentStep,
  reduceWorkflow,
} from "./workflow.js";

const app = document.querySelector("#app");
const announcer = document.querySelector("#announcer");

let state = createInitialState();
let lastError = "";

app.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-action]");
  if (!trigger || trigger.disabled) {
    return;
  }

  dispatch({ type: trigger.dataset.action });
});

app.addEventListener("submit", (event) => {
  if (event.target.id !== "business-form") {
    return;
  }

  event.preventDefault();
  const formData = new FormData(event.target);
  dispatch({
    type: ACTIONS.ANSWER_BUSINESS_QUESTIONS,
    answers: Object.fromEntries(formData.entries()),
  });
});

render();

function dispatch(action) {
  try {
    state = reduceWorkflow(state, action);
    lastError = "";
    announce(actionMessage(action.type));
  } catch (error) {
    lastError = error.message;
    announce(error.message);
  }
  render();
}

function render() {
  const current = getCurrentStep(state);
  app.innerHTML = `
    <header class="topbar">
      <div>
        <span class="eyebrow">Simulated product shell</span>
        <h1>Adaptive Intelligence Platform</h1>
        <p class="lede">
          A deterministic browser journey from sample S3 data to an improvable
          Smart Application. No credentials, uploads, AWS calls, or real model
          training are performed.
        </p>
      </div>
      <button class="subtle" type="button" data-action="${ACTIONS.RESET_WORKFLOW}">
        Reset simulation
      </button>
    </header>
    <main class="workspace">
      ${renderStepRail(current)}
      <section class="panel" aria-labelledby="current-title">
        ${lastError ? `<div class="alert" role="alert">${escapeHtml(lastError)}</div>` : ""}
        ${renderCurrentStep(current)}
      </section>
    </main>
  `;
}

function renderStepRail(current) {
  return `
    <aside class="rail" aria-label="Guided journey progress">
      <p class="rail-title">Seven-step journey</p>
      <ol class="step-list">
        ${STEPS.map((step, index) => {
          const isComplete = index < state.stepIndex;
          const isActive = step.id === current.id;
          return `
            <li
              class="step-item ${isComplete ? "is-complete" : ""} ${isActive ? "is-active" : ""}"
              ${isActive ? 'aria-current="step"' : ""}
            >
              <span class="step-number">${index + 1}</span>
              <span class="step-copy">
                <strong>${escapeHtml(step.shortTitle)}</strong>
                <span>${isComplete ? "Complete" : isActive ? "Current" : "Pending"}</span>
              </span>
            </li>
          `;
        }).join("")}
      </ol>
    </aside>
  `;
}

function renderCurrentStep(current) {
  switch (current.id) {
    case "s3-connect":
      return renderS3Connect(current);
    case "dataset-discovery":
      return renderDatasetDiscovery(current);
    case "business-questions":
      return renderBusinessQuestions(current);
    case "task-contract":
      return renderTaskContract(current);
    case "candidate-training":
      return renderCandidateTraining(current);
    case "champion-evidence":
      return renderChampionEvidence(current);
    case "deploy-feedback-retrain":
      return renderImproveLoop(current);
    default:
      return "";
  }
}

function renderPanelHeader(current) {
  return `
    <div class="panel-header">
      <div>
        <span class="badge">Simulated step ${state.stepIndex + 1} of ${STEPS.length}</span>
        <h2 id="current-title">${escapeHtml(current.title)}</h2>
      </div>
      <span class="badge">Deterministic</span>
    </div>
  `;
}

function renderS3Connect(current) {
  return `
    ${renderPanelHeader(current)}
    <p class="simulation-note">
      Simulated action: connect to a fixed, non-secret sample S3 bucket and prefix.
    </p>
    <div class="content-grid">
      <div class="support-card">
        <h3>Sample S3 location</h3>
        ${renderKeyValues(SAMPLE_S3_CONNECTION)}
      </div>
      <div class="support-card">
        <h3>Scope guardrails</h3>
        ${renderList([
          "No real AWS SDK, network request, upload, or credential prompt.",
          "Only a simulated catalog scan is represented in the browser.",
          "All downstream data and model actions use deterministic sample values.",
        ])}
      </div>
    </div>
    <div class="action-row">
      <button class="primary" type="button" data-action="${ACTIONS.CONNECT_SAMPLE_S3}">
        Connect sample prefix (simulated)
      </button>
    </div>
  `;
}

function renderDatasetDiscovery(current) {
  const outcome = state.dataset.candidateOutcomeColumn;
  return `
    ${renderPanelHeader(current)}
    <p class="simulation-note">
      Simulated discovery: the browser shows a fixed schema and candidate outcome
      column from the sample S3 prefix.
    </p>
    <div class="content-grid">
      <div class="support-card">
        <h3>Dataset</h3>
        ${renderKeyValues({
          name: state.dataset.name,
          source: state.dataset.source,
          grain: state.dataset.grain,
          rows: state.dataset.rows.toLocaleString("en-US"),
          timeColumn: state.dataset.timeColumn,
        })}
      </div>
      <div class="support-card">
        <h3>Candidate outcome</h3>
        <p>
          <span class="role-pill outcome">${escapeHtml(outcome)}</span>
          is proposed as the deterministic supervised-learning target.
        </p>
      </div>
      <div class="support-card full">
        <h3>Discovered columns</h3>
        ${renderColumnTable(state.dataset.columns)}
      </div>
    </div>
    <div class="action-row">
      <button class="primary" type="button" data-action="${ACTIONS.CONFIRM_DATASET}">
        Confirm discovered dataset (simulated)
      </button>
    </div>
  `;
}

function renderBusinessQuestions(current) {
  return `
    ${renderPanelHeader(current)}
    <p class="simulation-note">
      Simulated questions: these defaults are the minimum deterministic inputs
      needed to propose the ML Task Contract.
    </p>
    <form id="business-form" class="form-grid">
      ${renderTextArea(
        "businessGoal",
        "Business goal",
        DEFAULT_BUSINESS_ANSWERS.businessGoal,
        "What should the Smart Application improve?"
      )}
      ${renderTextArea(
        "decisionPoint",
        "Decision point",
        DEFAULT_BUSINESS_ANSWERS.decisionPoint,
        "When must a prediction be available?"
      )}
      ${renderTextArea(
        "actionOwner",
        "Action owner",
        DEFAULT_BUSINESS_ANSWERS.actionOwner,
        "Who acts on the prediction?"
      )}
      ${renderTextArea(
        "successMetric",
        "Success metric",
        DEFAULT_BUSINESS_ANSWERS.successMetric,
        "How should the simulated outcome be judged?"
      )}
      ${renderTextArea(
        "uncertainCasePolicy",
        "Uncertain case policy",
        DEFAULT_BUSINESS_ANSWERS.uncertainCasePolicy,
        "What should happen when confidence is too low?"
      )}
      <div class="action-row">
        <button class="primary" type="submit">Propose ML Task Contract (simulated)</button>
      </div>
    </form>
  `;
}

function renderTaskContract(current) {
  return `
    ${renderPanelHeader(current)}
    <p class="simulation-note">
      Simulated proposal: the contract fixes the task, usable decision-time
      inputs, excluded leakage fields, temporal split, objective, latency
      constraint, and human fallback.
    </p>
    <div class="content-grid">
      <div class="support-card">
        <h3>Task</h3>
        <p>${escapeHtml(state.taskContract.task)}</p>
        <p class="hint">${escapeHtml(state.taskContract.businessGoal)}</p>
      </div>
      <div class="support-card">
        <h3>Latency and fallback</h3>
        ${renderKeyValues({
          p95: `${state.taskContract.latencyConstraint.p95Ms} ms`,
          hardLimit: `${state.taskContract.latencyConstraint.hardLimitMs} ms`,
          humanFallback: state.taskContract.humanFallback,
        })}
      </div>
      <div class="support-card">
        <h3>Decision-time inputs</h3>
        ${renderList(state.taskContract.decisionTimeInputs)}
      </div>
      <div class="support-card">
        <h3>Leakage exclusions</h3>
        ${renderList(state.taskContract.leakageExclusions)}
      </div>
      <div class="support-card">
        <h3>Temporal split</h3>
        ${renderKeyValues(state.taskContract.temporalSplit)}
      </div>
      <div class="support-card">
        <h3>Objective</h3>
        ${renderKeyValues({
          primaryMetric: state.taskContract.objective.primaryMetric,
          minimumQuality: state.taskContract.objective.minimumQuality,
        })}
        ${renderList(state.taskContract.objective.tieBreakers)}
      </div>
    </div>
    <div class="action-row">
      <button class="primary" type="button" data-action="${ACTIONS.APPROVE_TASK_CONTRACT}">
        Approve contract (simulated)
      </button>
    </div>
  `;
}

function renderCandidateTraining(current) {
  return `
    ${renderPanelHeader(current)}
    <p class="simulation-note">
      Simulated training: fixed candidates are scored with deterministic quality,
      latency, and cost evidence. No model is trained and no service is called.
    </p>
    <div class="content-grid">
      <div class="support-card">
        <h3>Candidate set</h3>
        ${renderList([
          "Rules baseline",
          "Regularized logistic model",
          "Calibrated gradient boosted trees",
          "Deep sequence model",
        ])}
      </div>
      <div class="support-card">
        <h3>Selection rule</h3>
        ${renderList([
          `Meet the ${ML_TASK_CONTRACT.latencyConstraint.hardLimitMs} ms hard limit.`,
          "Prefer higher F1 score on the June 2026 test window.",
          "Use cost and simplicity only as deterministic tie-breakers.",
        ])}
      </div>
    </div>
    <div class="action-row">
      <button class="primary" type="button" data-action="${ACTIONS.RUN_CANDIDATE_TRAINING}">
        Run candidate comparison (simulated)
      </button>
    </div>
  `;
}

function renderChampionEvidence(current) {
  const champion = state.training.candidates.find(
    (candidate) => candidate.id === state.training.championId,
  );
  return `
    ${renderPanelHeader(current)}
    <p class="simulation-note">
      Simulated evidence: the champion is selected from a deterministic candidate
      comparison. The higher-F1 sequence model is rejected because it breaks the
      latency hard limit.
    </p>
    <div class="metrics">
      ${renderMetric(champion.quality.f1.toFixed(2), "Champion F1 score")}
      ${renderMetric(`${champion.latency.p95Ms} ms`, "p95 latency")}
      ${renderMetric(`$${champion.cost.costPer1k.toFixed(2)}`, "Cost per 1,000 decisions")}
    </div>
    <div class="candidate-grid">
      ${state.training.candidates.map((candidate) => renderCandidate(candidate)).join("")}
    </div>
    <div class="action-row">
      <button class="primary" type="button" data-action="${ACTIONS.ACCEPT_CHAMPION}">
        Accept champion (simulated)
      </button>
    </div>
  `;
}

function renderImproveLoop(current) {
  const canCaptureFeedback = state.deployment.status === "deployed";
  const canRetrain = state.feedback.status === "captured";
  return `
    ${renderPanelHeader(current)}
    <p class="simulation-note">
      Simulated loop: deployment, feedback capture, and manual retraining are
      state changes inside the browser only.
    </p>
    <div class="status-grid">
      <div class="status-card">
        <h3>Deployment</h3>
        <strong>${escapeHtml(statusLabel(state.deployment.status))}</strong>
        <span>${escapeHtml(state.deployment.endpointLabel || "No simulated endpoint yet")}</span>
      </div>
      <div class="status-card">
        <h3>Feedback</h3>
        <strong>${escapeHtml(statusLabel(state.feedback.status))}</strong>
        <span>${state.feedback.examples} simulated examples, ${state.feedback.acceptedCorrections} corrections</span>
      </div>
      <div class="status-card">
        <h3>Manual retrain</h3>
        <strong>${escapeHtml(statusLabel(state.retraining.status))}</strong>
        <span>${escapeHtml(state.retraining.reason || "Waiting for simulated feedback")}</span>
      </div>
    </div>
    <div class="action-row">
      <button
        class="primary"
        type="button"
        data-action="${ACTIONS.DEPLOY_CHAMPION}"
        ${state.deployment.status === "deployed" ? "disabled" : ""}
      >
        Deploy champion (simulated)
      </button>
      <button
        class="secondary"
        type="button"
        data-action="${ACTIONS.CAPTURE_FEEDBACK}"
        ${canCaptureFeedback && state.feedback.status !== "captured" ? "" : "disabled"}
      >
        Capture feedback (simulated)
      </button>
      <button
        type="button"
        data-action="${ACTIONS.REQUEST_MANUAL_RETRAIN}"
        ${canRetrain && state.retraining.status !== "queued" ? "" : "disabled"}
      >
        Queue manual retrain (simulated)
      </button>
    </div>
  `;
}

function renderTextArea(name, label, value, hint) {
  return `
    <label for="${name}">
      ${escapeHtml(label)}
      <textarea id="${name}" name="${name}" required>${escapeHtml(value)}</textarea>
      <span class="hint">${escapeHtml(hint)}</span>
    </label>
  `;
}

function renderCandidate(candidate) {
  const isChampion = candidate.id === state.training.championId;
  const isRejected = candidate.decision.startsWith("Rejected");
  return `
    <article class="candidate-card ${isChampion ? "is-champion" : ""} ${isRejected ? "is-rejected" : ""}">
      <h3>${escapeHtml(candidate.name)}</h3>
      <p>${escapeHtml(candidate.modelClass)}</p>
      <div class="candidate-meta">
        <span class="badge">F1 ${candidate.quality.f1.toFixed(2)}</span>
        <span class="badge">${candidate.latency.p95Ms} ms p95</span>
        <span class="badge">$${candidate.cost.costPer1k.toFixed(2)} / 1k</span>
      </div>
      <p class="hint">${escapeHtml(candidate.decision)}</p>
    </article>
  `;
}

function renderMetric(value, label) {
  return `
    <div class="metric">
      <strong>${escapeHtml(value)}</strong>
      <span>${escapeHtml(label)}</span>
    </div>
  `;
}

function renderColumnTable(columns) {
  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Column</th>
            <th scope="col">Type</th>
            <th scope="col">Role</th>
          </tr>
        </thead>
        <tbody>
          ${columns.map((column) => `
            <tr>
              <td>${escapeHtml(column.name)}</td>
              <td>${escapeHtml(column.type)}</td>
              <td><span class="${roleClass(column.role)}">${escapeHtml(column.role)}</span></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderKeyValues(values) {
  return `
    <dl class="key-values">
      ${Object.entries(values).map(([key, value]) => `
        <dt>${escapeHtml(labelize(key))}</dt>
        <dd>${escapeHtml(value)}</dd>
      `).join("")}
    </dl>
  `;
}

function renderList(items) {
  return `
    <ul class="check-list">
      ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ul>
  `;
}

function roleClass(role) {
  if (role === "candidate outcome") {
    return "role-pill outcome";
  }
  if (role === "leakage exclusion") {
    return "role-pill excluded";
  }
  return "role-pill";
}

function statusLabel(status) {
  return status
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function labelize(key) {
  return key
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (first) => first.toUpperCase());
}

function actionMessage(type) {
  const messages = {
    [ACTIONS.CONNECT_SAMPLE_S3]: "Simulated S3 prefix connected.",
    [ACTIONS.CONFIRM_DATASET]: "Simulated dataset confirmed.",
    [ACTIONS.ANSWER_BUSINESS_QUESTIONS]: "Simulated ML Task Contract proposed.",
    [ACTIONS.APPROVE_TASK_CONTRACT]: "Simulated ML Task Contract approved.",
    [ACTIONS.RUN_CANDIDATE_TRAINING]: "Simulated candidate comparison completed.",
    [ACTIONS.ACCEPT_CHAMPION]: "Simulated champion accepted.",
    [ACTIONS.DEPLOY_CHAMPION]: "Simulated champion deployed.",
    [ACTIONS.CAPTURE_FEEDBACK]: "Simulated feedback captured.",
    [ACTIONS.REQUEST_MANUAL_RETRAIN]: "Simulated manual retrain queued.",
    [ACTIONS.RESET_WORKFLOW]: "Simulation reset.",
  };
  return messages[type] || "Simulation updated.";
}

function announce(message) {
  announcer.textContent = message;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => {
    const escapes = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return escapes[character];
  });
}
