import ReactECharts from 'echarts-for-react';

// ---------------------------------------------------------------------------
// Backend chart_spec shape (src/chart/echarts_builder.py) is NOT a canonical
// ECharts option — it's an intermediate wrapper:
//   { chart_type, title (string), x_axis?, y_axis?, series?, data?, value? }
// Values coming from PostgreSQL DECIMAL columns are Python str (see memory
// feedback_numeric_strings_from_pg.md). We coerce them to numbers so ECharts
// can actually render them.
// ---------------------------------------------------------------------------

function toNumber(v) {
  if (v == null || v === '') return null;
  if (typeof v === 'number') return v;
  const n = Number(v);
  return Number.isFinite(n) ? n : v;
}

function coerceSeries(seriesList) {
  if (!Array.isArray(seriesList)) return seriesList;
  return seriesList.map((s) => {
    if (!s || !Array.isArray(s.data)) return s;
    const data = s.data.map((item) => {
      // Pie-shape: { name, value }
      if (item && typeof item === 'object' && 'value' in item) {
        return { ...item, value: toNumber(item.value) };
      }
      // Scatter-shape: [x, y]
      if (Array.isArray(item)) {
        return item.map((v, i) => (i === 0 && typeof v !== 'number' ? v : toNumber(v)));
      }
      // Flat numeric data
      return toNumber(item);
    });
    return { ...s, data };
  });
}

// Theme-aware defaults (dark mode; we read CSS vars at runtime).
function themeDefaults() {
  const cssVar = (name, fallback) => {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch {
      return fallback;
    }
  };
  const textPrimary = cssVar('--text-primary', '#e6edf3');
  const textSecondary = cssVar('--text-secondary', '#8a9bb8');
  const borderColor = cssVar('--border-color', '#1f2d4a');
  const borderSubtle = cssVar('--border-subtle', '#151f35');
  const accent = cssVar('--accent-primary', '#00d4e8');

  return {
    textPrimary,
    textSecondary,
    borderColor,
    borderSubtle,
    accent,
    palette: [
      accent,
      '#3fb950',
      '#f0b429',
      '#a78bfa',
      '#ff6b6b',
      '#7dd3fc',
      '#c4b5fd',
      '#fbbf24',
      '#34d399',
      '#f472b6',
    ],
  };
}

export function normalizeChartSpec(spec) {
  if (!spec || typeof spec !== 'object') return null;

  const { chart_type: chartType, title, x_axis, y_axis, series } = spec;
  const t = themeDefaults();

  const option = {
    backgroundColor: 'transparent',
    color: t.palette,
    title: title
      ? {
          text: typeof title === 'string' ? title : title?.text || '',
          textStyle: { color: t.textPrimary, fontSize: 14, fontWeight: 600 },
          left: 'center',
          top: 6,
        }
      : undefined,
    textStyle: {
      color: t.textPrimary,
      fontFamily: 'DM Sans, system-ui, sans-serif',
    },
    grid: { left: 60, right: 24, top: 50, bottom: 40, containLabel: true },
  };

  // Axes (bar / line / scatter / multi_line)
  if (x_axis && chartType !== 'pie' && chartType !== 'number_card') {
    option.xAxis = {
      type: 'category',
      data: Array.isArray(x_axis.data) ? x_axis.data.map((v) => String(v)) : [],
      name: x_axis.field || '',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: t.textSecondary },
      axisLine: { lineStyle: { color: t.borderColor } },
      axisLabel: { color: t.textSecondary, interval: 0, rotate: 0 },
      splitLine: { lineStyle: { color: t.borderSubtle } },
    };
  }
  if (y_axis && chartType !== 'pie' && chartType !== 'number_card') {
    option.yAxis = {
      type: 'value',
      name: y_axis.field || '',
      nameTextStyle: { color: t.textSecondary },
      axisLine: { lineStyle: { color: t.borderColor } },
      axisLabel: { color: t.textSecondary },
      splitLine: { lineStyle: { color: t.borderSubtle } },
    };
  }

  // Series (coerce string → number)
  if (Array.isArray(series)) {
    option.series = coerceSeries(series).map((s) => {
      if (s?.type === 'pie') {
        return {
          ...s,
          radius: s.radius || ['38%', '68%'],
          center: ['50%', '55%'],
          label: { color: t.textPrimary },
          labelLine: { lineStyle: { color: t.borderColor } },
          itemStyle: { borderColor: 'rgba(0,0,0,0.3)', borderWidth: 1 },
        };
      }
      if (s?.type === 'bar') {
        return {
          ...s,
          itemStyle: { color: t.accent, borderRadius: [4, 4, 0, 0] },
          barMaxWidth: 40,
        };
      }
      if (s?.type === 'line') {
        return {
          ...s,
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.08 },
        };
      }
      return s;
    });
  }

  // Tooltip / legend
  option.tooltip = {
    trigger: chartType === 'pie' ? 'item' : 'axis',
    backgroundColor: 'rgba(10, 18, 32, 0.95)',
    borderColor: t.borderColor,
    textStyle: { color: t.textPrimary },
  };

  if (chartType === 'pie' || chartType === 'multi_line') {
    option.legend = {
      bottom: 0,
      textStyle: { color: t.textSecondary },
      icon: 'circle',
    };
    // Shrink grid to make room for legend on multi_line
    if (chartType === 'multi_line') {
      option.grid.bottom = 48;
    }
  }

  return option;
}

export function ChartRenderer({ chartSpec }) {
  if (!chartSpec) {
    return (
      <div className="rounded-md border border-dashed border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 py-10 text-center text-sm text-[var(--text-muted)]">
        当前结果无图表展示
      </div>
    );
  }

  // Special case: number_card renders as a big metric, not ECharts.
  if (chartSpec.chart_type === 'number_card') {
    const value = toNumber(chartSpec.value);
    return (
      <div className="flex flex-col items-center justify-center rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)] px-6 py-12">
        <div className="text-xs uppercase tracking-wider text-[var(--text-secondary)]">
          {chartSpec.field || chartSpec.title || ''}
        </div>
        <div className="mt-2 text-5xl font-semibold tabular-nums text-[var(--accent-primary)]">
          {typeof value === 'number' ? value.toLocaleString() : String(chartSpec.value ?? '—')}
        </div>
        {chartSpec.title && chartSpec.field && chartSpec.title !== chartSpec.field && (
          <div className="mt-2 text-sm text-[var(--text-secondary)]">{chartSpec.title}</div>
        )}
      </div>
    );
  }

  const option = normalizeChartSpec(chartSpec);
  if (!option || !option.series || option.series.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 py-10 text-center text-sm text-[var(--text-muted)]">
        图表数据不完整
      </div>
    );
  }

  return (
    <div className="rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)] p-3">
      <ReactECharts
        option={option}
        style={{ height: '400px', width: '100%' }}
        notMerge
        lazyUpdate
      />
    </div>
  );
}
