import { useState } from 'react';
import { ThumbsUp, ThumbsDown, Check } from 'lucide-react';
import { api } from '../../lib/api';
import { Button } from '../ui/button';

/**
 * Thumbs-up / thumbs-down buttons for user feedback on a query result.
 * Phase 4b of v0.5.0 — feeds back into the experience pool / query_feedback
 * table so the agent can use rated examples as dynamic few-shot on future
 * similar queries.
 *
 * Props:
 *   historyId: number | null — row id from query_history (the query API now
 *     returns this in the response; null when async mode hasn't finished
 *     persisting yet).
 */
export function FeedbackButtons({ historyId }) {
  const [state, setState] = useState('idle'); // idle | negative-expanded | submitting | positive-done | negative-done | error
  const [detail, setDetail] = useState('');
  const [error, setError] = useState(null);

  const disabled = historyId == null || state === 'submitting' ||
    state === 'positive-done' || state === 'negative-done';

  async function submit(feedbackType, extraDetail = '') {
    if (historyId == null) return;
    setState('submitting');
    setError(null);
    try {
      await api.feedback({
        history_id: historyId,
        feedback_type: feedbackType,
        detail: extraDetail || null,
      });
      setState(feedbackType === 'positive' ? 'positive-done' : 'negative-done');
    } catch (err) {
      setError(err.message || String(err));
      setState('idle');
    }
  }

  if (historyId == null) {
    return null;
  }

  if (state === 'positive-done') {
    return (
      <div className="flex items-center gap-2 text-xs text-[var(--accent-success)]">
        <Check className="h-3.5 w-3.5" />
        感谢反馈！已记录为正面示例
      </div>
    );
  }

  if (state === 'negative-done') {
    return (
      <div className="flex items-center gap-2 text-xs text-[var(--accent-warn)]">
        <Check className="h-3.5 w-3.5" />
        已记录反馈，Agent 会尽量避免类似错误
      </div>
    );
  }

  if (state === 'negative-expanded') {
    return (
      <div className="space-y-2 rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)] px-3 py-2">
        <div className="text-xs text-[var(--text-secondary)]">哪里不对？（可选）</div>
        <textarea
          className="w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-xs"
          rows={2}
          placeholder="SQL 错误、字段选错、聚合维度不对..."
          value={detail}
          onChange={(e) => setDetail(e.target.value)}
        />
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="destructive"
            disabled={disabled}
            onClick={() => submit('negative', detail.trim())}
          >
            提交反馈
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setState('idle');
              setDetail('');
            }}
          >
            取消
          </Button>
        </div>
        {error && <div className="text-xs text-[var(--accent-error)]">{error}</div>}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-[var(--text-muted)]">这个结果有帮助吗？</span>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => submit('positive')}
        title="这次 SQL 写得对"
      >
        <ThumbsUp className="h-3.5 w-3.5" /> 有用
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => setState('negative-expanded')}
        title="这次 SQL 有问题"
      >
        <ThumbsDown className="h-3.5 w-3.5" /> 不对
      </Button>
      {error && <span className="text-xs text-[var(--accent-error)]">{error}</span>}
    </div>
  );
}
