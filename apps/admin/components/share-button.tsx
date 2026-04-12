'use client';

import { useState, useRef, useEffect } from 'react';
import { Copy, Download, Bookmark, Check, Share2, X } from 'lucide-react';
import { Button, useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';

interface ShareButtonProps {
  agentName: string;
  model?: string;
  inputText: string;
  outputText: string;
  totalTokens?: number;
  source?: 'playground' | 'workflow' | 'api';
}

export function ShareButton({
  agentName,
  model = '',
  inputText,
  outputText,
  totalTokens = 0,
  source = 'playground',
}: ShareButtonProps) {
  const [open, setOpen] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [copied, setCopied] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const { toast } = useToast();

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  async function handleCopy() {
    const text = `## Input\n${inputText}\n\n## Output\n${outputText}`;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    setOpen(false);
    toast('success', 'Copied to clipboard');
  }

  function handleDownload() {
    const md = `# ${agentName} — Saved Prompt\n\n**Model:** ${model}\n**Tokens:** ${totalTokens}\n**Source:** ${source}\n\n## Input\n\n${inputText}\n\n## Output\n\n${outputText}\n`;
    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${agentName}-prompt.md`;
    a.click();
    URL.revokeObjectURL(url);
    setOpen(false);
    toast('success', 'Downloaded as Markdown');
  }

  function handleSaveClick() {
    setOpen(false);
    setShowSaveModal(true);
  }

  return (
    <>
      <div className="relative" ref={menuRef}>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOpen(!open)}
          aria-label="Share prompt"
          aria-haspopup="true"
          aria-expanded={open}
        >
          <Share2 size={14} />
          <span className="ml-1.5">Share</span>
        </Button>

        {open && (
          <div className="absolute right-0 top-full mt-1 z-50 bg-bg-elevated border border-border rounded-lg shadow-xl min-w-[180px] py-1">
            <button
              type="button"
              onClick={handleCopy}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-text-primary hover:bg-primary/5 dark:hover:bg-white/5 transition-colors"
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              Copy to clipboard
            </button>
            <button
              type="button"
              onClick={handleDownload}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-text-primary hover:bg-primary/5 dark:hover:bg-white/5 transition-colors"
            >
              <Download size={14} />
              Download as .md
            </button>
            <button
              type="button"
              onClick={handleSaveClick}
              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-text-primary hover:bg-primary/5 dark:hover:bg-white/5 transition-colors"
            >
              <Bookmark size={14} />
              Save as example
            </button>
          </div>
        )}
      </div>

      {showSaveModal && (
        <SavePromptModal
          agentName={agentName}
          model={model}
          inputText={inputText}
          outputText={outputText}
          totalTokens={totalTokens}
          source={source}
          onClose={() => setShowSaveModal(false)}
        />
      )}
    </>
  );
}

function SavePromptModal({
  agentName,
  model,
  inputText,
  outputText,
  totalTokens,
  source,
  onClose,
}: {
  agentName: string;
  model: string;
  inputText: string;
  outputText: string;
  totalTokens: number;
  source: string;
  onClose: () => void;
}) {
  const [tags, setTags] = useState('');
  const [isExample, setIsExample] = useState(true);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();

  async function handleSave() {
    setSaving(true);
    try {
      const tagList = tags
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
      await adminApi.savePrompt({
        agent_name: agentName,
        model,
        input_text: inputText,
        output_text: outputText,
        total_tokens: totalTokens,
        tags: tagList,
        source,
        is_example: isExample,
      });
      toast('success', 'Prompt saved');
      onClose();
    } catch {
      toast('error', 'Failed to save prompt');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50">
      <div className="bg-bg-elevated border border-border rounded-xl shadow-2xl w-full max-w-[28rem] p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-text-primary">Save Prompt</h3>
          <button type="button" onClick={onClose} className="text-text-muted hover:text-text-primary">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-text-muted mb-1">Agent</label>
            <div className="text-sm text-text-primary">{agentName}</div>
          </div>

          <div>
            <label className="block text-sm text-text-muted mb-1">Input preview</label>
            <div className="text-sm text-text-secondary bg-bg-subtle rounded p-2 max-h-20 overflow-auto">
              {inputText.slice(0, 200)}{inputText.length > 200 ? '...' : ''}
            </div>
          </div>

          <div>
            <label htmlFor="save-tags" className="block text-sm text-text-muted mb-1">
              Tags (comma-separated)
            </label>
            <input
              id="save-tags"
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="e.g. good-example, summarization"
              className="w-full bg-bg-subtle border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isExample}
              onChange={(e) => setIsExample(e.target.checked)}
              className="rounded border-border bg-bg-subtle text-primary focus:ring-primary"
            />
            <span className="text-sm text-text-secondary">Mark as few-shot example</span>
          </label>
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  );
}
