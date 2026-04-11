import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, AlertCircle, Check, Info } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Badge } from '../ui/badge';
import { api } from '../../lib/api';
import { DataSourceIcon, getDialectLabel } from '../icons/DataSourceIcon';
import { cn } from '../../lib/utils';

function FieldRow({ field, value, onChange }) {
  const id = `field_${field.key}`;
  const common = {
    id,
    value: value ?? '',
    onChange: (e) =>
      onChange(field.type === 'int' ? e.target.value.replace(/[^\d]/g, '') : e.target.value),
    placeholder: field.placeholder || '',
  };

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="flex items-center gap-1 text-xs font-medium text-[var(--text-secondary)]">
        {field.label}
        {field.required && <span className="text-[var(--accent-error)]">*</span>}
      </label>
      {field.type === 'password' ? (
        <Input type="password" autoComplete="new-password" {...common} />
      ) : field.type === 'int' ? (
        <Input type="text" inputMode="numeric" {...common} />
      ) : (
        <Input type="text" {...common} />
      )}
      {field.help && (
        <p className="flex items-start gap-1 text-[10px] text-[var(--text-muted)]">
          <Info className="h-3 w-3 shrink-0 mt-0.5" />
          {field.help}
        </p>
      )}
    </div>
  );
}

function initialConnectionFrom(schema) {
  if (!schema) return {};
  const obj = {};
  for (const f of schema.fields) {
    if (f.default != null) obj[f.key] = String(f.default);
    else obj[f.key] = '';
  }
  return obj;
}

function buildPayloadConnection(schema, connection) {
  // Strip empty optional fields; coerce int fields.
  const out = {};
  for (const f of schema.fields) {
    const v = connection[f.key];
    if (v === '' || v == null) {
      if (f.required) out[f.key] = v ?? '';
      continue;
    }
    if (f.type === 'int') {
      const n = Number(v);
      out[f.key] = Number.isFinite(n) ? n : v;
    } else {
      out[f.key] = v;
    }
  }
  return out;
}

