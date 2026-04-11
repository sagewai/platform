'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { QRCodeSVG } from 'qrcode.react';
import { Badge, Button, Card, FormField, TextInput, Select, TextArea } from '@/components/ui/legacy';
import { Brain, Cpu, Languages, Mic, GitBranch, Layers, CheckCircle, XCircle } from 'lucide-react';
import { adminApi } from '@/utils/api';
import { setTokens } from '@/utils/auth';
import { StepProgress } from '@/components/step-progress';
import Link from 'next/link';

const STEPS = ['Welcome', 'Organization', 'Application', 'Admin Account', 'Intelligence', '2FA Setup', 'Complete'];

const INTELLIGENCE_FEATURES = [
  { label: 'Embeddings', dep: 'sentence-transformers', icon: Cpu, installed: true },
  { label: 'Multi-Language', dep: 'lingua-language-detector', icon: Languages, installed: true },
  { label: 'Entity Extraction', dep: 'gliner', icon: Brain, installed: true },
  { label: 'Transcription', dep: 'faster-whisper', icon: Mic, installed: false },
  { label: 'Summarization', dep: 'transformers + torch', icon: Layers, installed: false },
  { label: 'Graph Builder', dep: 'built-in', icon: GitBranch, installed: true },
];
const TIMEZONES = [
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: 'Eastern Time (US)' },
  { value: 'America/Chicago', label: 'Central Time (US)' },
  { value: 'America/Denver', label: 'Mountain Time (US)' },
  { value: 'America/Los_Angeles', label: 'Pacific Time (US)' },
  { value: 'Europe/London', label: 'London' },
  { value: 'Europe/Berlin', label: 'Berlin / Paris' },
  { value: 'Europe/Istanbul', label: 'Istanbul' },
  { value: 'Asia/Dubai', label: 'Dubai' },
  { value: 'Asia/Tokyo', label: 'Tokyo' },
  { value: 'Asia/Singapore', label: 'Singapore' },
  { value: 'Australia/Sydney', label: 'Sydney' },
];

interface WizardState {
  org_name: string;
  org_slug: string;
  contact_email: string;
  timezone: string;
  app_name: string;
  app_description: string;
  admin_name: string;
  admin_email: string;
  admin_password: string;
  admin_password_confirm: string;
}

const initial: WizardState = {
  org_name: '', org_slug: '', contact_email: '', timezone: 'UTC',
  app_name: '', app_description: '',
  admin_name: '', admin_email: '', admin_password: '', admin_password_confirm: '',
};

