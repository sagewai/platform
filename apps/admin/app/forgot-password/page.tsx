'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Button } from '@sagecurator/ui';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Stub — would call adminApi.forgotPassword(email)
    setSent(true);
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
          <p className="m-0 text-text-secondary text-sm">
            Enter your email and we&apos;ll send a reset link.
          </p>
        </div>

        {/* Card */}
        <div className="bg-surface-dark rounded-xl border border-border-dark p-8 shadow-2xl shadow-black/20">
          {sent ? (
            <div className="bg-success/10 border border-success/20 text-success p-3.5 rounded-lg text-center text-sm">
              If an account exists with that email, a reset link has been sent.
            </div>
          ) : (
            <form onSubmit={handleSubmit}>
              <label className="block mb-6">
                <span className="text-[13px] font-medium text-text-secondary block mb-1.5">Email</span>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                  className="w-full px-3.5 py-2.5 border border-border-dark rounded-lg text-sm bg-bg-deep box-border focus:border-primary focus:ring-1 focus:ring-primary/30 outline-none transition-colors"
                  placeholder="you@example.com" />
              </label>
              <Button type="submit" className="w-full">
                Send Reset Link
              </Button>
            </form>
          )}

          <div className="mt-5 text-center text-[13px] text-text-muted">
            <Link href="/login" className="text-primary no-underline hover:underline">Back to sign in</Link>
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
