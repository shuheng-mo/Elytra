import { MessageCircle } from 'lucide-react';
import { SQLDisplay } from './SQLDisplay';
import { QueryResult } from './QueryResult';
import { formatDuration } from '../../lib/utils';

/**
 * ChatGPT-style linear conversation flow for multi-turn NL2SQL.
 *
 * Renders an array of completed turns (user bubble + agent result card).
 * The currently in-flight turn is rendered by QueryPage above this
 * component via the live runner state; once that runner's response
 * settles, QueryPage pushes it to the turns array and clears the runner.
 *
 * Each agent card ends with FeedbackButtons (via QueryResult) so users
 * can rate any past turn, not just the newest one.
 */
export function ConversationThread({ turns }) {
  if (!turns || turns.length === 0) return null;

  return (
    <div className="space-y-6">
      {turns.map((turn, idx) => (
        <div key={turn.turn_id ?? idx} className="space-y-3">
          {/* User bubble */}
          <div className="flex items-start gap-2">
            <div className="mt-0.5 text-[var(--text-muted)]">
              <MessageCircle className="h-4 w-4" />
            </div>
            <div className="flex-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]">
              {turn.query}
            </div>
          </div>

          {/* Agent card */}
          <div className="space-y-3 pl-6">
            {turn.generated_sql && <SQLDisplay sql={turn.generated_sql} />}
            {turn.result && (
              <QueryResult
                rows={turn.result}
                chartSpec={turn.chart_spec}
                finalAnswer={turn.final_answer}
                historyId={turn.history_id}
              />
            )}
            {turn.error && !turn.result && (
              <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
                {turn.error}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
              {turn.model_used && <span>模型：{turn.model_used}</span>}
              {turn.latency_ms != null && <span>耗时：{formatDuration(turn.latency_ms)}</span>}
              {turn.retry_count > 0 && <span>重试：{turn.retry_count} 次</span>}
              {turn.dialect && <span>方言：{turn.dialect}</span>}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
