interface CodeBlockProps {
  code: string;
  language?: string;
  title?: string;
}

export function CodeBlock({ code, language = 'python', title }: CodeBlockProps) {
  return (
    <div className="min-w-0 max-w-full rounded-xl overflow-hidden border border-gray-800 bg-gray-950 my-6">
      {title && (
        <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-900 border-b border-gray-800">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full bg-red-500/80" />
            <span className="w-3 h-3 rounded-full bg-yellow-500/80" />
            <span className="w-3 h-3 rounded-full bg-green-500/80" />
          </div>
          <span className="ml-2 text-xs text-gray-400 font-mono">{title}</span>
        </div>
      )}
      {/* `min-w-0` lets the <pre> shrink below its intrinsic content width so
          `overflow-x-auto` actually starts scrolling on narrow viewports
          instead of forcing the parent grid/flex track to expand. */}
      <pre className="min-w-0 p-4 overflow-x-auto text-sm leading-relaxed">
        <code className={`language-${language} text-gray-100`}>{code}</code>
      </pre>
    </div>
  );
}
