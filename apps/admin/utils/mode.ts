/** Sagewai deployment mode helper. */

export const SAGEWAI_MODE = process.env.NEXT_PUBLIC_SAGEWAI_MODE ?? 'self-hosted';
export const isCloud = SAGEWAI_MODE === 'cloud';
