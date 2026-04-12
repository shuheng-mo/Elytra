import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectTaskWS } from '../lib/api';
import { STEP_LABELS } from '../lib/constants';
import { useSettings } from '../lib/settings';

// Async /api/task/{id} returns the raw AgentState dict, whose field names
// differ from the canonical QueryResponse returned by the sync /api/query
// endpoint. This function mirrors the conversion in src/api/query.py:138-156
// so the UI can use a single shape throughout.
function agentStateToResponse(state) {
  if (!state) return null;
  return {
    success: Boolean(state.execution_success),
    query: state.user_query || '',
    source: state.active_source || null,
    dialect: state.sql_dialect || null,
    intent: state.intent || null,
    generated_sql: state.generated_sql || null,
    result: state.execution_result ?? null,
    visualization_hint: state.visualization_hint || null,
    final_answer: state.final_answer || '',
    model_used: state.model_used || null,
    retry_count: state.retry_count ?? 0,
    latency_ms: state.latency_ms ?? 0,
    token_count: state.token_count ?? 0,
    error: state.execution_error || null,
    user_role: state.user_role || null,
    tables_filtered: 0,
    chart_spec: state.chart_spec || null,
    history_id: state.history_id ?? null,
    session_id: state.session_id || null,
    sanitizer_violations: state.sanitizer_violations || [],
    node_timings: state.node_timings || null,
  };
}

// Synthesize the AgentSteps timeline from a sync QueryResponse.
// Backend does not expose per-step durations in sync mode, so we attach
// total latency to the final step.
export function synthesizeSteps(response) {
  const steps = [];
  if (!response) return steps;

  steps.push({
    key: 'intent',
    name: '意图识别',
    status: 'success',
    detail: response.intent || '—',
  });

  steps.push({
    key: 'schema',
    name: 'Schema 召回',
    status: 'success',
    detail: `${response.tables_filtered ?? 0} 张表`,
  });

  steps.push({
    key: 'sql_gen',
    name: 'SQL 生成',
    status: response.generated_sql ? 'success' : 'failed',
    detail: response.model_used || '—',
  });

  const retries = response.retry_count ?? 0;
  for (let i = 1; i <= retries; i++) {
    steps.push({
      key: `retry_${i}`,
      name: `自修正 #${i}`,
      status: 'success',
      detail: '执行失败后重新生成',
      branch: true,
    });
  }

  steps.push({
    key: 'execute',
    name: '执行查询',
    status: response.success ? 'success' : 'failed',
    detail: response.success
      ? `${response.result?.length ?? 0} 行结果`
      : response.error || '执行失败',
    duration_ms: response.latency_ms,
  });

  return steps;
}

// Map a WS progress event to a timeline entry.
// `detail` from backend (if any) has shape { info, extra } — `info` is a
// one-line human-readable summary and `extra` is a small key/value object
// the UI can reveal on expand.
function wsStepToEntry(wsStep, pct, detail) {
  return {
    key: wsStep,
    name: STEP_LABELS[wsStep] || wsStep,
    status: 'running',
    detail: pct != null ? `${pct}%` : '',
    info: detail?.info || null,
    extra: detail?.extra || null,
  };
}

// Initial placeholder step to show the timeline as soon as user hits submit.
const SUBMIT_STEP = {
  key: '__submitted',
  name: '提交中',
  status: 'running',
  detail: '正在连接 agent…',
};

