import { useCallback } from 'react';
import { DataSourcePicker } from '../query/DataSourcePicker';
import { QueryInput } from '../query/QueryInput';
import { AgentSteps } from '../query/AgentSteps';
import { SQLDisplay } from '../query/SQLDisplay';
import { QueryResult } from '../query/QueryResult';
import { PermissionBadge } from '../shared/PermissionBadge';
import { useQueryRunner } from '../../hooks/useQuery';
import { formatDuration } from '../../lib/utils';

export function QueryPage({
  currentSource,
  setCurrentSource,
  pendingQuery,
  clearPendingQuery,
}) {
  const runner = useQueryRunner();

  const handleSubmit = useCallback(
    (query) => {
      runner.submit(query, currentSource);
    },
    [runner, currentSource]
  );

  const toggleMode = () => runner.setMode(runner.mode === 'sync' ? 'async' : 'sync');
  const resp = runner.response;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 px-8 py-8">
      {/* Header: data source picker + role */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <DataSourcePicker value={currentSource} onChange={setCurrentSource} />
        </div>
        <PermissionBadge role={resp?.user_role || 'analyst'} />
      </div>

      {/* Query input */}
      <QueryInput
        onSubmit={handleSubmit}
        isLoading={runner.isLoading}
        currentSource={currentSource}
        currentRole={resp?.user_role || 'analyst'}
        mode={runner.mode}
        onToggleMode={toggleMode}
        prefillQuery={pendingQuery}
        onConsumePrefill={clearPendingQuery}
      />

      {/* Error banner */}
      {runner.error && (
        <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
          {runner.error}
        </div>
      )}

      {/* Agent steps — always visible while streaming or after response */}
      {(runner.steps.length > 0 || runner.isLoading) && (
        <AgentSteps
          steps={runner.steps}
          isStreaming={runner.isLoading}
        />
      )}

      {/* Generated SQL */}
      {(resp?.generated_sql || runner.isLoading) && (
        <SQLDisplay sql={resp?.generated_sql} />
      )}

      {/* Result (table + chart) */}
      {resp?.result && (
        <QueryResult
          rows={resp.result}
          chartSpec={resp.chart_spec}
          finalAnswer={resp.final_answer}
        />
      )}

      {/* Footer status bar */}
      {resp && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--border-subtle)] pt-3 text-xs text-[var(--text-secondary)]">
          <span>
            模型：<span className="text-[var(--text-primary)]">{resp.model_used || '—'}</span>
          </span>
          <span>·</span>
          <span>
            耗时：<span className="text-[var(--text-primary)]">{formatDuration(resp.latency_ms)}</span>
          </span>
          <span>·</span>
          <span>
            tokens：<span className="text-[var(--text-primary)]">{resp.token_count ?? 0}</span>
          </span>
          <span>·</span>
          <span>
            重试：<span className="text-[var(--text-primary)]">{resp.retry_count ?? 0}</span> 次
          </span>
          {resp.dialect && (
            <>
              <span>·</span>
              <span>
                方言：<span className="text-[var(--text-primary)]">{resp.dialect}</span>
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
