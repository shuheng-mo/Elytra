import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import {
  Sun,
  Moon,
  RotateCcw,
  Check,
  Zap,
  ClockArrowUp,
  Shield,
  Download,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { ACCENT_PRESETS, USER_IDENTITIES, useSettings } from '../../lib/settings';
import { getOrCreateSessionId } from '../../lib/utils';
import { cn } from '../../lib/utils';
import { useState } from 'react';
import { exportSessionBundle } from '../../lib/export';

function SectionHeader({ title, description }) {
  return (
    <CardHeader>
      <CardTitle>{title}</CardTitle>
      {description && <CardDescription>{description}</CardDescription>}
    </CardHeader>
  );
}

const EXPORT_STAGE_LABELS = {
  fetching_datasources: '读取数据源列表',
  fetching_schema: '读取 Schema',
  fetching_history: '读取查询历史',
  fetching_audit: '读取审计统计',
  writing: '生成 Excel 文件',
  done: '完成',
};

export function SettingsPage() {
  const { settings, update, reset } = useSettings();
  const [sessionId, setSessionId] = useState(getOrCreateSessionId());
  const [exportStage, setExportStage] = useState(null); // null | stage string
  const [exportError, setExportError] = useState(null);

  const resetSession = () => {
    localStorage.removeItem('elytra_session_id');
    setSessionId(getOrCreateSessionId());
  };

  const handleBundleExport = async () => {
    setExportError(null);
    setExportStage('fetching_datasources');
    try {
      await exportSessionBundle({
        sessionId,
        onProgress: (stage) => setExportStage(stage),
      });
      // Leave "done" briefly, then reset so the button can be clicked again.
      setTimeout(() => setExportStage(null), 1500);
    } catch (err) {
      setExportError(err.message || String(err));
      setExportStage(null);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-8 py-8 space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">设置</h1>
          <p className="text-xs text-[var(--text-secondary)]">
            外观、身份与查询行为配置 · 存储在浏览器 localStorage 中
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={reset}>
          <RotateCcw className="h-3.5 w-3.5" /> 恢复默认
        </Button>
      </header>

      {/* Theme */}
      <Card>
        <SectionHeader title="主题" description="深色 / 浅色，当前品牌主色取自 Elytra logo 的电光青" />
        <CardContent>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => update({ theme: 'dark' })}
              className={cn(
                'flex flex-1 items-center gap-3 rounded-md border px-4 py-3 text-left transition-colors',
                settings.theme === 'dark'
                  ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)]'
                  : 'border-[var(--border-color)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <Moon className="h-5 w-5 text-[var(--accent-primary)]" />
              <div className="flex-1">
                <div className="text-sm font-medium">深色模式</div>
                <div className="text-xs text-[var(--text-secondary)]">深海军蓝背景，默认</div>
              </div>
              {settings.theme === 'dark' && (
                <Check className="h-4 w-4 text-[var(--accent-primary)]" />
              )}
            </button>
            <button
              type="button"
              onClick={() => update({ theme: 'light' })}
              className={cn(
                'flex flex-1 items-center gap-3 rounded-md border px-4 py-3 text-left transition-colors',
                settings.theme === 'light'
                  ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)]'
                  : 'border-[var(--border-color)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <Sun className="h-5 w-5 text-[var(--accent-warning)]" />
              <div className="flex-1">
                <div className="text-sm font-medium">浅色模式</div>
                <div className="text-xs text-[var(--text-secondary)]">白色背景，适合白天</div>
              </div>
              {settings.theme === 'light' && (
                <Check className="h-4 w-4 text-[var(--accent-primary)]" />
              )}
            </button>
          </div>
        </CardContent>
      </Card>

      {/* Accent */}
      <Card>
        <SectionHeader title="强调色" description="主按钮、链接、代码高亮使用的颜色" />
        <CardContent>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {Object.entries(ACCENT_PRESETS).map(([key, preset]) => {
              const active = settings.accent === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => update({ accent: key })}
                  className={cn(
                    'flex items-center gap-3 rounded-md border px-3 py-2.5 text-left transition-colors',
                    active
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)]'
                      : 'border-[var(--border-color)] hover:bg-[var(--bg-tertiary)]'
                  )}
                >
                  <span
                    className="h-6 w-6 shrink-0 rounded-md border border-white/10 shadow-inner"
                    style={{ backgroundColor: preset.primary }}
                  />
                  <span className="flex-1 text-xs font-medium text-[var(--text-primary)]">
                    {preset.label}
                  </span>
                  {active && <Check className="h-3.5 w-3.5 text-[var(--accent-primary)]" />}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Identity & Role */}
      <Card>
        <SectionHeader
          title="身份与权限"
          description="切换身份后，后端会按对应角色执行字段与行数过滤（config/permissions.yaml）"
        />
        <CardContent>
          <div className="space-y-2">
            {USER_IDENTITIES.map((u) => {
              const active = settings.userId === u.id;
              return (
                <button
                  key={u.id}
                  type="button"
                  onClick={() => update({ userId: u.id })}
                  className={cn(
                    'flex w-full items-center gap-3 rounded-md border px-4 py-3 text-left transition-colors',
                    active
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)]'
                      : 'border-[var(--border-color)] hover:bg-[var(--bg-tertiary)]'
                  )}
                >
                  <Shield className="h-4 w-4 text-[var(--accent-primary)]" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      {u.label}
                      <Badge variant="secondary" className="font-mono text-[10px]">
                        {u.id}
                      </Badge>
                    </div>
                    <div className="text-xs text-[var(--text-secondary)]">{u.description}</div>
                  </div>
                  {active && <Check className="h-4 w-4 text-[var(--accent-primary)]" />}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Query mode */}
      <Card>
        <SectionHeader
          title="默认查询模式"
          description="异步模式通过 WebSocket 实时推送 agent 执行进度，推荐作为默认"
        />
        <CardContent>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => update({ defaultMode: 'async' })}
              className={cn(
                'flex flex-1 items-center gap-3 rounded-md border px-4 py-3 text-left transition-colors',
                settings.defaultMode === 'async'
                  ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)]'
                  : 'border-[var(--border-color)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <Zap className="h-5 w-5 text-[var(--accent-primary)]" />
              <div className="flex-1">
                <div className="text-sm font-medium">异步（实时流）</div>
                <div className="text-xs text-[var(--text-secondary)]">
                  WebSocket 推送每个 agent 节点的进度，时间线实时更新
                </div>
              </div>
              {settings.defaultMode === 'async' && (
                <Check className="h-4 w-4 text-[var(--accent-primary)]" />
              )}
            </button>
            <button
              type="button"
              onClick={() => update({ defaultMode: 'sync' })}
              className={cn(
                'flex flex-1 items-center gap-3 rounded-md border px-4 py-3 text-left transition-colors',
                settings.defaultMode === 'sync'
                  ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)]'
                  : 'border-[var(--border-color)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <ClockArrowUp className="h-5 w-5 text-[var(--text-secondary)]" />
              <div className="flex-1">
                <div className="text-sm font-medium">同步</div>
                <div className="text-xs text-[var(--text-secondary)]">
                  单次 REST 请求，完成后一次性返回
                </div>
              </div>
              {settings.defaultMode === 'sync' && (
                <Check className="h-4 w-4 text-[var(--accent-primary)]" />
              )}
            </button>
          </div>
        </CardContent>
      </Card>

      {/* Session */}
      <Card>
        <SectionHeader title="会话" description="session_id 用于查询历史分组" />
        <CardContent>
          <div className="flex items-center justify-between gap-3 rounded-md border border-[var(--border-color)] bg-[var(--bg-code)] px-4 py-3">
            <code className="font-mono text-xs text-[var(--text-primary)]">{sessionId}</code>
            <Button variant="outline" size="sm" onClick={resetSession}>
              <RotateCcw className="h-3 w-3" /> 重置会话
            </Button>
          </div>
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            重置后，新查询会归入一个新的 session，旧历史仍在后端保留。
          </p>
        </CardContent>
      </Card>

      {/* Session data bundle export */}
      <Card>
        <SectionHeader
          title="会话数据导出"
          description="把当前 session 的 Schema / 查询历史 / 审计快照打包成一个 Excel 文件"
        />
        <CardContent>
          <div className="space-y-3">
            <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]/40 p-3 text-xs text-[var(--text-secondary)]">
              <div className="mb-1 font-medium text-[var(--text-primary)]">包含内容：</div>
              <ul className="ml-4 list-disc space-y-0.5">
                <li>
                  <strong>Schema</strong>：默认数据源的全部表结构（按 ODS / DWD / DWS 分 sheet）
                </li>
                <li>
                  <strong>History</strong>：session_id ={' '}
                  <code className="font-mono">{sessionId.slice(-12)}</code> 的查询记录
                </li>
                <li>
                  <strong>Audit</strong>：过去 7 天的总量 / 成本 / 分布统计
                </li>
              </ul>
            </div>

            <div className="flex items-center gap-3">
              <Button
                onClick={handleBundleExport}
                disabled={!!exportStage && exportStage !== 'done'}
              >
                {exportStage && exportStage !== 'done' ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {EXPORT_STAGE_LABELS[exportStage] || '处理中…'}
                  </>
                ) : exportStage === 'done' ? (
                  <>
                    <Check className="h-4 w-4 text-[var(--accent-success)]" />
                    已导出
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4" />
                    导出本会话所有数据
                  </>
                )}
              </Button>
              {exportError && (
                <div className="flex items-start gap-1.5 text-xs text-[var(--accent-error)]">
                  <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <span>{exportError}</span>
                </div>
              )}
            </div>

            <p className="text-[10px] text-[var(--text-muted)]">
              也可以在 Schema / 历史 / 审计 各页面右上角单独导出每一类数据。
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
