"use client";

import { useRouter } from "next/navigation";
import { ProfileForm } from "@/components/profile-form";

export default function NewProfilePage() {
  const router = useRouter();
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-md">New Security Profile</h1>
      <ProfileForm onSaved={(p) => router.push(`/sealed/profiles/${p.id}`)} />
    </div>
  );
}
