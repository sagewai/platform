import assert from "node:assert/strict";
import test from "node:test";

import { createGame, move, restart } from "./engine.js";

const level = (overrides = {}) => ({
  width: 4,
  height: 3,
  start: [0, 0],
  exit: [3, 2],
  walls: [],
  evidence: [],
  hazards: [],
  lives: 3,
  ...overrides,
});

test("blocked moves do not advance the turn", () => {
  const state = createGame(
    level({
      walls: [[1, 0]],
    }),
  );

  const outside = move(state, "up");
  const wall = move(state, "right");

  assert.deepEqual(outside.player, [0, 0]);
  assert.deepEqual(wall.player, [0, 0]);
  assert.equal(outside.turn, 0);
  assert.equal(wall.turn, 0);
});

test("collecting every signal unlocks the exit", () => {
  let state = createGame(
    level({
      width: 3,
      height: 1,
      exit: [2, 0],
      evidence: [[1, 0]],
    }),
  );

  state = move(state, "right");
  assert.equal(state.score, 100);
  assert.deepEqual(state.evidence, []);
  assert.equal(state.status, "playing");

  state = move(state, "right");
  assert.equal(state.status, "won");
});

test("the exit stays locked while signals remain", () => {
  const state = move(
    createGame(
      level({
        exit: [1, 0],
        evidence: [[3, 2]],
      }),
    ),
    "right",
  );

  assert.equal(state.status, "playing");
  assert.match(state.message, /signal/i);
});

test("touching a glitch costs a life and resets the runner", () => {
  const state = move(
    createGame(
      level({
        hazards: [{ path: [[1, 0]] }],
      }),
    ),
    "right",
  );

  assert.equal(state.lives, 2);
  assert.deepEqual(state.player, [0, 0]);
  assert.equal(state.status, "playing");
});

test("a final glitch collision ends the game", () => {
  const state = move(
    createGame(
      level({
        lives: 1,
        hazards: [{ path: [[1, 0]] }],
      }),
    ),
    "right",
  );

  assert.equal(state.lives, 0);
  assert.equal(state.status, "lost");
});

test("a collision cannot make remaining lives negative", () => {
  const state = move(
    createGame(
      level({
        lives: 0,
        hazards: [{ path: [[1, 0]] }],
      }),
    ),
    "right",
  );

  assert.equal(state.lives, 0);
  assert.equal(state.status, "lost");
});

test("glitches patrol after a successful move", () => {
  const state = move(
    createGame(
      level({
        hazards: [{ path: [[3, 0], [3, 1]] }],
      }),
    ),
    "down",
  );

  assert.deepEqual(state.hazards[0].position, [3, 1]);
  assert.equal(state.turn, 1);
});

test("restart restores the original level", () => {
  const initial = createGame(
    level({
      evidence: [[1, 0]],
    }),
  );
  const changed = move(initial, "right");

  assert.deepEqual(restart(changed), initial);
});

test("the shipped game can be won without losing a life", () => {
  const directions = ["up", "down", "left", "right"];
  const initial = createGame();
  const stateKey = (state) =>
    JSON.stringify([
      state.player,
      state.evidence,
      state.hazards.map((hazard) => hazard.pathIndex),
      state.lives,
      state.status,
      state.turn,
    ]);
  const turnBound = initial.level.turnLimit ?? 120;
  const queue = [initial];
  const visited = new Set([stateKey(initial)]);
  let winningState = null;

  while (queue.length > 0) {
    const state = queue.shift();
    if (state.status === "won" && state.lives === initial.lives) {
      winningState = state;
      break;
    }
    if (state.status !== "playing" || state.turn >= turnBound) {
      continue;
    }
    for (const direction of directions) {
      const next = move(state, direction);
      if (next.lives < initial.lives) {
        continue;
      }
      const key = stateKey(next);
      if (!visited.has(key)) {
        visited.add(key);
        queue.push(next);
      }
    }
  }

  assert.ok(
    winningState,
    `expected a winning route within ${turnBound} turns; searched ${visited.size} states`,
  );
  assert.equal(winningState.lives, initial.lives);
  if (initial.level.turnLimit !== undefined) {
    assert.ok(winningState.turn <= initial.level.turnLimit);
  }
});

test("glitch speed advances multiple path steps and checks intermediate collisions", () => {
  const advanced = move(
    createGame(
      level({
        height: 2,
        hazards: [{ path: [[3, 0], [2, 0], [1, 0], [2, 0]], speed: 2 }],
      }),
    ),
    "down",
  );

  assert.equal(advanced.hazards[0].pathIndex, 2);
  assert.deepEqual(advanced.hazards[0].position, [1, 0]);

  const collided = move(
    createGame(
      level({
        hazards: [{ path: [[3, 0], [1, 0], [2, 0]], speed: 2 }],
      }),
    ),
    "right",
  );

  assert.equal(collided.lives, 2);
  assert.deepEqual(collided.player, [0, 0]);
  assert.equal(collided.hazards[0].pathIndex, 1);
  assert.deepEqual(collided.hazards[0].position, [1, 0]);
});

test("turn limit loses on the move that exhausts the budget", () => {
  const lost = move(
    createGame(
      level({
        turnLimit: 1,
      }),
    ),
    "down",
  );

  assert.equal(lost.turn, 1);
  assert.equal(lost.status, "lost");
});

test("reaching the exit on the final permitted turn still wins", () => {
  const won = move(
    createGame(
      level({
        width: 2,
        height: 1,
        exit: [1, 0],
        turnLimit: 1,
      }),
    ),
    "right",
  );

  assert.equal(won.turn, 1);
  assert.equal(won.status, "won");
});

test("omitted difficulty controls preserve the original state shape", () => {
  const state = createGame(
    level({
      hazards: [{ path: [[3, 0], [3, 1]] }],
    }),
  );

  assert.deepEqual(Object.keys(state), [
    "level",
    "player",
    "exit",
    "walls",
    "evidence",
    "hazards",
    "lives",
    "score",
    "turn",
    "status",
    "message",
  ]);
  assert.deepEqual(Object.keys(state.level), [
    "width",
    "height",
    "start",
    "exit",
    "lives",
    "walls",
    "evidence",
    "hazards",
  ]);
  assert.deepEqual(Object.keys(state.level.hazards[0]), ["path"]);
  assert.deepEqual(Object.keys(state.hazards[0]), ["path", "pathIndex", "position"]);
  assert.deepEqual(state, {
    level: {
      width: 4,
      height: 3,
      start: [0, 0],
      exit: [3, 2],
      lives: 3,
      walls: [],
      evidence: [],
      hazards: [{ path: [[3, 0], [3, 1]] }],
    },
    player: [0, 0],
    exit: [3, 2],
    walls: [],
    evidence: [],
    hazards: [{ path: [[3, 0], [3, 1]], pathIndex: 0, position: [3, 0] }],
    lives: 3,
    score: 0,
    turn: 0,
    status: "playing",
    message: "Collect every signal, then reach the uplink.",
  });
});

test("restart round-trips hazard speed and turn limit", () => {
  const initial = createGame(
    level({
      turnLimit: 4,
      hazards: [{ path: [[3, 0], [3, 1]], speed: 2 }],
    }),
  );
  const changed = move(initial, "down");
  const restarted = restart(changed);

  assert.equal(restarted.level.turnLimit, 4);
  assert.equal(restarted.level.hazards[0].speed, 2);
  assert.equal(restarted.hazards[0].speed, 2);
  assert.deepEqual(restarted, initial);
});
