import { createGame, move, restart } from "./engine.js";

const canvas = document.querySelector("#board");
const context = canvas.getContext("2d");
const score = document.querySelector("#score");
const signals = document.querySelector("#signals");
const lives = document.querySelector("#lives");
const message = document.querySelector("#message");
const result = document.querySelector("#result");
const resultKicker = document.querySelector("#result-kicker");
const resultTitle = document.querySelector("#result-title");
const playAgain = document.querySelector("#play-again");
const restartButton = document.querySelector("#restart");

let state = createGame();

const palette = {
  board: "#081a29",
  grid: "rgba(114, 216, 255, 0.08)",
  wall: "#163247",
  wallEdge: "#214b63",
  signal: "#f6c85f",
  signalGlow: "rgba(246, 200, 95, 0.28)",
  glitch: "#ff557a",
  runner: "#72d8ff",
  runnerCore: "#e9fbff",
  uplink: "#75f0b0",
  locked: "#546a79",
};

function resizeCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const bounds = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(bounds.width * ratio));
  const height = Math.max(1, Math.round(bounds.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
}

function cellMetrics() {
  return {
    width: canvas.width / state.level.width,
    height: canvas.height / state.level.height,
  };
}

function cellCenter([x, y]) {
  const cell = cellMetrics();
  return [(x + 0.5) * cell.width, (y + 0.5) * cell.height];
}

function drawGrid() {
  const cell = cellMetrics();
  context.fillStyle = palette.board;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = palette.grid;
  context.lineWidth = Math.max(1, window.devicePixelRatio || 1);
  for (let x = 1; x < state.level.width; x += 1) {
    context.beginPath();
    context.moveTo(x * cell.width, 0);
    context.lineTo(x * cell.width, canvas.height);
    context.stroke();
  }
  for (let y = 1; y < state.level.height; y += 1) {
    context.beginPath();
    context.moveTo(0, y * cell.height);
    context.lineTo(canvas.width, y * cell.height);
    context.stroke();
  }
}

function drawWalls() {
  const cell = cellMetrics();
  const inset = Math.min(cell.width, cell.height) * 0.12;
  for (const [x, y] of state.walls) {
    context.fillStyle = palette.wall;
    context.strokeStyle = palette.wallEdge;
    context.lineWidth = inset * 0.32;
    context.beginPath();
    context.roundRect(
      x * cell.width + inset,
      y * cell.height + inset,
      cell.width - inset * 2,
      cell.height - inset * 2,
      inset * 0.6,
    );
    context.fill();
    context.stroke();
  }
}

function drawSignals() {
  const radius = Math.min(cellMetrics().width, cellMetrics().height) * 0.16;
  for (const point of state.evidence) {
    const [x, y] = cellCenter(point);
    context.fillStyle = palette.signalGlow;
    context.beginPath();
    context.arc(x, y, radius * 2.1, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = palette.signal;
    context.beginPath();
    context.moveTo(x, y - radius);
    context.lineTo(x + radius, y);
    context.lineTo(x, y + radius);
    context.lineTo(x - radius, y);
    context.closePath();
    context.fill();
  }
}

function drawUplink() {
  const [x, y] = cellCenter(state.exit);
  const radius = Math.min(cellMetrics().width, cellMetrics().height) * 0.27;
  const unlocked = state.evidence.length === 0;
  context.strokeStyle = unlocked ? palette.uplink : palette.locked;
  context.lineWidth = radius * 0.22;
  context.beginPath();
  context.arc(x, y, radius, 0, Math.PI * 2);
  context.stroke();
  context.beginPath();
  context.arc(x, y, radius * 0.48, 0, Math.PI * 2);
  context.stroke();
}

function drawHazards() {
  const size = Math.min(cellMetrics().width, cellMetrics().height) * 0.28;
  context.strokeStyle = palette.glitch;
  context.lineWidth = size * 0.24;
  context.lineCap = "round";
  for (const { position } of state.hazards) {
    const [x, y] = cellCenter(position);
    context.beginPath();
    context.moveTo(x - size, y - size);
    context.lineTo(x + size, y + size);
    context.moveTo(x + size, y - size);
    context.lineTo(x - size, y + size);
    context.stroke();
  }
}

function drawRunner() {
  const [x, y] = cellCenter(state.player);
  const radius = Math.min(cellMetrics().width, cellMetrics().height) * 0.25;
  context.fillStyle = palette.runner;
  context.beginPath();
  context.arc(x, y, radius, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = palette.runnerCore;
  context.beginPath();
  context.arc(x, y, radius * 0.36, 0, Math.PI * 2);
  context.fill();
}

function render() {
  resizeCanvas();
  drawGrid();
  drawWalls();
  drawUplink();
  drawSignals();
  drawHazards();
  drawRunner();

  score.textContent = String(state.score).padStart(4, "0");
  const totalSignals = state.level.evidence.length;
  signals.textContent = `${totalSignals - state.evidence.length} / ${totalSignals}`;
  lives.textContent = Array.from({ length: state.level.lives }, (_, index) =>
    index < state.lives ? "●" : "○",
  ).join(" ");
  message.textContent = state.message;

  const ended = state.status !== "playing";
  const justEnded = ended && result.hidden;
  result.hidden = !ended;
  if (ended) {
    resultKicker.textContent = state.status === "won" ? "Route complete" : "Control lost";
    resultTitle.textContent = state.status === "won" ? "Uplink restored" : "Try another route";
    if (justEnded) {
      playAgain.focus();
    }
  }
}

function takeTurn(direction) {
  state = move(state, direction);
  render();
}

const keyDirections = {
  ArrowUp: "up",
  w: "up",
  W: "up",
  ArrowDown: "down",
  s: "down",
  S: "down",
  ArrowLeft: "left",
  a: "left",
  A: "left",
  ArrowRight: "right",
  d: "right",
  D: "right",
};

window.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  const direction = keyDirections[event.key];
  if (direction) {
    event.preventDefault();
    takeTurn(direction);
  }
});

document.querySelectorAll("[data-direction]").forEach((button) => {
  button.addEventListener("click", () => takeTurn(button.dataset.direction));
});

function resetGame() {
  state = restart(state);
  render();
  restartButton.focus();
}

restartButton.addEventListener("click", resetGame);
playAgain.addEventListener("click", resetGame);
window.addEventListener("resize", render);

render();
