'use client';

interface StepProgressProps {
  steps: string[];
  currentStep: number; // 0-indexed
}

export function StepProgress({ steps, currentStep }: StepProgressProps) {
  return (
    <div className="flex items-center justify-center gap-0 mb-8">
      {steps.map((label, i) => {
        const isCompleted = i < currentStep;
        const isCurrent = i === currentStep;
        const isLast = i === steps.length - 1;

        return (
          <div key={label} className="flex items-center">
            {/* Dot + Label */}
            <div className="flex flex-col items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
                  isCompleted
                    ? 'bg-primary text-white'
                    : isCurrent
                    ? 'bg-primary/20 border-2 border-primary text-primary'
                    : 'bg-bg-subtle border border-border text-text-secondary'
                }`}
              >
                {isCompleted ? '\u2713' : i + 1}
              </div>
              <span
                className={`text-[10px] mt-1.5 whitespace-nowrap ${
                  isCurrent ? 'text-primary font-semibold' : 'text-text-secondary'
                }`}
              >
                {label}
              </span>
            </div>

            {/* Connector line */}
            {!isLast && (
              <div
                className={`w-8 h-0.5 mx-1 mt-[-14px] ${
                  isCompleted ? 'bg-primary' : 'bg-border'
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
