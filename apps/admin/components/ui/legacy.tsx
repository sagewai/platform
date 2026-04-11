'use client';

/**
 * Legacy compat layer — re-exports the former `@sagecurator/ui` API using
 * shadcn/ui primitives and local implementations so admin can keep its
 * existing imports during the decommission without rewriting every page.
 *
 * Every call site that used to `import { X } from '@/components/ui/legacy'` now
 * `import { X } from '@/components/ui/legacy'`. The prop signatures are
 * preserved so most files need zero other changes.
 *
 * New code should NOT import from this file — use the individual shadcn
 * primitives in `@/components/ui/*` directly.
 */

import * as React from 'react';
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  type ReactNode,
} from 'react';
import { toast as sonnerToast } from 'sonner';
import { cn } from '@/lib/utils';
import { Button as ShadcnButton, buttonVariants } from '@/components/ui/button';
import { Skeleton as ShadcnSkeleton } from '@/components/ui/skeleton';
import { EmptyState as NewEmptyState } from '@/components/ui/empty-state';

// Re-export the sidebar context so existing imports of
// SidebarProvider/useSidebar/SidebarToggle keep working from the legacy path.
export {
  SidebarProvider,
  SidebarToggle,
  useSidebar,
} from '@/components/sidebar-context';

// ── Button ────────────────────────────────────────────────────────────────
// Legacy variants: primary | secondary | ghost | danger
// Legacy sizes: sm | md | lg
// Map these to shadcn's variants and sizes.
const LEGACY_BUTTON_VARIANT: Record<
  NonNullable<LegacyButtonProps['variant']>,
  React.ComponentProps<typeof ShadcnButton>['variant']
> = {
  primary: 'default',
  secondary: 'secondary',
  ghost: 'ghost',
  danger: 'destructive',
};

const LEGACY_BUTTON_SIZE: Record<
  NonNullable<LegacyButtonProps['size']>,
  React.ComponentProps<typeof ShadcnButton>['size']
> = {
  sm: 'sm',
  md: 'default',
  lg: 'lg',
};

export interface LegacyButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'size'> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  children,
  ...props
}: LegacyButtonProps) {
  return (
    <ShadcnButton
      variant={LEGACY_BUTTON_VARIANT[variant]}
      size={LEGACY_BUTTON_SIZE[size]}
      className={className}
      {...(props as React.ComponentProps<typeof ShadcnButton>)}
    >
      {children}
    </ShadcnButton>
  );
}

// ── Card ─────────────────────────────────────────────────────────────────
// Legacy Card is a simple div wrapper with an optional title prop —
// NOT the shadcn Card with sub-components.
interface CardProps {
  title?: string;
  children: ReactNode;
  className?: string;
}

export function Card({ title, children, className }: CardProps) {
  return (
    <div
      className={cn(
        'bg-card text-card-foreground rounded-lg border border-border p-6',
        className,
      )}
    >
      {title && (
        <h3 className="mt-0 mb-3 text-lg font-semibold font-[family-name:var(--font-heading)]">
          {title}
        </h3>
      )}
      {children}
    </div>
  );
}

// ── Badge ────────────────────────────────────────────────────────────────
export type BadgeVariant = 'default' | 'success' | 'error' | 'warning' | 'info';

const BADGE_VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default: 'bg-muted text-muted-foreground',
  success: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  error: 'bg-red-500/15 text-red-600 dark:text-red-400',
  warning: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  info: 'bg-cyan-500/15 text-cyan-600 dark:text-cyan-400',
};

interface BadgeProps {
  variant?: BadgeVariant;
  children: ReactNode;
  className?: string;
}

export function Badge({ variant = 'default', children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium',
        BADGE_VARIANT_CLASSES[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}

// ── Skeleton ─────────────────────────────────────────────────────────────
// Legacy API: `lines` prop renders N stacked skeleton bars.
interface SkeletonProps {
  className?: string;
  lines?: number;
}

export function Skeleton({ className, lines = 1 }: SkeletonProps) {
  if (lines === 1) {
    return <ShadcnSkeleton className={cn('h-4 w-full', className)} />;
  }
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <ShadcnSkeleton
          key={i}
          className="h-4"
          style={{ width: `${85 - i * 5}%` }}
        />
      ))}
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="rounded-lg border border-border bg-card p-6">
      <Skeleton lines={4} />
    </div>
  );
}

// ── EmptyState (legacy API) ──────────────────────────────────────────────
// Legacy signature: { title, description, actionLabel, onAction, icon: ReactNode }
// Wraps the new EmptyState which takes a LucideIcon component.
interface LegacyEmptyStateProps {
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: ReactNode;
}

export function EmptyState({
  title,
  description,
  actionLabel,
  onAction,
  icon,
}: LegacyEmptyStateProps) {
  return (
    <div className="w-full rounded-xl border border-border bg-card text-card-foreground px-6 py-12 text-center">
      {icon && (
        <div className="mx-auto mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground text-xl">
          {icon}
        </div>
      )}
      <h3 className="text-base font-semibold m-0 mb-1">{title}</h3>
      <p className="mx-auto max-w-xl text-sm text-muted-foreground m-0">{description}</p>
      {actionLabel && onAction && (
        <div className="mt-5 flex items-center justify-center">
          <Button onClick={onAction}>{actionLabel}</Button>
        </div>
      )}
    </div>
  );
}

// ── FormField + TextInput + TextArea + Select ───────────────────────────
// Preserve the FieldContext pattern so child inputs auto-pick up label ids.
interface FieldContextValue {
  inputId: string;
  descriptionId: string | undefined;
}

const FieldContext = createContext<FieldContextValue>({
  inputId: '',
  descriptionId: undefined,
});

