"use client";

import { useState } from "react";

interface Props {
  secretKeys: string[];
  value: Record<string, string[]>;
  onChange: (next: Record<string, string[]>) => void;
}

const SUGGESTED_TOOLS = ["claude-code", "codex", "gemini", "shell", "git", "python"];

export function AclMatrix({ secretKeys, value, onChange }: Props) {
  const [pendingTool, setPendingTool] = useState("");

  const tools = Array.from(new Set([...SUGGESTED_TOOLS, ...Object.keys(value)]));

  const isChecked = (tool: string, key: string) =>
    Object.prototype.hasOwnProperty.call(value, tool) && value[tool].includes(key);

  const toggle = (tool: string, key: string) => {
    const current = value[tool] ?? [];
    const next = current.includes(key)
      ? current.filter((k) => k !== key)
      : [...current, key];
    onChange({ ...value, [tool]: next });
  };

  const addTool = () => {
    const name = pendingTool.trim();
    if (!name) return;
    if (Object.prototype.hasOwnProperty.call(value, name)) return;
    onChange({ ...value, [name]: [] });
    setPendingTool("");
  };

  return (
    <div className="acl-matrix">
      <h4>Per-CLI access (optional)</h4>
      <p className="text-sm text-muted">
        Tools without a row see all secrets (default). Empty row = deny all secrets.
      </p>
      {secretKeys.length === 0 ? (
        <p>Add secrets above to configure per-CLI access.</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr>
              <th>CLI</th>
              {secretKeys.map((k) => <th key={k}>{k}</th>)}
            </tr>
          </thead>
          <tbody>
            {tools.map((tool) => (
              <tr key={tool}>
                <td>{tool}</td>
                {secretKeys.map((k) => (
                  <td key={k}>
                    <input
                      type="checkbox"
                      checked={isChecked(tool, k)}
                      onChange={() => toggle(tool, k)}
                      aria-label={`${tool} can access ${k}`}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-2">
        <input
          type="text"
          placeholder="custom-tool-name"
          value={pendingTool}
          onChange={(e) => setPendingTool(e.target.value)}
        />
        <button type="button" onClick={addTool}>
          + Add CLI
        </button>
      </div>
    </div>
  );
}
