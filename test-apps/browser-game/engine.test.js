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

test("the shipped game can be won with moving glitches", () => {
  const directions = ["up", "down", "left", "right"];
  const initial = createGame();
  const stateKey = (state) =>
    JSON.stringify([
      state.player,
      state.evidence,
      state.hazards.map((hazard) => hazard.pathIndex),
      state.lives,
      state.status,
    ]);
  const queue = [initial];
  const visited = new Set([stateKey(initial)]);
  let won = false;

  while (queue.length > 0) {
    const state = queue.shift();
    if (state.status === "won") {
      won = true;
      break;
    }
    for (const direction of directions) {
      const next = move(state, direction);
      const key = stateKey(next);
      if (!visited.has(key)) {
        visited.add(key);
        queue.push(next);
      }
    }
  }

  assert.ok(won, `expected a winning route; searched ${visited.size} states`);
});