function useFieldContext() {
  return useContext(FieldContext);
}

interface FormFieldProps {
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: ReactNode;
}

export function FormField({ label, required, error, hint, children }: FormFieldProps) {
  const inputId = useId();
  const descId = useId();
  const hasDescription = !!(error || hint);

  return (
    <FieldContext.Provider
      value={{ inputId, descriptionId: hasDescription ? descId : undefined }}
    >
      <div>
        <label
          htmlFor={inputId}
          className="block font-semibold mb-1.5 text-sm text-foreground"
        >
          {label}
          {required && (
            <>
              <span className="text-destructive ml-0.5" aria-hidden="true">
                *
              </span>
              <span className="sr-only"> (required)</span>
            </>
          )}
        </label>

        {children}

        {hint && !error && (
          <p id={descId} className="mt-1 text-xs text-muted-foreground">
            {hint}
          </p>
        )}
        {error && (
          <p id={descId} className="mt-1 text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </div>
    </FieldContext.Provider>
  );
}

const inputClass =
  'w-full px-3.5 py-2.5 rounded-md border border-input bg-transparent text-sm outline-none focus:border-ring transition-colors placeholder:text-muted-foreground';

export function TextInput({
  className,
  id,
  'aria-describedby': describedby,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  const { inputId, descriptionId } = useFieldContext();
  return (
    <input
      id={id ?? inputId}
      aria-describedby={describedby ?? descriptionId}
      {...props}
      className={cn(inputClass, className)}
    />
  );
}

export function TextArea({
  className,
  id,
  'aria-describedby': describedby,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { inputId, descriptionId } = useFieldContext();
  return (
    <textarea
      id={id ?? inputId}
      aria-describedby={describedby ?? descriptionId}
      {...props}
      className={cn(inputClass, 'resize-y min-h-[80px]', className)}
    />
  );
}

export function Select({
  className,
  id,
  'aria-describedby': describedby,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  const { inputId, descriptionId } = useFieldContext();
  return (
    <select
      id={id ?? inputId}
      aria-describedby={describedby ?? descriptionId}
      {...props}
      className={cn(inputClass, className)}
    />
  );
}

// ── PageLayout ───────────────────────────────────────────────────────────
interface PageLayoutProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function PageLayout({ title, description, actions, children }: PageLayoutProps) {
  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-start justify-between mb-lg">
        <div>
          <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">
            {title}
          </h1>
          {description && (
            <p className="mt-0 text-sm text-muted-foreground">{description}</p>
          )}
        </div>
        {actions && <div className="flex gap-sm">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

// ── Dialog + ConfirmDialog ───────────────────────────────────────────────
// Uses native <dialog> for API parity with the legacy implementation.
interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}

export function Dialog({ open, onClose, title, children, actions }: DialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    else if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      className="backdrop:bg-black/40 bg-card text-card-foreground rounded-lg border border-border p-0 max-w-[32rem] w-full shadow-xl"
    >
      <div className="p-6">
        <h2 className="mt-0 mb-4 text-lg font-bold font-[family-name:var(--font-heading)]">
          {title}
        </h2>
        <div className="text-sm text-muted-foreground">{children}</div>
        {actions && <div className="flex justify-end gap-2 mt-6">{actions}</div>}
      </div>
    </dialog>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
  confirmText?: string;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = 'Delete',
  confirmText,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('');

  useEffect(() => {
    if (!open) setTyped('');
  }, [open]);

  const canConfirm = confirmText ? typed === confirmText : true;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onClick={onConfirm} disabled={!canConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p>{message}</p>
      {confirmText && (
        <div className="mt-4">
          <p className="text-xs text-muted-foreground mb-1">
            Type <strong className="font-mono">{confirmText}</strong> to confirm:
          </p>
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            className={cn(inputClass, 'font-mono')}
          />
        </div>
      )}
    </Dialog>
  );
}

// ── Tabs (legacy API: {tabs, active, onChange}) ──────────────────────────
interface Tab {
  id: string;
  label: string;
}

interface TabsProps {
  tabs: Tab[];
  active: string;
  onChange: (id: string) => void;
}

export function Tabs({ tabs, active, onChange }: TabsProps) {
  return (
    <div className="flex border-b border-border mb-lg" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={tab.id === active}
          onClick={() => onChange(tab.id)}
          className={cn(
            'px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer bg-transparent border-x-0 border-t-0',
            tab.id === active
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground',
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// ── Toggle ───────────────────────────────────────────────────────────────
interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}

export function Toggle({ checked, onChange, label, disabled }: ToggleProps) {
  return (
    <label className="inline-flex items-center gap-2.5 cursor-pointer select-none">
      <button
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2 disabled:opacity-50 disabled:cursor-not-allowed',
          checked ? 'bg-primary' : 'bg-border',
        )}
      >
        <span
          className={cn(
            'pointer-events-none inline-block h-5 w-5 rounded-full bg-background shadow-sm transition-transform',
            checked ? 'translate-x-5' : 'translate-x-0',
          )}
        />
      </button>
      <span className="text-sm text-foreground">{label}</span>
    </label>
  );
}

// ── useToast (sonner adapter) ────────────────────────────────────────────
// Legacy signature: const { toast } = useToast(); toast('success', 'message');
type ToastType = 'success' | 'error' | 'info';

export function useToast() {
  return {
    toast: (type: ToastType, message: string) => {
      if (type === 'success') sonnerToast.success(message);
      else if (type === 'error') sonnerToast.error(message);
      else sonnerToast(message);
    },
  };
}

// ToastProvider is now <Toaster /> mounted in root layout; export a no-op
// so any leftover references don't break.
export function ToastProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

// Re-export buttonVariants for pages that need Link-as-button styling.
export { buttonVariants };
