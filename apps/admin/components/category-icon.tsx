import {
  LifeBuoy,
  Code2,
  Database,
  Microscope,
  PenLine,
  Settings2,
  Plane,
  GraduationCap,
  Sparkles,
  Briefcase,
  type LucideIcon,
} from 'lucide-react';

export type CategoryTone =
  | 'cyan'
  | 'purple'
  | 'sky'
  | 'amber'
  | 'green'
  | 'orange'
  | 'rose';

export interface CategoryMeta {
  Icon: LucideIcon;
  tone: CategoryTone;
  label: string;
}

export const CATEGORY_META: Record<string, CategoryMeta> = {
  support:     { Icon: LifeBuoy,      tone: 'cyan',   label: 'Support' },
  engineering: { Icon: Code2,         tone: 'purple', label: 'Engineering' },
  data:        { Icon: Database,      tone: 'sky',    label: 'Data' },
  research:    { Icon: Microscope,    tone: 'amber',  label: 'Research' },
  content:     { Icon: PenLine,       tone: 'green',  label: 'Content' },
  operations:  { Icon: Settings2,     tone: 'orange', label: 'Operations' },
  travel:      { Icon: Plane,         tone: 'sky',    label: 'Travel' },
  education:   { Icon: GraduationCap, tone: 'purple', label: 'Education' },
  general:     { Icon: Sparkles,      tone: 'cyan',   label: 'General' },
};

export function getCategoryMeta(category: string): CategoryMeta {
  return (
    CATEGORY_META[category?.toLowerCase()] ?? {
      Icon: Briefcase,
      tone: 'cyan',
      label: category || 'General',
    }
  );
}

/**
 * Tailwind classes for the tinted icon tile and category text. Returned as
 * static class strings (not template strings) so the JIT compiler can pick
 * them up.
 */
export const TONE_CLASSES: Record<CategoryTone, { tile: string; text: string }> = {
  cyan:   { tile: 'bg-cyan-500/10 text-cyan-500',     text: 'text-cyan-500' },
  purple: { tile: 'bg-purple-500/10 text-purple-500', text: 'text-purple-500' },
  sky:    { tile: 'bg-sky-500/10 text-sky-500',       text: 'text-sky-500' },
  amber:  { tile: 'bg-amber-500/10 text-amber-500',   text: 'text-amber-500' },
  green:  { tile: 'bg-emerald-500/10 text-emerald-500', text: 'text-emerald-500' },
  orange: { tile: 'bg-orange-500/10 text-orange-500', text: 'text-orange-500' },
  rose:   { tile: 'bg-rose-500/10 text-rose-500',     text: 'text-rose-500' },
};
