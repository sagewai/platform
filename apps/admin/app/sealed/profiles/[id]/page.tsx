"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { adminApi } from "@/utils/api";
import type { Profile, Revocation } from "@/utils/types";
import { ProfileForm } from "@/components/profile-form";
import { RevealButton } from "@/components/reveal-button";
import { RevokeButton } from "@/components/revoke-button";

export default function ProfileDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [revocations, setRevocations] = useState<Revocation[]>([]);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [p, revs] = await Promise.all([
        adminApi.getProfileFull(id),
        adminApi.listRevocations({ profile_id: id }),
      ]);
      setProfile(p);
      setRevocations(revs);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (loading) return <div>Loading…</div>;
  if (!profile) return <div className="text-rose-600">Profile not found.</div>;

  if (editing) {
    return (
      <div className="max-w-3xl mx-auto">
        <h1 className="text-2xl font-bold mb-md">Edit {profile.name}</h1>
        <ProfileForm
          initial={profile}
          onSaved={(p) => {
            setProfile(p);
            setEditing(false);
          }}
        />
        <button
          onClick={() => setEditing(false)}
          className="mt-3 text-xs text-neutral-600 hover:underline"
        >
          Cancel
        </button>
      </div>
    );
  }

  async function onDelete() {
    if (!confirm(`Delete profile ${profile!.id}? This cannot be undone.`)) return;
    await adminApi.deleteProfile(profile!.id);
    router.push("/sealed/profiles");
  }

  return (
    <div className="max-w-3xl mx-auto space-y-md">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{profile.name}</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setEditing(true)}
            className="rounded border px-3 py-1.5 text-sm"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            className="rounded bg-rose-600 px-3 py-1.5 text-sm text-white"
          >
            Delete
          </button>
        </div>
      </div>

      <section>
        <h2 className="font-semibold mb-2">Metadata</h2>
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt className="text-neutral-500">ID</dt><dd className="font-mono">{profile.id}</dd>
          <dt className="text-neutral-500">Description</dt><dd>{profile.description || "—"}</dd>
          <dt className="text-neutral-500">Owner</dt><dd>{profile.owner ?? "—"}</dd>
          <dt className="text-neutral-500">Tags</dt><dd>{profile.tags.join(", ") || "—"}</dd>
          <dt className="text-neutral-500">Allowed workflows</dt>
          <dd>{profile.allowed_workflows.length === 0 ? "all" : profile.allowed_workflows.join(", ")}</dd>
          <dt className="text-neutral-500">Last rotated</dt>
          <dd>{profile.last_rotated_at ? new Date(profile.last_rotated_at).toLocaleString() : "—"}</dd>
        </dl>
      </section>

      <section>
        <h2 className="font-semibold mb-2">Environment variables</h2>
        {Object.keys(profile.env).length === 0 ? (
          <div className="text-neutral-500 text-sm">No env variables.</div>
        ) : (
          <table className="text-sm border-collapse">
            <tbody>
              {Object.entries(profile.env).map(([k, v]) => (
                <tr key={k} className="border-b">
                  <td className="font-mono pr-4">{k}</td>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="font-semibold mb-2">Secrets</h2>
        {profile.secret_keys.length === 0 ? (
          <div className="text-neutral-500 text-sm">No secrets.</div>
        ) : (
          <table className="text-sm border-collapse">
            <tbody>
              {profile.secret_keys.map((k) => {
                const activeRevocation = revocations.find(
                  (r) => r.secret_key === k && r.lifted_at === null
                );
                return (
                  <tr key={k} className="border-b">
                    <td className="font-mono pr-4">{k}</td>
                    <td>
                      {activeRevocation ? (
                        <span
                          className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-700"
                          title={`Reason: ${activeRevocation.reason}\nRevoked: ${new Date(activeRevocation.revoked_at).toLocaleString()}`}
                        >
                          Revoked
                        </span>
                      ) : (
                        <RevealButton profileId={profile.id} secretKey={k} />
                      )}
                    </td>
                    <td>
                      <RevokeButton
                        profileId={profile.id}
                        secretKey={k}
                        onRevoked={refresh}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