export function AddConnectorDialog({ open, onOpenChange, onCreated }) {
  const [dialects, setDialects] = useState([]);
  const [loadingTypes, setLoadingTypes] = useState(false);
  const [typesError, setTypesError] = useState(null);

  const [selected, setSelected] = useState(null); // dialect key
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [connection, setConnection] = useState({});
  const [runBootstrap, setRunBootstrap] = useState(true);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const selectedSchema = useMemo(
    () => dialects.find((d) => d.dialect === selected) || null,
    [dialects, selected]
  );

  // Load dialect schemas when dialog opens.
  useEffect(() => {
    if (!open) return;
    setLoadingTypes(true);
    setTypesError(null);
    api
      .getDataSourceTypes()
      .then((data) => {
        setDialects(data.dialects || []);
        // Auto-select first dialect if none chosen yet
        if (!selected && (data.dialects || []).length > 0) {
          setSelected(data.dialects[0].dialect);
        }
      })
      .catch((err) => setTypesError(err.message || String(err)))
      .finally(() => setLoadingTypes(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Reset connection defaults when dialect changes.
  useEffect(() => {
    if (selectedSchema) {
      setConnection(initialConnectionFrom(selectedSchema));
    }
  }, [selectedSchema]);

  // Reset everything on close.
  const handleOpenChange = (o) => {
    if (!o) {
      setSelected(null);
      setName('');
      setDescription('');
      setConnection({});
      setRunBootstrap(true);
      setSubmitting(false);
      setError(null);
      setResult(null);
    }
    onOpenChange(o);
  };

  const canSubmit =
    !submitting &&
    selectedSchema &&
    name.trim().length > 0 &&
    selectedSchema.fields
      .filter((f) => f.required)
      .every((f) => {
        const v = connection[f.key];
        return v != null && String(v).length > 0;
      });

  const handleSubmit = useCallback(async () => {
    if (!selectedSchema || !canSubmit) return;
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const payload = {
        name: name.trim(),
        dialect: selected,
        description: description.trim(),
        connection: buildPayloadConnection(selectedSchema, connection),
        run_bootstrap: runBootstrap,
      };
      const res = await api.createDataSource(payload);
      setResult(res);
      // Notify parent to refresh list
      onCreated?.(res);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSubmitting(false);
    }
  }, [
    selectedSchema,
    canSubmit,
    name,
    selected,
    description,
    connection,
    runBootstrap,
    onCreated,
  ]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Plus className="h-4 w-4 text-[var(--accent-primary)]" />
            新增数据源连接
          </DialogTitle>
          <DialogDescription>
            连接参数仅保存在本机的{' '}
            <code className="font-mono">config/datasources.local.yaml</code>
            （已 gitignore）
          </DialogDescription>
        </DialogHeader>

        {loadingTypes && (
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载支持的类型…
          </div>
        )}

        {typesError && (
          <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-3 py-2 text-xs text-[var(--accent-error)]">
            {typesError}
          </div>
        )}

        {!loadingTypes && !typesError && dialects.length > 0 && (
          <div className="space-y-5 max-h-[60vh] overflow-y-auto pr-1">
            {/* Dialect picker */}
            <div className="space-y-2">
              <label className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                连接器类型
              </label>
              <div className="grid grid-cols-3 gap-2">
                {dialects.map((d) => {
                  const active = selected === d.dialect;
                  return (
                    <button
                      key={d.dialect}
                      type="button"
                      disabled={submitting || !!result}
                      onClick={() => setSelected(d.dialect)}
                      className={cn(
                        'flex items-center gap-2 rounded-md border px-3 py-2.5 text-left transition-colors',
                        active
                          ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)]'
                          : 'border-[var(--border-color)] hover:bg-[var(--bg-tertiary)]',
                        submitting && 'opacity-60'
                      )}
                    >
                      <DataSourceIcon dialect={d.dialect} size={20} />
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium text-[var(--text-primary)]">
                          {d.label}
                        </div>
                        <div className="truncate text-[10px] text-[var(--text-muted)]">
                          {d.description}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Basic info */}
            {selectedSchema && !result && (
              <>
                <div className="space-y-2">
                  <label className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                    基本信息
                  </label>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label
                        htmlFor="ds_name"
                        className="flex items-center gap-1 text-xs font-medium text-[var(--text-secondary)]"
                      >
                        名称 <span className="text-[var(--accent-error)]">*</span>
                      </label>
                      <Input
                        id="ds_name"
                        value={name}
                        onChange={(e) =>
                          setName(e.target.value.replace(/[^a-zA-Z0-9_]/g, ''))
                        }
                        placeholder="my_datasource"
                      />
                      <p className="text-[10px] text-[var(--text-muted)]">
                        字母开头，仅允许字母数字下划线
                      </p>
                    </div>
                    <div className="space-y-1">
                      <label
                        htmlFor="ds_desc"
                        className="text-xs font-medium text-[var(--text-secondary)]"
                      >
                        描述
                      </label>
                      <Input
                        id="ds_desc"
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="业务侧对这个连接器的描述"
                      />
                    </div>
                  </div>
                </div>

                {/* Connection fields (dynamic) */}
                <div className="space-y-2">
                  <label className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                    连接参数
                  </label>
                  <div className="grid grid-cols-2 gap-3">
                    {selectedSchema.fields.map((f) => (
                      <FieldRow
                        key={f.key}
                        field={f}
                        value={connection[f.key]}
                        onChange={(v) =>
                          setConnection((c) => ({ ...c, [f.key]: v }))
                        }
                      />
                    ))}
                  </div>
                </div>

                {/* Bootstrap toggle */}
                <div className="flex items-start gap-3 rounded-md border border-[var(--border-color)] bg-[var(--bg-tertiary)]/40 p-3">
                  <input
                    type="checkbox"
                    id="run_bootstrap"
                    checked={runBootstrap}
                    onChange={(e) => setRunBootstrap(e.target.checked)}
                    className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-[var(--accent-primary)]"
                  />
                  <label htmlFor="run_bootstrap" className="flex-1 cursor-pointer">
                    <div className="text-xs font-medium text-[var(--text-primary)]">
                      立即索引 Schema Embeddings
                    </div>
                    <div className="mt-0.5 text-[10px] text-[var(--text-muted)]">
                      为这个数据源的表 + 字段生成向量索引，让 agent 立刻能检索到。
                      取消勾选则需后续手动运行{' '}
                      <code className="font-mono">python -m src.retrieval.bootstrap --source {name || '<name>'}</code>
                    </div>
                  </label>
                </div>

                {error && (
                  <div className="flex items-start gap-2 rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-3 py-2 text-xs text-[var(--accent-error)]">
                    <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>{error}</span>
                  </div>
                )}
              </>
            )}

            {/* Success state */}
            {result && (
              <div className="space-y-3 rounded-md border border-[var(--accent-success)]/40 bg-[var(--accent-success)]/10 p-4">
                <div className="flex items-center gap-2">
                  <Check className="h-5 w-5 text-[var(--accent-success)]" />
                  <div className="text-sm font-medium text-[var(--text-primary)]">
                    数据源 {result.datasource?.name} 已创建
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs text-[var(--text-secondary)]">
                  <div>
                    方言:{' '}
                    <span className="font-mono text-[var(--text-primary)]">
                      {result.datasource?.dialect}
                    </span>
                  </div>
                  <div>
                    表数: {result.datasource?.table_count ?? '—'}
                  </div>
                  <div>
                    Schema 索引:{' '}
                    <Badge
                      variant={
                        result.indexing_status === 'success'
                          ? 'success'
                          : result.indexing_status === 'failed'
                            ? 'error'
                            : 'secondary'
                      }
                      className="ml-1"
                    >
                      {result.indexing_status}
                    </Badge>
                  </div>
                </div>
                {result.indexing_error && (
                  <div className="rounded bg-[var(--bg-code)] p-2 font-mono text-[10px] text-[var(--accent-warning)]">
                    {result.indexing_error}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {!result ? (
            <>
              <Button
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button onClick={handleSubmit} disabled={!canSubmit}>
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    创建并测试连接…
                  </>
                ) : (
                  <>
                    <Plus className="h-4 w-4" />
                    创建
                  </>
                )}
              </Button>
            </>
          ) : (
            <Button onClick={() => handleOpenChange(false)}>完成</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
