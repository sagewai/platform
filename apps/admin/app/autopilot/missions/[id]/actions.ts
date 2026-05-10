'use server';

import {
  shouldCelebrateFirstMission,
  markFirstMissionCelebrated,
} from '@/lib/admin-state';

/** Returns true exactly once per machine instance, then false forever. */
export async function consumeFirstMissionFlag(): Promise<boolean> {
  if (!(await shouldCelebrateFirstMission())) return false;
  await markFirstMissionCelebrated();
  return true;
}
