import { type ReactNode } from 'react';

interface FeatureCardProps {
  icon: ReactNode;
  title: string;
  description: string;
}

export function FeatureCard({ icon, title, description }: FeatureCardProps) {
  return (
    <div className="group relative bg-bg-elevated rounded-xl border border-border p-6 hover:border-primary/40 hover:shadow-lg transition-all duration-200">
      <div className="flex items-start gap-4">
        <span className="text-primary flex-shrink-0" aria-label={title}>
          {icon}
        </span>
        <div>
          <h3 className="text-lg font-semibold text-text-primary mb-1">{title}</h3>
          <p className="text-sm text-text-secondary leading-relaxed">{description}</p>
        </div>
      </div>
      <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-primary-light/0 to-primary-light/50 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
    </div>
  );
}
