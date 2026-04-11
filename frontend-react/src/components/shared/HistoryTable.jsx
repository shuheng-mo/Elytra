import { useState } from 'react';
import { Play, RefreshCw, Eye } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../ui/table';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../ui/tooltip';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { api } from '../../lib/api';
import { formatDate, formatDuration, truncate } from '../../lib/utils';

export function HistoryTable({ history, onReuseQuery, showReplay = true }) {
  const [replayState, setReplayState] = useState({ open: false, data: null, loading: false });

  const handleReplay = async (id) => {
    setReplayState({ open: true, data: null, loading: true });
    try {
      const res = await api.replay(id);
      setReplayState({ open: true, data: res, loading: false });
    } catch (err) {
      setReplayState({ open: true, data: { error: err.message }, loading: false });
    }
  };

  if (!history || history.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 py-10 text-center text-sm text-[var(--text-muted)]">
        暂无查询历史
      </div>
    );
  }

  return (
    <>
      <div className="rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)]">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>时间</TableHead>
              <TableHead>用户</TableHead>
              <TableHead>查询</TableHead>
              <TableHead>意图</TableHead>
              <TableHead>模型</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">延迟</TableHead>
              <TableHead className="text-right">重试</TableHead>
              {showReplay && <TableHead>操作</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {history.map((h) => (
              <TableRow key={h.id}>
                <TableCell className="whitespace-nowrap text-xs text-[var(--text-secondary)]">
                  {formatDate(h.created_at)}
                </TableCell>
                <TableCell className="text-xs">
                  <div>{h.user_id || '—'}</div>
                  {h.user_role && (
                    <div className="text-[10px] text-[var(--text-muted)]">{h.user_role}</div>
                  )}
                </TableCell>
                <TableCell className="max-w-[300px]">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        onClick={() =>
                          onReuseQuery?.(h.user_query, h.source_name)
                        }
                        className="text-left text-xs text-[var(--text-primary)] hover:text-[var(--accent-primary)]"
                      >
                        {truncate(h.user_query, 60)}
                      </button>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-md">{h.user_query}</TooltipContent>
                  </Tooltip>
                </TableCell>
                <TableCell>
                  {h.intent && (
                    <Badge variant="secondary" className="text-[10px]">
                      {h.intent}
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="font-mono text-[10px] text-[var(--text-secondary)]">
                  {h.model_used || '—'}
                </TableCell>
                <TableCell>
                  {h.execution_success === true ? (
                    <Badge variant="success">成功</Badge>
                  ) : h.execution_success === false ? (
                    <Badge variant="error">失败</Badge>
                  ) : (
                    <Badge variant="outline">—</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right text-xs font-mono text-[var(--text-secondary)]">
                  {formatDuration(h.latency_ms)}
                </TableCell>
                <TableCell className="text-right text-xs text-[var(--text-secondary)]">
                  {h.retry_count ?? 0}
                </TableCell>
                {showReplay && (
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => handleReplay(h.id)}
                      >
                        <RefreshCw className="h-3 w-3" /> 回放
                      </Button>
                    </div>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog
        open={replayState.open}
        onOpenChange={(o) => setReplayState((s) => ({ ...s, open: o }))}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Play className="h-4 w-4 text-[var(--accent-primary)]" />
              查询回放结果
            </DialogTitle>
            <DialogDescription>
              将原始 SQL 在当前数据源上重新执行，并与历史结果哈希对比。
            </DialogDescription>
          </DialogHeader>

          {replayState.loading && (
            <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <RefreshCw className="h-4 w-4 animate-spin" /> 正在回放…
            </div>
          )}

          {replayState.data?.error && (
            <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
              {replayState.data.error}
            </div>
          )}

          {replayState.data && !replayState.data.error && (
            <div className="space-y-3 text-sm">
              <div className="flex items-center gap-3">
                <span className="text-[var(--text-secondary)]">结果一致：</span>
                {replayState.data.result_match ? (
                  <Badge variant="success">一致</Badge>
                ) : (
                  <Badge variant="error">不一致</Badge>
                )}
              </div>
              <div className="space-y-1 rounded-md bg-[var(--bg-code)] p-3 font-mono text-xs">
                <div>
                  <span className="text-[var(--text-muted)]">原始哈希：</span>
                  {replayState.data.original?.result_hash || '—'}
                </div>
                <div>
                  <span className="text-[var(--text-muted)]">回放哈希：</span>
                  {replayState.data.replay?.result_hash || '—'}
                </div>
                <div>
                  <span className="text-[var(--text-muted)]">行数：</span>
                  {replayState.data.replay?.row_count ?? 0} ·{' '}
                  <span className="text-[var(--text-muted)]">延迟：</span>
                  {formatDuration(replayState.data.replay?.latency_ms)}
                </div>
                {replayState.data.diff_summary && (
                  <div>
                    <span className="text-[var(--text-muted)]">差异：</span>
                    {replayState.data.diff_summary}
                  </div>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
