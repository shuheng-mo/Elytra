import { useCallback, useEffect, useRef, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { DataSourcePicker } from '../query/DataSourcePicker';
import { QueryInput } from '../query/QueryInput';
import { AgentSteps } from '../query/AgentSteps';
import { ConversationThread } from '../query/ConversationThread';
import { SQLDisplay } from '../query/SQLDisplay';
import { QueryResult } from '../query/QueryResult';
import { PermissionBadge } from '../shared/PermissionBadge';
import { Button } from '../ui/button';
import { useQueryRunner } from '../../hooks/useQuery';
import { formatDuration, getOrCreateSessionId, rotateSessionId } from '../../lib/utils';
import { useSettings } from '../../lib/settings';
import { api } from '../../lib/api';

// v0.5.0 — QueryPage is now a multi-turn conversation surface:
//   * Prior completed turns render via ConversationThread (top to bottom)
//   * The currently in-flight turn is still shown via AgentSteps / SQLDisplay
//   * When the runner settles on a successful response, we push it to the
//     turns array and clear the runner
//   * "新对话" button rotates session_id and clears the thread
export function QueryPage({
  currentSource,
  setCurrentSource,
  pendingQuery,
  clearPendingQuery,
}) {
  const runner = useQueryRunner();
  const [turns, setTurns] = useState([]);
  const [sessionId, setSessionId] = useState(() => getOrCreateSessionId());
  const [pendingQueryText, setPendingQueryText] = useState(null);
  const lastCapturedRef = useRef(null);

  // When a run settles (response present, not loading), capture it as a turn.
  // We use a ref to guard against re-capturing the same response on re-renders.
  useEffect(() => {
    if (runner.isLoading) return;
    if (!runner.response) return;
    if (lastCapturedRef.current === runner.response) return;

    lastCapturedRef.current = runner.response;
    setTurns((prev) => [
      ...prev,
      {
        turn_id: `${Date.now()}-${prev.length}`,
        query: pendingQueryText || runner.response.query,
        generated_sql: runner.response.generated_sql,
        result: runner.response.result,
        chart_spec: runner.response.chart_spec,
        final_answer: runner.response.final_answer,
        error: runner.response.error,
        history_id: runner.response.history_id,
        model_used: runner.response.model_used,
        latency_ms: runner.response.latency_ms,
        retry_count: runner.response.retry_count,
        dialect: runner.response.dialect,
        token_count: runner.response.token_count,
      },
    ]);
    setPendingQueryText(null);
  }, [runner.isLoading, runner.response, pendingQueryText]);

  const handleSubmit = useCallback(
    (query) => {
      setPendingQueryText(query);
      runner.submit(query, currentSource);
    },
    [runner, currentSource]
  );

  const handleNewConversation = useCallback(async () => {
    try {
      await api.clearConversation(sessionId);
    } catch (err) {
      // Non-fatal — even if the server tombstone fails, rotate locally so
      // the next query uses a fresh id.
      console.warn('clearConversation failed:', err);
    }
    const freshId = rotateSessionId();
    setSessionId(freshId);
    setTurns([]);
    lastCapturedRef.current = null;
    runner.reset();
  }, [runner, sessionId]);

  const toggleMode = () => runner.setMode(runner.mode === 'sync' ? 'async' : 'sync');
  const resp = runner.response;
  const { currentIdentity } = useSettings();
  const currentRole = turns[turns.length - 1]?.user_role || resp?.user_role || currentIdentity.role;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 px-8 py-8">
      {/* Header: data source picker + new-conversation button + role */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <DataSourcePicker value={currentSource} onChange={setCurrentSource} />
          <Button
            variant="outline"
            size="sm"
            onClick={handleNewConversation}
            disabled={runner.isLoading}
            title="清空当前会话并开启新对话"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            新对话
          </Button>
          {turns.length > 0 && (
            <span className="text-xs text-[var(--text-muted)]">{turns.length} 轮对话</span>
          )}
        </div>
        <PermissionBadge role={currentRole} />
      </div>

      {/* Prior turns */}
      {turns.length > 0 && <ConversationThread turns={turns} />}

      {/* Query input */}
      <QueryInput
        onSubmit={handleSubmit}
        isLoading={runner.isLoading}
        currentSource={currentSource}
        currentRole={currentRole}
        mode={runner.mode}
        onToggleMode={toggleMode}
        prefillQuery={pendingQuery}
        onConsumePrefill={clearPendingQuery}
      />

      {/* Error banner for the in-flight turn */}
      {runner.error && (
        <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
          {runner.error}
        </div>
      )}

      {/* In-flight agent steps — visible while streaming */}
      {(runner.steps.length > 0 && runner.isLoading) && (
        <AgentSteps steps={runner.steps} isStreaming={runner.isLoading} />
      )}

      {/* In-flight SQL preview while the query is still running */}
      {runner.isLoading && resp?.generated_sql && <SQLDisplay sql={resp.generated_sql} />}

      {/* Footer status for the most recent turn */}
      {!runner.isLoading && turns.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--border-subtle)] pt-3 text-xs text-[var(--text-secondary)]">
          <span>会话：<code className="text-[var(--text-primary)]">{sessionId.slice(0, 16)}…</code></span>
          <span>·</span>
          <span>累计 tokens：<span className="text-[var(--text-primary)]">
            {turns.reduce((sum, t) => sum + (t.token_count ?? 0), 0)}
          </span></span>
          <span>·</span>
          <span>累计耗时：<span className="text-[var(--text-primary)]">
            {formatDuration(turns.reduce((sum, t) => sum + (t.latency_ms ?? 0), 0))}
          </span></span>
        </div>
      )}
    </div>
  );
}
