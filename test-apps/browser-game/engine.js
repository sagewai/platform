const DIRECTIONS = {
  up: [0, -1],
  down: [0, 1],
  left: [-1, 0],
  right: [1, 0],
};

export const DEFAULT_LEVEL = {
  width: 12,
  height: 8,
  start: [0, 7],
  exit: [11, 0],
  lives: 3,
  walls: [
    [2, 1], [3, 1], [4, 1], [7, 1], [8, 1], [9, 1],
    [1, 3], [2, 3], [5, 3], [6, 3], [9, 3], [10, 3],
    [3, 5], [4, 5], [7, 5], [8, 5], [10, 6],
  ],
  evidence: [[1, 6], [4, 6], [6, 4], [8, 2], [10, 1]],
  hazards: [
    { path: [[5, 6], [6, 6], [6, 7], [5, 7]] },
    { path: [[10, 4], [11, 4], [11, 5], [10, 5]] },
    { path: [[4, 2], [5, 2], [6, 2], [5, 2]] },
  ],
};

const copyPoint = ([x, y]) => [x, y];
const pointKey = ([x, y]) => `${x}:${y}`;
const samePoint = (left, right) => left[0] === right[0] && left[1] === right[1];

const normalizeLevel = (level) => ({
  width: level.width,
  height: level.height,
  start: copyPoint(level.start),
  exit: copyPoint(level.exit),
  lives: level.lives,
  walls: level.walls.map(copyPoint),
  evidence: level.evidence.map(copyPoint),
  hazards: level.hazards.map(({ path }) => ({ path: path.map(copyPoint) })),
});

export function createGame(level = DEFAULT_LEVEL) {
  const normalized = normalizeLevel(level);
  return {
    level: normalized,
    player: copyPoint(normalized.start),
    exit: copyPoint(normalized.exit),
    walls: normalized.walls.map(copyPoint),
    evidence: normalized.evidence.map(copyPoint),
    hazards: normalized.hazards.map(({ path }) => ({
      path: path.map(copyPoint),
      pathIndex: 0,
      position: copyPoint(path[0]),
    })),
    lives: normalized.lives,
    score: 0,
    turn: 0,
    status: "playing",
    message: "Collect every signal, then reach the uplink.",
  };
}

export function restart(state) {
  return createGame(state.level);
}

function isOpen(state, point) {
  const [x, y] = point;
  if (x < 0 || y < 0 || x >= state.level.width || y >= state.level.height) {
    return false;
  }
  const walls = new Set(state.walls.map(pointKey));
  return !walls.has(pointKey(point));
}

function collide(state) {
  return state.hazards.some(({ position }) => samePoint(position, state.player));
}

function loseLife(state) {
  const lives = state.lives - 1;
  if (lives === 0) {
    return {
      ...state,
      lives,
      status: "lost",
      message: "The glitches overwhelmed the route. Restart and try again.",
    };
  }
  return {
    ...state,
    lives,
    player: copyPoint(state.level.start),
    message: "Signal lost. Route restored to the last safe point.",
  };
}

function advanceHazards(hazards) {
  return hazards.map((hazard) => {
    const pathIndex = (hazard.pathIndex + 1) % hazard.path.length;
    return {
      ...hazard,
      pathIndex,
      position: copyPoint(hazard.path[pathIndex]),
    };
  });
}

export function move(state, direction) {
  if (state.status !== "playing") {
    return state;
  }
  const delta = DIRECTIONS[direction];
  if (!delta) {
    return state;
  }
  const target = [state.player[0] + delta[0], state.player[1] + delta[1]];
  if (!isOpen(state, target)) {
    return state;
  }

  let next = {
    ...state,
    player: target,
    turn: state.turn + 1,
    message: "Keep moving. The glitches move when you do.",
  };

  const collected = next.evidence.some((point) => samePoint(point, target));
  if (collected) {
    next = {
      ...next,
      evidence: next.evidence.filter((point) => !samePoint(point, target)),
      score: next.score + 100,
      message: "Signal secured.",
    };
  }

  if (collide(next)) {
    return loseLife(next);
  }

  next = {
    ...next,
    hazards: advanceHazards(next.hazards),
  };
  if (collide(next)) {
    return loseLife(next);
  }

  if (samePoint(next.player, next.exit)) {
    if (next.evidence.length === 0) {
      return {
        ...next,
        score: next.score + 500,
        status: "won",
        message: "Uplink restored. Route complete!",
      };
    }
    return {
      ...next,
      message: `${next.evidence.length} signal${next.evidence.length === 1 ? "" : "s"} still missing.`,
    };
  }

  return next;
}
