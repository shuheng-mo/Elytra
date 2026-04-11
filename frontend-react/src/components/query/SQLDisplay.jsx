import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { Button } from '../ui/button';

const SQL_KEYWORDS =
  /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|AND|OR|NOT|IN|AS|LIMIT|OFFSET|HAVING|UNION|ALL|INSERT|UPDATE|DELETE|INTO|VALUES|SET|COUNT|SUM|AVG|MAX|MIN|DISTINCT|CASE|WHEN|THEN|ELSE|END|IS|NULL|BETWEEN|LIKE|ILIKE|EXISTS|WITH|OVER|PARTITION BY|ROW_NUMBER|RANK|DENSE_RANK|CAST|COALESCE|NVL|DATE_TRUNC|DATE_FORMAT|EXTRACT|INTERVAL|CURRENT_DATE|NOW|TRUE|FALSE|ASC|DESC)\b/gi;

const SQL_STRING = /'([^'\\]|\\.)*'/g;
const SQL_NUMBER = /\b\d+(\.\d+)?\b/g;
const SQL_COMMENT = /--[^\n]*/g;

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Lightweight tokenizer: masks, then highlights in order: comment → string → keyword → number.
function highlight(sql) {
  let html = escapeHtml(sql);
  // comments first (before strings to avoid matching -- inside strings incorrectly)
  html = html.replace(SQL_COMMENT, (m) => `\x00C${m}\x00`);
  html = html.replace(SQL_STRING, (m) => `\x00S${m}\x00`);
  html = html.replace(SQL_KEYWORDS, (m) => `<span class="sql-keyword">${m}</span>`);
  html = html.replace(SQL_NUMBER, (m) => `<span class="sql-number">${m}</span>`);
  // restore strings and comments with highlighting
  html = html.replace(/\x00S([^\x00]*)\x00/g, (_m, g1) => `<span class="sql-string">${g1}</span>`);
  html = html.replace(
    /\x00C([^\x00]*)\x00/g,
    (_m, g1) => `<span style="color:var(--text-muted);font-style:italic">${g1}</span>`
  );
  return html;
}

export function SQLDisplay({ sql }) {
  const [copied, setCopied] = useState(false);

  if (!sql) {
    return (
      <div className="rounded-md border border-dashed border-[var(--border-color)] bg-[var(--bg-code)] px-4 py-6 text-center text-sm text-[var(--text-muted)]">
        尚未生成 SQL
      </div>
    );
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  };

  return (
    <div className="relative rounded-md border border-[var(--border-color)] bg-[var(--bg-code)]">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4 py-2">
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--text-secondary)]">
          Generated SQL
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleCopy}
          className="h-7 px-2 text-xs"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-[var(--accent-success)]" /> 已复制
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" /> 复制
            </>
          )}
        </Button>
      </div>
      <pre
        className="m-0 overflow-x-auto px-4 py-3 font-mono text-[13px] leading-relaxed text-[var(--text-primary)]"
        dangerouslySetInnerHTML={{ __html: highlight(sql) }}
      />
    </div>
  );
}