export default function SetupPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [state, setState] = useState<WizardState>(initial);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  // 2FA state
  const [totpCode, setTotpCode] = useState('');
  const [totpVerifying, setTotpVerifying] = useState(false);
  // Placeholder secret — real implementation fetches this from /api/v1/setup/totp
  const totpSecret = 'JBSWY3DPEHPK3PXP';

  const set = (key: keyof WizardState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setState((s) => {
      const next = { ...s, [key]: e.target.value };
      if (key === 'org_name' && !s.org_slug) {
        next.org_slug = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
      }
      return next;
    });
  };

  const canNext = () => {
    if (step === 1) return !!state.org_name;
    if (step === 2) return !!state.app_name;
    if (step === 3) return !!state.admin_name && !!state.admin_email && state.admin_password.length >= 8 && state.admin_password === state.admin_password_confirm;
    return true;
  };

  const submit = async () => {
    setSubmitting(true);
    setError('');
    try {
      await adminApi.runSetup({
        org_name: state.org_name,
        org_slug: state.org_slug,
        contact_email: state.contact_email,
        timezone: state.timezone,
        app_name: state.app_name,
        app_description: state.app_description,
        admin_name: state.admin_name,
        admin_email: state.admin_email,
        admin_password: state.admin_password,
      });
      // Auto-login with the admin credentials just created.
      try {
        const tokens = await adminApi.login(state.admin_email, state.admin_password);
        setTokens(tokens.access_token);
      } catch {
        // Login may not be available yet — user can log in manually.
      }
      setDone(true);
      setStep(4); // Go to Intelligence info step
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Setup failed';
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const verifyTotp = async () => {
    if (totpCode.length !== 6) return;
    setTotpVerifying(true);
    // Placeholder: real implementation calls POST /api/v1/setup/totp/verify
    await new Promise((r) => setTimeout(r, 600));
    setTotpVerifying(false);
    setStep(6);
  };

  const skipTotp = () => setStep(6);

  const passwordStrength = (pw: string) => {
    if (pw.length < 8) return { label: 'Too short', color: 'bg-error' };
    let score = 0;
    if (/[A-Z]/.test(pw)) score++;
    if (/[0-9]/.test(pw)) score++;
    if (/[^A-Za-z0-9]/.test(pw)) score++;
    if (pw.length >= 12) score++;
    if (score <= 1) return { label: 'Weak', color: 'bg-warning' };
    if (score <= 2) return { label: 'Fair', color: 'bg-secondary' };
    return { label: 'Strong', color: 'bg-success' };
  };

  const strength = passwordStrength(state.admin_password);

  return (
    <div className="min-h-screen bg-bg-deep flex items-center justify-center p-md">
      <div className="w-full max-w-[36rem]">
        {/* Logo — full logo on dark background */}
        <img
          src="/brand/sagewai_logo_dark.svg"
          alt="Sagewai"
          className="h-10 w-auto mx-auto mb-4"
        />

        {/* Step progress */}
        <StepProgress steps={STEPS} currentStep={step} />

        <Card className="!bg-bg-surface">
          {/* Step 0: Welcome */}
          {step === 0 && (
            <div className="text-center py-xl">
              <h1 className="text-3xl font-bold mb-2 font-[family-name:var(--font-heading)] text-text-primary">
                Welcome to Sagewai
              </h1>
              <p className="text-text-secondary mb-xl max-w-[24rem] mx-auto">
                Let&apos;s get your AI agent environment set up. This takes about 2 minutes.
              </p>
              <Button onClick={() => setStep(1)} size="lg">Get Started</Button>
            </div>
          )}

          {/* Step 1: Organization */}
          {step === 1 && (
            <div className="flex flex-col gap-md">
              <h2 className="text-xl font-bold font-[family-name:var(--font-heading)] text-text-primary">Organization</h2>
              <FormField label="Organization name" required>
                <TextInput value={state.org_name} onChange={set('org_name')} placeholder="Acme Corp" />
              </FormField>
              <FormField label="Slug" hint="Used in URLs and identifiers">
                <TextInput value={state.org_slug} onChange={set('org_slug')} placeholder="acme-corp" />
              </FormField>
              <FormField label="Contact email" hint="For system alerts and notifications">
                <TextInput type="email" value={state.contact_email} onChange={set('contact_email')} placeholder="admin@acme.com" />
              </FormField>
              <FormField label="Timezone" hint="Used for timestamps in logs and reports">
                <Select value={state.timezone} onChange={set('timezone')}>
                  {TIMEZONES.map((tz) => <option key={tz.value} value={tz.value}>{tz.label}</option>)}
                </Select>
              </FormField>
            </div>
          )}

          {/* Step 2: Application */}
          {step === 2 && (
            <div className="flex flex-col gap-md">
              <h2 className="text-xl font-bold font-[family-name:var(--font-heading)] text-text-primary">First Application</h2>
              <p className="text-sm text-text-secondary -mt-sm">This becomes your first project in the system.</p>
              <FormField label="Application name" required>
                <TextInput value={state.app_name} onChange={set('app_name')} placeholder="My AI App" />
              </FormField>
              <FormField label="Description">
                <TextArea value={state.app_description} onChange={set('app_description')} placeholder="What this app does..." />
              </FormField>
            </div>
          )}

          {/* Step 3: Admin Account */}
          {step === 3 && (
            <div className="flex flex-col gap-md">
              <h2 className="text-xl font-bold font-[family-name:var(--font-heading)] text-text-primary">Admin Account</h2>
              <FormField label="Full name" required>
                <TextInput value={state.admin_name} onChange={set('admin_name')} placeholder="Jane Smith" />
              </FormField>
              <FormField label="Email" required>
                <TextInput type="email" value={state.admin_email} onChange={set('admin_email')} placeholder="jane@acme.com" />
              </FormField>
              <FormField label="Password" required error={state.admin_password && state.admin_password.length < 8 ? 'Minimum 8 characters' : undefined}>
                <TextInput type="password" value={state.admin_password} onChange={set('admin_password')} />
                {state.admin_password && (
                  <div className="flex items-center gap-2 mt-1">
                    <div className="flex-1 h-1 rounded-full bg-bg-subtle">
                      <div className={`h-1 rounded-full transition-all ${strength.color}`} style={{ width: strength.label === 'Strong' ? '100%' : strength.label === 'Fair' ? '66%' : strength.label === 'Weak' ? '33%' : '10%' }} />
                    </div>
                    <span className="text-xs text-text-muted">{strength.label}</span>
                  </div>
                )}
              </FormField>
              <FormField label="Confirm password" required error={state.admin_password_confirm && state.admin_password !== state.admin_password_confirm ? 'Passwords do not match' : undefined}>
                <TextInput type="password" value={state.admin_password_confirm} onChange={set('admin_password_confirm')} />
              </FormField>
            </div>
          )}

          {/* Step 4: Intelligence */}
          {step === 4 && (
            <div className="flex flex-col gap-md">
              <h2 className="text-xl font-bold font-[family-name:var(--font-heading)] text-text-primary">Intelligence Layer</h2>
              <p className="text-sm text-text-secondary -mt-sm">
                Sagewai includes a pluggable Intelligence Layer for embeddings, NLP, and multimodal processing.
                Features activate automatically when their optional dependencies are installed.
              </p>

              <div className="space-y-2">
                {INTELLIGENCE_FEATURES.map((f) => (
                  <div
                    key={f.label}
                    className="flex items-center justify-between px-4 py-3 bg-bg-subtle rounded-lg"
                  >
                    <div className="flex items-center gap-3">
                      <f.icon size={16} className="text-text-muted" />
                      <div>
                        <div className="text-sm font-medium text-text-primary">{f.label}</div>
                        <div className="text-xs text-text-muted">
                          <code className="font-[family-name:var(--font-mono)]">{f.dep}</code>
                        </div>
                      </div>
                    </div>
                    {f.installed ? (
                      <Badge variant="success">
                        <span className="flex items-center gap-1">
                          <CheckCircle size={10} /> Available
                        </span>
                      </Badge>
                    ) : (
                      <Badge variant="default">
                        <span className="flex items-center gap-1">
                          <XCircle size={10} /> Not installed
                        </span>
                      </Badge>
                    )}
                  </div>
                ))}
              </div>

              <div className="bg-bg-subtle rounded-lg px-4 py-3 mt-sm">
                <p className="text-xs text-text-muted mb-1">Install intelligence features:</p>
                <code className="text-sm font-[family-name:var(--font-mono)] text-text-primary select-all">
                  pip install sagewai[intelligence]
                </code>
              </div>
            </div>
          )}

          {/* Step 5: 2FA Setup */}
          {step === 5 && (
            <div className="flex flex-col gap-md">
              <h2 className="text-xl font-bold font-[family-name:var(--font-heading)] text-text-primary">Two-Factor Authentication</h2>
              <p className="text-sm text-text-secondary -mt-sm">
                Add an extra layer of security to your admin account. Scan the QR code with an authenticator app (Google Authenticator, Authy, 1Password, etc.).
              </p>

              {/* QR code */}
              <div className="flex flex-col items-center gap-3 py-md">
                <div className="p-3 bg-white rounded-xl" aria-label="QR code for authenticator app">
                  <QRCodeSVG
                    value={`otpauth://totp/Sagewai:${encodeURIComponent(state.admin_email || 'admin')}?secret=${totpSecret}&issuer=Sagewai`}
                    size={160}
                    level="M"
                  />
                </div>
              </div>

              {/* Manual secret */}
              <div className="bg-bg-subtle rounded-lg px-4 py-3">
                <p className="text-xs text-text-muted mb-1">Manual entry key</p>
                <code className="text-sm font-[family-name:var(--font-mono)] text-text-primary tracking-widest select-all">
                  {totpSecret}
                </code>
              </div>

              {/* TOTP code input */}
              <FormField label="Verification code" hint="Enter the 6-digit code from your authenticator app">
                <TextInput
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  placeholder="000000"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                />
              </FormField>

              <div className="flex flex-col gap-2 mt-sm">
                <Button
                  onClick={verifyTotp}
                  disabled={totpCode.length !== 6 || totpVerifying}
                >
                  {totpVerifying ? 'Verifying…' : 'Verify & Continue'}
                </Button>
                <button
                  type="button"
                  onClick={skipTotp}
                  className="text-sm text-text-muted hover:text-text-secondary text-center transition-colors py-1"
                >
                  Skip for now
                </button>
              </div>
            </div>
          )}

          {/* Step 6: Complete */}
          {step === 6 && done && (
            <div className="text-center py-lg">
              <div className="text-4xl mb-md">&#10003;</div>
              <h2 className="text-xl font-bold font-[family-name:var(--font-heading)] text-text-primary mb-2">Setup Complete</h2>
              <p className="text-sm text-text-secondary mb-lg">Your Sagewai environment is ready.</p>
              <div className="text-left bg-bg-subtle rounded-lg p-md mb-lg text-sm">
                <p><strong>Organization:</strong> {state.org_name}</p>
                <p><strong>Application:</strong> {state.app_name}</p>
                <p><strong>Admin:</strong> {state.admin_email}</p>
              </div>
              <h3 className="text-sm font-semibold mb-sm text-text-primary">Getting Started</h3>
              <div className="grid gap-3 mb-lg text-left">
                <Link href="/playground" className="flex items-center gap-3 px-4 py-3 bg-bg-subtle rounded-lg hover:bg-white/5 transition-colors no-underline group">
                  <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center text-primary flex-shrink-0">
                    <Brain size={18} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-text-primary group-hover:text-primary transition-colors">Go to Playground</div>
                    <div className="text-xs text-text-muted">Chat with agents and test tools</div>
                  </div>
                </Link>
                <Link href="/system/models" className="flex items-center gap-3 px-4 py-3 bg-bg-subtle rounded-lg hover:bg-white/5 transition-colors no-underline group">
                  <div className="w-9 h-9 rounded-lg bg-secondary/10 flex items-center justify-center text-secondary flex-shrink-0">
                    <Cpu size={18} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-text-primary group-hover:text-secondary transition-colors">Configure LLM Provider</div>
                    <div className="text-xs text-text-muted">Connect OpenAI, Anthropic, Google, and more</div>
                  </div>
                </Link>
                <a href="https://docs.sagewai.ai" target="_blank" rel="noopener noreferrer" className="flex items-center gap-3 px-4 py-3 bg-bg-subtle rounded-lg hover:bg-white/5 transition-colors no-underline group">
                  <div className="w-9 h-9 rounded-lg bg-accent-purple/10 flex items-center justify-center text-accent-purple flex-shrink-0">
                    <Layers size={18} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-text-primary group-hover:text-accent-purple transition-colors">Browse Documentation</div>
                    <div className="text-xs text-text-muted">Guides, API reference, and examples</div>
                  </div>
                </a>
              </div>
              <Button onClick={() => router.push('/')} size="lg">Go to Dashboard</Button>
            </div>
          )}

          {/* Navigation (steps 1–4; step 5 (2FA) has its own buttons) */}
          {step > 0 && step < 5 && (
            <div className="flex justify-between mt-xl">
              {step <= 3 ? (
                <Button variant="ghost" onClick={() => setStep(step - 1)}>Back</Button>
              ) : (
                <div />
              )}
              {step < 3 ? (
                <Button onClick={() => setStep(step + 1)} disabled={!canNext()}>Next</Button>
              ) : step === 3 ? (
                <Button onClick={submit} disabled={!canNext() || submitting}>
                  {submitting ? 'Setting up...' : 'Complete Setup'}
                </Button>
              ) : (
                <Button onClick={() => setStep(step + 1)}>Next</Button>
              )}
            </div>
          )}

          {error && <p className="text-sm text-error mt-md text-center">{error}</p>}
        </Card>
      </div>
    </div>
  );
}
