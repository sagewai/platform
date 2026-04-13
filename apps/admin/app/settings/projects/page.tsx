'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { PageLayout, Button, Badge, Card, FormField, TextInput, TextArea, Select, ConfirmDialog, useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type { Project, AvailableModel } from '@/utils/types';
import { ChevronDown, ChevronRight, Trash2, AlertCircle } from 'lucide-react';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [deleteSlug, setDeleteSlug] = useState<string | null>(null);
  const [expandedSlug, setExpandedSlug] = useState<string | null>(null);
  const [form, setForm] = useState({ name: '', slug: '', allowed_origins: '' });
  const { toast } = useToast();

  // Detail edit state
  const [editModel, setEditModel] = useState('');
  const [editOrigins, setEditOrigins] = useState('');
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await adminApi.listProjects();
      setProjects(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    adminApi.listAvailableModels().then(setAvailableModels).catch(() => {});
  }, []);

  const handleCreate = async () => {
    try {
      await adminApi.createProject({ ...form, environment: 'production' });
      toast('success', `Project "${form.name}" created`);
      setShowCreate(false);
      setForm({ name: '', slug: '', allowed_origins: '' });
      load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to create project';
      toast('error', msg);
    }
  };

  const handleDelete = async () => {
    if (!deleteSlug) return;
    try {
      await adminApi.deleteProject(deleteSlug);
      toast('success', 'Project deleted');
      if (expandedSlug === deleteSlug) setExpandedSlug(null);
      setDeleteSlug(null);
      load();
    } catch {
      toast('error', 'Failed to delete');
    }
  };

  function handleExpand(project: Project) {
    if (expandedSlug === project.slug) {
      setExpandedSlug(null);
    } else {
      setExpandedSlug(project.slug);
      setEditModel(project.default_model ?? '');
      setEditOrigins(project.allowed_origins ?? '');
    }
  }

  async function handleSaveDetail() {
    if (!expandedSlug) return;
    setSaving(true);
    try {
      await adminApi.updateProject(expandedSlug, {
        default_model: editModel || undefined,
        allowed_origins: editOrigins,
      });
      toast('success', 'Project updated');
      await load();
    } catch {
      toast('error', 'Failed to update project');
    } finally {
      setSaving(false);
    }
  }

  const envBadge = (env: string) => {
    const variant = env === 'production' ? 'error' : env === 'staging' ? 'warning' : 'info';
    return <Badge variant={variant}>{env}</Badge>;
  };

  // Group models by provider for the dropdown
  const modelsByProvider = availableModels.reduce<Record<string, AvailableModel[]>>((acc, m) => {
    (acc[m.provider] ??= []).push(m);
    return acc;
  }, {});

  const hasModels = availableModels.length > 0;

  return (
    <PageLayout
      title="Projects"
      description="Manage projects in your Sagewai instance."
      actions={<Button onClick={() => setShowCreate(true)}>Create Project</Button>}
    >
      {showCreate && (
        <Card className="mb-lg">
          <h3 className="text-lg font-semibold mb-md font-[family-name:var(--font-heading)]">New Project</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
            <FormField label="Name" required>
              <TextInput value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="My App" />
            </FormField>
            <FormField label="Slug" hint="Auto-generated from name if empty">
              <TextInput value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="my-app" />
            </FormField>
            <FormField label="Allowed Origins" hint="Comma-separated, e.g. http://localhost:3000">
              <TextInput value={form.allowed_origins} onChange={(e) => setForm({ ...form, allowed_origins: e.target.value })} />
            </FormField>
          </div>
          <div className="flex gap-sm mt-md">
            <Button onClick={handleCreate} disabled={!form.name}>Create</Button>
            <Button variant="ghost" onClick={() => setShowCreate(false)}>Cancel</Button>
          </div>
        </Card>
      )}

      {/* Accordion list */}
      <div className="flex flex-col gap-sm">
        {loading && projects.length === 0 && (
          <Card><p className="text-sm text-text-muted m-0">Loading...</p></Card>
        )}
        {!loading && projects.length === 0 && (
          <Card><p className="text-sm text-text-muted m-0">No projects yet. Create one to get started.</p></Card>
        )}
        {projects.map((project) => {
          const isExpanded = expandedSlug === project.slug;
          return (
            <Card key={project.slug} className="!p-0 overflow-hidden">
              {/* Accordion header — div (not button) so nested delete button is valid HTML */}
              <div
                role="button"
                tabIndex={0}
                onClick={() => handleExpand(project)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleExpand(project); }}
                className="w-full flex items-center justify-between gap-md px-lg py-md cursor-pointer text-left hover:bg-bg-subtle/50 transition-colors"
              >
                <div className="flex items-center gap-md min-w-0">
                  {isExpanded
                    ? <ChevronDown size={16} className="text-text-muted shrink-0" />
                    : <ChevronRight size={16} className="text-text-muted shrink-0" />
                  }
                  <div className="min-w-0">
                    <span className="font-semibold text-sm text-text-primary">{project.name}</span>
                    <span className="text-xs text-text-muted font-[family-name:var(--font-mono)] ml-2">{project.slug}</span>
                  </div>
                </div>
                <div className="flex items-center gap-sm shrink-0">
                  {envBadge(project.environment)}
                  <Badge variant={project.status === 'active' ? 'success' : 'default'}>{project.status}</Badge>
                  {project.slug !== 'default' && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setDeleteSlug(project.slug); }}
                      className="p-1.5 rounded hover:bg-error/10 text-text-muted hover:text-error transition-colors bg-transparent border-0 cursor-pointer"
                      title="Delete"
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>

              {/* Accordion body */}
              {isExpanded && (
                <div className="border-t border-border px-lg py-md">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-md mb-md">
                    <FormField label="Default Model" hint="Override the default LLM model for this project">
                      {hasModels ? (
                        <Select value={editModel} onChange={(e) => setEditModel(e.target.value)}>
                          <option value="">Use system default</option>
                          {Object.entries(modelsByProvider).map(([provider, models]) => (
                            <optgroup key={provider} label={provider.charAt(0).toUpperCase() + provider.slice(1)}>
                              {models.map((m) => (
                                <option key={m.id} value={m.id}>{m.id}</option>
                              ))}
                            </optgroup>
                          ))}
                        </Select>
                      ) : (
                        <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2.5">
                          <AlertCircle size={16} className="text-warning shrink-0 mt-0.5" />
                          <div className="text-sm">
                            <p className="text-text-secondary m-0">No LLM providers configured.</p>
                            <p className="text-text-muted m-0 mt-1">
                              Set up an API key or connect a local model (Ollama) in{' '}
                              <Link href="/settings/models" className="text-primary hover:underline">
                                Settings &rarr; AI Models
                              </Link>
                            </p>
                          </div>
                        </div>
                      )}
                    </FormField>
                    <FormField label="Allowed Origins (CORS)" hint="One origin per line, or comma-separated">
                      <TextArea
                        value={editOrigins}
                        onChange={(e) => setEditOrigins(e.target.value)}
                        placeholder={"http://localhost:3000\nhttps://app.example.com"}
                        rows={4}
                      />
                    </FormField>
                  </div>
                  <Button onClick={handleSaveDetail} disabled={saving}>
                    {saving ? 'Saving...' : 'Save Changes'}
                  </Button>
                </div>
              )}
            </Card>
          );
        })}
      </div>

      <ConfirmDialog
        open={!!deleteSlug}
        onClose={() => setDeleteSlug(null)}
        onConfirm={handleDelete}
        title="Delete Project"
        message={`Are you sure you want to delete "${deleteSlug}"? This cannot be undone.`}
        confirmText={deleteSlug || ''}
      />
    </PageLayout>
  );
}
