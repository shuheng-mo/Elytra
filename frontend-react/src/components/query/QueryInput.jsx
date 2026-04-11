import { useEffect, useState } from 'react';
import { Textarea } from '../ui/textarea';
import { Button } from '../ui/button';
import { Send, Loader2, Zap } from 'lucide-react';
import { EXAMPLE_QUERIES } from '../../lib/constants';
import { cn } from '../../lib/utils';

export function QueryInput({
  onSubmit,
  isLoading,
  currentSource,
  currentRole = 'analyst',
  mode,
  onToggleMode,
  prefillQuery,
  onConsumePrefill,
}) {
  const [value, setValue] = useState('');
  const [placeholderIdx, setPlaceholderIdx] = useState(0);

  // Rotate placeholder every 4s
  useEffect(() => {
    const t = setInterval(() => {
      setPlaceholderIdx((i) => (i + 1) % EXAMPLE_QUERIES.length);
    }, 4000);
    return () => clearInterval(t);
  }, []);

  // Prefill from history reuse
  useEffect(() => {
    if (prefillQuery) {
      setValue(prefillQuery);
      onConsumePrefill?.();
    }
  }, [prefillQuery, onConsumePrefill]);

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed || isLoading) return;
    onSubmit(trimmed);
  };

  const onKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="w-full">
      <div className="relative">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={isLoading}
          rows={3}
          placeholder={`试试问：${EXAMPLE_QUERIES[placeholderIdx]}`}
          className="pr-24 text-lg resize-none"
        />
        <Button
          type="button"
          onClick={handleSubmit}
          disabled={isLoading || !value.trim()}
          className="absolute bottom-3 right-3"
          size="sm"
        >
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              处理中
            </>
          ) : (
            <>
              <Send className="h-3.5 w-3.5" />
              发送 ⌘↵
            </>
          )}
        </Button>
      </div>

      <div className="mt-2 flex items-center justify-between text-xs text-[var(--text-secondary)]">
        <div className="flex items-center gap-3">
          <span>
            数据源：<span className="text-[var(--text-primary)]">{currentSource || '—'}</span>
          </span>
          <span>·</span>
          <span>
            角色：<span className="text-[var(--text-primary)]">{currentRole}</span>
          </span>
        </div>
        <button
          type="button"
          onClick={onToggleMode}
          className={cn(
            'flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium transition-colors',
            mode === 'async'
              ? 'bg-[var(--accent-primary)]/15 text-[var(--accent-primary)]'
              : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
          )}
        >
          <Zap className="h-3 w-3" />
          {mode === 'async' ? '异步模式 (WebSocket)' : '同步模式'}
        </button>
      </div>
    </div>
  );
}
