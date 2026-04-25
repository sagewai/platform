"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { adminApi } from "@/utils/api";
import type { ProfileMetadata } from "@/utils/types";

export default function ProfilesListPage() {
  const [profiles, setProfiles] = useState<ProfileMetadata[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminApi.listProfiles().then(setProfiles).finally(() => setLoading(false));
  }, []);

  if (loading) return <div>Loading…</div>;

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-md">
        <h1 className="text-2xl font-bold">Security Profiles</h1>
        <Link
          href="/sealed/profiles/new"
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white"
        >
          + New Profile
        </Link>
      </div>
      <table className="w-full text-sm border-collapse">
        <thead className="text-left border-b">
          <tr>
            <th>Name</th>
            <th>Owner</th>
            <th>Tags</th>
            <th>Keys</th>
            <th>Last rotated</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((p) => (
            <tr key={p.id} className="border-b hover:bg-neutral-50">
              <td>
                <Link href={`/sealed/profiles/${p.id}`} className="text-blue-600">
                  {p.name}
                </Link>
              </td>
              <td>{p.owner ?? "—"}</td>
              <td>{p.tags.join(", ")}</td>
              <td>
                {p.secret_keys.length} secrets, {Object.keys(p.env).length} env
              </td>
              <td>{p.last_rotated_at ? new Date(p.last_rotated_at).toLocaleString() : "—"}</td>
            </tr>
          ))}
          {profiles.length === 0 && (
            <tr>
              <td colSpan={5} className="text-center text-neutral-500 py-md">
                No profiles yet. <Link href="/sealed/profiles/new" className="text-blue-600">Create the first.</Link>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
