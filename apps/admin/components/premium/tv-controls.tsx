'use client';

import { useEffect, useState } from 'react';
import { ArrowLeft, Pin, PinOff, Monitor } from 'lucide-react';

interface Props {
  currentScreen: number;
  totalScreens: number;
  isPinned: boolean;
  cycleSpeed: number;
  onTogglePin: () => void;
  onExit: () => void;
}

export function TVControls({
  currentScreen,
  totalScreens,
  isPinned,
  cycleSpeed,
  onTogglePin,
  onExit,
}: Props) {
  const [visible, setVisible] = useState(true);
  const [timer, setTimer] = useState<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const handleMove = () => {
      setVisible(true);
      if (timer) clearTimeout(timer);
      const t = setTimeout(() => setVisible(false), 3000);
      setTimer(t);
    };

    window.addEventListener('mousemove', handleMove);
    // Auto-hide after 3s initially
    const initial = setTimeout(() => setVisible(false), 3000);

    return () => {
      window.removeEventListener('mousemove', handleMove);
      if (timer) clearTimeout(timer);
      clearTimeout(initial);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className={`fixed bottom-0 left-0 right-0 z-50 transition-all duration-500 ${
        visible ? 'translate-y-0 opacity-100' : 'translate-y-full opacity-0'
      }`}
    >
      <div className="bg-black/60 backdrop-blur-md border-t border-white/10 px-6 py-3 flex items-center justify-between">
        {/* Exit */}
        <button
          onClick={onExit}
          className="flex items-center gap-2 text-white/60 hover:text-white text-sm transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Dashboard
        </button>

        {/* Screen dots */}
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            {Array.from({ length: totalScreens }).map((_, i) => (
              <div
                key={i}
                className={`w-2.5 h-2.5 rounded-full transition-all duration-300 ${
                  i === currentScreen ? 'bg-[#26C6DA] scale-125' : 'bg-white/20'
                }`}
              />
            ))}
          </div>

          {/* Pin */}
          <button
            onClick={onTogglePin}
            className={`flex items-center gap-1.5 text-sm transition-colors ${
              isPinned ? 'text-[#FFB74D]' : 'text-white/50 hover:text-white'
            }`}
          >
            {isPinned ? <Pin className="w-3.5 h-3.5" /> : <PinOff className="w-3.5 h-3.5" />}
            {isPinned ? 'Pinned' : 'Auto-cycle'}
          </button>

          {/* Speed */}
          <span className="text-white/30 text-xs tabular-nums">
            <Monitor className="w-3 h-3 inline mr-1" />
            {cycleSpeed}s
          </span>
        </div>

        {/* Spacer */}
        <div className="w-24" />
      </div>
    </div>
  );
}