// Unified query hook supporting sync + async modes.
// Default mode comes from SettingsContext; user_id too.
export function useQueryRunner() {
  const { settings } = useSettings();
  const [isLoading, setIsLoading] = useState(false);
  const [response, setResponse] = useState(null);
  const [steps, setSteps] = useState([]);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState(settings.defaultMode || 'async');
  const [taskId, setTaskId] = useState(null);
  const wsRef = useRef(null);

  // Keep mode in sync with user's Settings preference changes.
  useEffect(() => {
    setMode(settings.defaultMode || 'async');
  }, [settings.defaultMode]);

  const reset = useCallback(() => {
    setResponse(null);
    setSteps([]);
    setError(null);
    setTaskId(null);
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  // Sync submission
  const submitSync = useCallback(
    async (query, source) => {
      reset();
      setIsLoading(true);
      // Show a placeholder step immediately so the timeline isn't empty.
      setSteps([{ ...SUBMIT_STEP }]);
      try {
        const res = await api.query({ query, source, user_id: settings.userId });
        setResponse(res);
        setSteps(synthesizeSteps(res));
        if (!res.success) setError(res.error || '查询失败');
      } catch (err) {
        setError(err.message || String(err));
        setSteps((prev) =>
          prev.map((s) => (s.status === 'running' ? { ...s, status: 'failed' } : s))
        );
      } finally {
        setIsLoading(false);
      }
    },
    [reset, settings.userId]
  );

  // Async submission via WebSocket
  const submitAsync = useCallback(
    async (query, source) => {
      reset();
      setIsLoading(true);
      // Show a placeholder step immediately; replaced by real WS events.
      setSteps([{ ...SUBMIT_STEP }]);

      try {
        const submit = await api.queryAsync({ query, source, user_id: settings.userId });
        setTaskId(submit.task_id);

        const ws = connectTaskWS(
          submit.task_id,
          (evt) => {
            // Mirror every WS event to the browser devtools console so
            // React-side developers can cross-reference with the backend log.
            if (import.meta.env.DEV) {
              // eslint-disable-next-line no-console
              console.log('[elytra-agent]', evt);
            }
            if (evt.type === 'status' || evt.type === 'progress') {
              const entry = wsStepToEntry(evt.step, evt.pct, evt.detail);
              setSteps((prev) => {
                // Drop the placeholder once real events arrive.
                const filtered = prev.filter((s) => s.key !== '__submitted');
                // Mark all previous steps success, append/update current.
                const marked = filtered.map((s) =>
                  s.status === 'running' ? { ...s, status: 'success' } : s
                );
                const existingIdx = marked.findIndex((s) => s.key === entry.key);
                if (existingIdx >= 0) {
                  marked[existingIdx] = { ...marked[existingIdx], ...entry };
                  return marked;
                }
                return [...marked, entry];
              });
            } else if (evt.type === 'complete') {
              setSteps((prev) =>
                prev.map((s) =>
                  s.status === 'running' ? { ...s, status: 'success' } : s
                )
              );
              api
                .getTask(submit.task_id)
                .then((taskInfo) => {
                  // taskInfo.result is the raw AgentState — convert it to
                  // the canonical QueryResponse shape the UI expects.
                  const normalized = agentStateToResponse(taskInfo.result);
                  if (normalized) {
                    setResponse(normalized);
                    if (!normalized.success && normalized.error) {
                      setError(normalized.error);
                    }
                  }
                  if (taskInfo.status === 'failed' || evt.status === 'failed') {
                    const errMsg = taskInfo.error || evt.error || '异步任务失败';
                    setError(errMsg);
                    setSteps((prev) => {
                      const copy = [...prev];
                      if (copy.length > 0) copy[copy.length - 1].status = 'failed';
                      return copy;
                    });
                  }
                })
                .catch((err) => setError(err.message || String(err)))
                .finally(() => setIsLoading(false));
            }
          },
          (closeEvt) => {
            if (closeEvt.code === 4004) {
              setError('任务不存在或已过期');
              setIsLoading(false);
            }
          }
        );
        wsRef.current = ws;
      } catch (err) {
        setError(err.message || String(err));
        setIsLoading(false);
        setSteps((prev) =>
          prev.map((s) => (s.status === 'running' ? { ...s, status: 'failed' } : s))
        );
      }
    },
    [reset, settings.userId]
  );

  const submit = useCallback(
    (query, source) =>
      (mode === 'async' ? submitAsync : submitSync)(query, source),
    [mode, submitSync, submitAsync]
  );

  return {
    mode,
    setMode,
    isLoading,
    response,
    steps,
    error,
    taskId,
    submit,
    reset,
  };
}
