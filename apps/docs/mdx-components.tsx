import type { MDXComponents } from 'mdx/types';
import { Mermaid } from '@/components/mermaid';

export function useMDXComponents(components: MDXComponents): MDXComponents {
  return {
    ...components,
    h1: (props: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h1 className="text-3xl font-bold text-text-primary mb-6 mt-2" {...props} />
    ),
    h2: (props: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h2 className="text-2xl font-bold text-text-primary mb-4 mt-10 pb-2 border-b border-border" {...props} />
    ),
    h3: (props: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h3 className="text-xl font-semibold text-text-primary mb-3 mt-8" {...props} />
    ),
    h4: (props: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h4 className="text-lg font-semibold text-text-primary mb-2 mt-6" {...props} />
    ),
    p: (props: React.HTMLAttributes<HTMLParagraphElement>) => (
      <p className="text-text-secondary leading-7 mb-4" {...props} />
    ),
    a: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
      <a className="text-primary hover:text-primary-hover underline underline-offset-2" {...props} />
    ),
    ul: (props: React.HTMLAttributes<HTMLUListElement>) => (
      <ul className="list-disc list-inside space-y-1 mb-4 text-text-secondary" {...props} />
    ),
    ol: (props: React.OlHTMLAttributes<HTMLOListElement>) => (
      <ol className="list-decimal list-inside space-y-1 mb-4 text-text-secondary" {...props} />
    ),
    li: (props: React.LiHTMLAttributes<HTMLLIElement>) => (
      <li className="leading-7" {...props} />
    ),
    blockquote: (props: React.BlockquoteHTMLAttributes<HTMLQuoteElement>) => (
      <blockquote className="border-l-4 border-primary pl-4 my-4 text-text-secondary italic" {...props} />
    ),
    hr: () => <hr className="my-8 border-border" />,
    strong: (props: React.HTMLAttributes<HTMLElement>) => (
      <strong className="font-semibold text-text-primary" {...props} />
    ),
    pre: (props: React.HTMLAttributes<HTMLPreElement>) => {
      // Check if the child <code> has language-mermaid class
      const child = props.children as React.ReactElement<{ className?: string; children?: string }>;
      if (child?.props?.className === 'language-mermaid' && typeof child.props.children === 'string') {
        return <Mermaid chart={child.props.children} />;
      }
      // Code blocks are an "always-dark island" against the page so they
      // remain readable in both light and dark themes. Previously this
      // used `bg-bg-deep` + `text-text-on-dark`, but `--color-bg-deep`
      // flips to a light gray (#F1F5F9) in light mode while
      // `--color-text-on-dark` stays near-white — producing white text
      // on a light background. Tailwind's built-in slate scale keeps
      // contrast stable regardless of the active theme.
      return <pre className="bg-slate-900 text-slate-100 rounded-lg p-4 overflow-x-auto text-sm font-mono mb-4 leading-6" {...props} />;
    },
    code: (props: React.HTMLAttributes<HTMLElement>) => {
      const isInline = typeof props.children === 'string' && !props.className;
      if (isInline) {
        return <code className="bg-bg-subtle text-primary px-1.5 py-0.5 rounded text-sm font-mono" {...props} />;
      }
      return <code {...props} />;
    },
    table: (props: React.TableHTMLAttributes<HTMLTableElement>) => (
      <div className="overflow-x-auto mb-6 rounded-lg border border-border-dim">
        <table className="w-full border-collapse text-sm" {...props} />
      </div>
    ),
    thead: (props: React.HTMLAttributes<HTMLTableSectionElement>) => (
      <thead className="bg-bg-subtle text-text-primary" {...props} />
    ),
    th: (props: React.ThHTMLAttributes<HTMLTableCellElement>) => (
      <th className="border-b border-r border-text-muted/20 px-3 py-2.5 text-left font-semibold text-text-primary" {...props} />
    ),
    td: (props: React.TdHTMLAttributes<HTMLTableCellElement>) => (
      <td className="border-b border-r border-text-muted/10 px-3 py-2 text-text-secondary" {...props} />
    ),
  };
}
