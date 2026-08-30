import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

function resolveConfiguredPath(value: string): string {
  const expanded = value === '~'
    ? os.homedir()
    : value.startsWith('~/')
      ? path.join(os.homedir(), value.slice(2))
      : value;
  return path.resolve(expanded);
}

const sagewaiHome = resolveConfiguredPath(
  process.env.SAGEWAI_HOME ?? path.join(os.homedir(), '.sagewai'),
);
const STATE_PATH = resolveConfiguredPath(
  process.env.SAGEWAI_ADMIN_UI_STATE_FILE
    ?? path.join(sagewaiHome, 'config', 'admin-ui-state.json'),
);

interface AdminState {
  firstMissionCelebrated?: boolean;
  [key: string]: unknown;
}

async function read(): Promise<AdminState> {
  try {
    return JSON.parse(await fs.readFile(STATE_PATH, 'utf8')) as AdminState;
  } catch {
    return {};
  }
}

async function write(s: AdminState): Promise<void> {
  await fs.mkdir(path.dirname(STATE_PATH), { recursive: true });
  await fs.writeFile(STATE_PATH, JSON.stringify(s, null, 2), 'utf8');
}

export async function shouldCelebrateFirstMission(): Promise<boolean> {
  const s = await read();
  return !s.firstMissionCelebrated;
}

export async function markFirstMissionCelebrated(): Promise<void> {
  await write({ ...(await read()), firstMissionCelebrated: true });
}
