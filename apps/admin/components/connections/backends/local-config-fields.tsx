// Copyright 2026 Ali Arda Diri, Berlin, Germany
// AGPL-3.0-or-later

/** Local backend takes no config — operator master key resolves via env / vault file. */
export function LocalConfigFields() {
  return (
    <p className="text-xs text-gray-500 mt-2" data-testid="local-config-fields">
      Local backend uses the platform master key (from <code>SAGEWAI_MASTER_KEY</code> env or the
      configured key file). No per-connection config needed.
    </p>
  );
}
