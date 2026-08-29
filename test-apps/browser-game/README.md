# Signal Runner

A dependency-free browser game used as a tangible Sagewai Work control-plane
target. The game has a pure deterministic engine, a canvas UI, keyboard and
touch controls, and no network calls.

## Run it

```bash
cd test-apps/browser-game
npm test
npm run serve
```

Open <http://localhost:4173>. Use the arrow keys, WASD, or the touch controls.
Collect all five yellow signals, avoid the pink glitches, and reach the green
uplink.

## Verification contract

The standalone application exposes the same command shape Sagewai's software
profile expects:

```bash
just smoke
```

That command performs JavaScript syntax checks and runs the deterministic engine
suite. It needs Node.js 20+ and `just`; it does not install packages, use the
network, or mutate external state.

## Suggested Sagewai work

Use one narrowly scoped change at a time, for example:

```text
In test-apps/browser-game, add a pause control. Pause must stop keyboard and
touch movement without changing score, lives, hazards, or collected signals.
Add deterministic engine tests and change nothing outside this app.
```

```text
In test-apps/browser-game, add a second level that starts only after the first
uplink is restored. Preserve the current first level and prove both levels are
reachable with deterministic tests. Do not add dependencies or network calls.
```
