import { useCallback, useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Download } from 'lucide-react';
import { api } from '../../lib/api';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Skeleton } from '../ui/skeleton';
import { Button } from '../ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';
import { AUDIT_RANGES } from '../../lib/constants';
import { HistoryTable } from '../shared/HistoryTable';
import { formatDuration } from '../../lib/utils';
import { exportAuditToXlsx, exportAuditToCsv } from '../../lib/export';

// Common ECharts defaults honoring the dark theme.
const darkTextStyle = { color: '#8b949e' };
const gridLine = { lineStyle: { color: '#21262d' } };
const axisLine = { lineStyle: { color: '#30363d' } };
const palette = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#d2a8ff', '#79c0ff'];

function KpiCard({ title, value, hint }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-3xl tabular-nums">{value}</CardTitle>
      </CardHeader>
      {hint && (
        <CardContent>
          <p className="text-xs text-[var(--text-muted)]">{hint}</p>
        </CardContent>
      )}
    </Card>
  );
}

function pieOption(title, dataObj) {
  const entries = Object.entries(dataObj || {}).map(([name, value]) => {
    const count = typeof value === 'object' ? value.count || 0 : value;
    return { name, value: count };
  });
  return {
    backgroundColor: 'transparent',
    color: palette,
    tooltip: { trigger: 'item' },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: darkTextStyle,
    },
    series: [
      {
        name: title,
        type: 'pie',
        radius: ['40%', '65%'],
        center: ['35%', '50%'],
        label: { color: '#e6edf3' },
        data: entries,
      },
    ],
  };
}

function barOption(title, dataObj) {
  const entries = Object.entries(dataObj || {});
  return {
    backgroundColor: 'transparent',
    color: palette,
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: {
      type: 'category',
      data: entries.map(([k]) => k),
      axisLine,
      axisLabel: { ...darkTextStyle, rotate: entries.length > 5 ? 20 : 0 },
    },
    yAxis: {
      type: 'value',
      axisLine,
      axisLabel: darkTextStyle,
      splitLine: gridLine,
    },
    series: [
      {
        type: 'bar',
        data: entries.map(([, v]) => v),
        itemStyle: { color: palette[0] },
        barMaxWidth: 40,
      },
    ],
  };
}

export function AuditDashboard() {
  const [days, setDays] = useState(7);
  const [stats, setStats] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, h] = await Promise.all([
        api.getAuditStats(days),
        api.getHistory({ limit: 20 }),
      ]);
      setStats(s);
      setHistory(h.history || []);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">审计统计</h1>
          {stats?.period && (
            <p className="text-xs text-[var(--text-secondary)]">{stats.period}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!stats || loading}
            onClick={() => exportAuditToXlsx(stats, history)}
            title="导出当前审计快照到 Excel"
          >
            <Download className="h-3.5 w-3.5" /> 导出 Excel
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!stats || loading}
            onClick={() => exportAuditToCsv(stats)}
            title="导出当前审计快照到 CSV"
          >
            <Download className="h-3.5 w-3.5" /> 导出 CSV
          </Button>
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {AUDIT_RANGES.map((r) => (
                <SelectItem key={r.value} value={String(r.value)}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
          {error}
        </div>
      )}

      {/* KPI cards */}
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28 rounded-lg" />)
        ) : stats ? (
          <>
            <KpiCard title="总查询数" value={stats.total_queries ?? 0} />
            <KpiCard
              title="成功率"
              value={`${((stats.success_rate ?? 0) * 100).toFixed(1)}%`}
            />
            <KpiCard
              title="平均延迟"
              value={formatDuration(stats.avg_latency_ms ?? 0)}
            />
            <KpiCard
              title="总成本"
              value={`$${(stats.total_cost_usd ?? 0).toFixed(4)}`}
            />
          </>
        ) : null}
      </div>

      {/* Charts */}
      {stats && !loading && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>模型使用分布</CardTitle>
                <CardDescription>按调用次数统计</CardDescription>
              </CardHeader>
              <CardContent>
                <ReactECharts
                  option={pieOption('模型', stats.by_model)}
                  style={{ height: '240px' }}
                />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>意图分类分布</CardTitle>
                <CardDescription>Agent 识别的 query 类型</CardDescription>
              </CardHeader>
              <CardContent>
                <ReactECharts
                  option={barOption('意图', stats.by_intent)}
                  style={{ height: '240px' }}
                />
              </CardContent>
            </Card>
          </div>

          <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>数据源使用分布</CardTitle>
              </CardHeader>
              <CardContent>
                <ReactECharts
                  option={barOption('数据源', stats.by_source)}
                  style={{ height: '220px' }}
                />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>每日查询趋势</CardTitle>
                <CardDescription>待后端补 time_series 接口</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex h-[220px] items-center justify-center rounded-md border border-dashed border-[var(--border-color)] text-xs text-[var(--text-muted)]">
                  占位 · 等待后端提供按天聚合数据
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>最近查询记录</CardTitle>
            </CardHeader>
            <CardContent>
              <HistoryTable history={history} showReplay={false} />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
