'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { adminApi } from '@/utils/api';
import { setTokens } from '@/utils/auth';
import { Button } from '@/components/ui/legacy';

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await adminApi.register(email, password, displayName);
      setTokens(data.access_token);
      router.push('/');
    } catch {
      setError('Registration failed. Email may already be in use.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg-deep relative overflow-hidden">
      {/* Decorative gradient orbs */}
      <div className="absolute top-[-20%] left-[-10%] w-[600px] h-[600px] rounded-full opacity-[0.07] blur-[120px]" style={{ background: 'radial-gradient(circle, #9C27B0, transparent)' }} />
      <div className="absolute bottom-[-20%] right-[-10%] w-[600px] h-[600px] rounded-full opacity-[0.07] blur-[120px]" style={{ background: 'radial-gradient(circle, #26C6DA, transparent)' }} />

      <div className="w-[420px] relative z-10">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold font-[family-name:var(--font-heading)] m-0 mb-2">
            <span className="bg-clip-text text-transparent" style={{ backgroundImage: 'var(--gradient-brand)' }}>SAGEWAI</span>
          </h1>
          <p className="m-0 text-text-secondary text-sm">Create your account</p>
        </div>

        {/* Card */}
        <div className="bg-surface-dark rounded-xl border border-border-dark p-8 shadow-2xl shadow-black/20">
          {error && (
            <div className="bg-error/10 border border-error/20 text-error px-3.5 py-2.5 rounded-lg mb-6 text-[13px]">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <label className="block mb-4">
              <span className="text-[13px] font-medium text-text-secondary block mb-1.5">Name</span>
              <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)}
                className="w-full px-3.5 py-2.5 border border-border-dark rounded-lg text-sm bg-bg-deep box-border focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none transition-colors"
                placeholder="Your name" />
            </label>
            <label className="block mb-4">
              <span className="text-[13px] font-medium text-text-secondary block mb-1.5">Email</span>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                className="w-full px-3.5 py-2.5 border border-border-dark rounded-lg text-sm bg-bg-deep box-border focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none transition-colors"
                placeholder="you@example.com" />
            </label>
            <label className="block mb-6">
              <span className="text-[13px] font-medium text-text-secondary block mb-1.5">Password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8}
                className="w-full px-3.5 py-2.5 border border-border-dark rounded-lg text-sm bg-bg-deep box-border focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none transition-colors"
                placeholder="At least 8 characters" />
            </label>
            <Button type="submit" disabled={loading} className="w-full">
              {loading ? 'Creating account...' : 'Create Account'}
            </Button>
          </form>

          <div className="mt-5 text-center text-[13px] text-text-muted">
            Already have an account?{' '}
            <Link href="/login" className="text-primary no-underline font-medium hover:underline">Sign in</Link>
          </div>
        </div>

        {/* Footer */}
        <div className="text-center mt-8 text-xs text-text-muted/50">
          Powered by Sagewai SDK
        </div>
      </div>
    </div>
  );
}
