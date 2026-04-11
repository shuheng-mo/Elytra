import { useState } from 'react';
import { Check, X, Loader2, ChevronDown, ChevronRight } from 'lucide-react';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '../ui/collapsible';
import { cn, formatDuration } from '../../lib/utils';

function StepIcon({ status }) {
  if (status === 'success')
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent-success)]/15 text-[var(--accent-success)]">
        <Check className="h-3 w-3" />
      </span>
    );
  if (status === 'failed')
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent-error)]/15 text-[var(--accent-error)]">
        <X className="h-3 w-3" />
      </span>
    );
  if (status === 'running')
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent-primary-soft)] text-[var(--accent-primary)]">
        <Loader2 className="h-3 w-3 animate-spin" />
      </span>
    );
  return (
    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--bg-tertiary)] text-[var(--text-muted)]">
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--text-muted)]" />
    </span>
  );
}

// Renders a key/value from the `extra` object. Long strings get a dedicated
// code-like block; short ones inline.
function ExtraField({ label, value }) {
  if (value == null || value === '') return null;

  let display;
  if (Array.isArray(value)) {
    display = (
      <div className="flex flex-wrap gap-1">
        {value.map((v, i) => (
          <span
            key={i}
            className="rounded bg-[var(--bg-tertiary)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-primary)]"
          >
            {String(v)}
          </span>
        ))}
      </div>
    );
  } else if (typeof value === 'string' && value.length > 40) {
    display = (
      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-[var(--bg-code)] p-2 font-mono text-[11px] leading-relaxed text-[var(--text-primary)]">
        {value}
      </pre>
    );
  } else {
    display = (
      <span className="font-mono text-[11px] text-[var(--text-primary)]">{String(value)}</span>
    );
  }

  return (
    <div className="grid grid-cols-[130px_1fr] gap-3 py-1">
      <div className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </div>
      <div className="min-w-0">{display}</div>
    </div>
  );
}

// Pretty labels for well-known extra keys.
const EXTRA_LABELS = {
  intent: 'Intent',
  complexity_score: 'Complexity',
  tables: 'Matched Tables',
  role: 'Role',
  tables_remaining: 'Tables After Filter',
  model: 'Model',
  sql_preview: 'SQL Preview',
  row_count: 'Row Count',
  error: 'Error',
  retry_count: 'Retry #',
  last_error: 'Last Error',
  visualization_hint: 'Chart Type',
  final_answer_preview: 'Final Answer',
  question: 'Clarification',
};

function StepItem({ step }) {
  const [open, setOpen] = useState(false);
  const canExpand = !!step.extra && Object.keys(step.extra).length > 0;

  return (
    <li className={cn('relative', step.branch && 'ml-4')}>
      <span className="absolute -left-[26px] top-0">
        <StepIcon status={step.status} />
      </span>

      <Collapsible open={open} onOpenChange={canExpand ? setOpen : undefined}>
        <CollapsibleTrigger asChild disabled={!canExpand}>
          <button
            type="button"
            disabled={!canExpand}
            className={cn(
              'group flex w-full items-start justify-between gap-3 rounded-md px-2 py-1 -mx-2 text-left transition-colors',
              canExpand && 'hover:bg-[var(--bg-tertiary)]/40 cursor-pointer',
              !canExpand && 'cursor-default'
            )}
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5">
                {canExpand &&
                  (open ? (
                    <ChevronDown className="h-3 w-3 shrink-0 text-[var(--text-muted)]" />
                  ) : (
                    <ChevronRight className="h-3 w-3 shrink-0 text-[var(--text-muted)]" />
                  ))}
                <span className="text-sm font-medium text-[var(--text-primary)]">
                  {step.name}
                </span>
                {step.duration_ms != null && (
                  <span className="ml-auto font-mono text-[10px] text-[var(--text-muted)]">
                    {formatDuration(step.duration_ms)}
                  </span>
                )}
              </div>
              {/* Prefer the human-readable `info` from backend; fall back to
                  the plain pct string. */}
              {(step.info || step.detail) && (
                <div
                  className={cn(
                    'mt-0.5 text-xs truncate',
                    step.info ? 'text-[var(--text-secondary)]' : 'text-[var(--text-muted)]'
                  )}
                >
                  {step.info || step.detail}
                </div>
              )}
            </div>
          </button>
        </CollapsibleTrigger>

        {canExpand && (
          <CollapsibleContent>
            <div className="mt-2 ml-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-primary)] px-3 py-2">
              {Object.entries(step.extra).map(([k, v]) => (
                <ExtraField key={k} label={EXTRA_LABELS[k] || k} value={v} />
              ))}
            </div>
          </CollapsibleContent>
        )}
      </Collapsible>
    </li>
  );
}

export function AgentSteps({ steps, isStreaming, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);

  if (!steps || steps.length === 0) return null;

  const runningCount = steps.filter((s) => s.status === 'running').length;
  const summary = isStreaming && runningCount > 0 ? '进行中…' : `${steps.length} 步`;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)]">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]/30"
          >
            <div className="flex items-center gap-2">
              {open ? (
                <ChevronDown className="h-4 w-4 text-[var(--text-muted)]" />
              ) : (
                <ChevronRight className="h-4 w-4 text-[var(--text-muted)]" />
              )}
              <span>Agent 执行过程</span>
              <span className="text-xs font-normal text-[var(--text-secondary)]">
                · {summary}
              </span>
            </div>
            <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              点击步骤查看详情
            </span>
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="border-t border-[var(--border-subtle)] px-5 py-4">
            <ol className="relative space-y-3 border-l-2 border-[var(--border-subtle)] pl-6">
              {steps.map((step) => (
                <StepItem key={step.key} step={step} />
              ))}
            </ol>
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}
