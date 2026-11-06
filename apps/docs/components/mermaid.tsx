'use client';

import { useEffect, useRef, useState } from 'react';

type MermaidTheme = 'default' | 'dark';

export function Mermaid({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>('');
  const [theme, setTheme] = useState<MermaidTheme>('default');

  // Watch for theme changes
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const t = document.documentElement.getAttribute('data-theme');
      setTheme(t === 'dark' ? 'dark' : 'default');
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    // Set initial theme
    const t = document.documentElement.getAttribute('data-theme');
    setTheme(t === 'dark' ? 'dark' : 'default');

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function render() {
      const mermaid = (await import('mermaid')).default;
      mermaid.initialize({
        startOnLoad: false,
        theme: theme,
        securityLevel: 'loose',
        flowchart: { useMaxWidth: false, htmlLabels: true },
        sequence: { useMaxWidth: false },
        gantt: { useMaxWidth: false },
        journey: { useMaxWidth: false },
        class: { useMaxWidth: false },
        state: { useMaxWidth: false },
        er: { useMaxWidth: false },
        themeVariables: theme === 'dark' ? {
          primaryColor: '#132857',
          primaryTextColor: '#EEF1FF',
          primaryBorderColor: '#9D8DFF',
          lineColor: '#8692B8',
          secondaryColor: '#10234C',
          tertiaryColor: '#0E2048',
          noteBkgColor: '#132857',
          noteTextColor: '#EEF1FF',
          edgeLabelBackground: '#0E2048',
        } : {},
      });

      const id = `mermaid-${Math.random().toString(36).slice(2, 9)}`;
      try {
        const { svg: rendered } = await mermaid.render(id, chart.trim());
        if (!cancelled) setSvg(rendered);
      } catch {
        if (!cancelled) setSvg(`<pre class="text-error text-sm">${chart}</pre>`);
      }
    }

    render();
    return () => { cancelled = true; };
  }, [chart, theme]);

  if (!svg) {
    return (
      <div className="bg-bg-subtle rounded-lg p-4 my-4 text-text-muted text-sm">
        Loading diagram...
      </div>
    );
  }

  return (
    <div
      ref={ref}
      className="my-6 overflow-x-auto bg-bg-subtle/50 rounded-lg p-4 text-center [&>svg]:inline-block"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
