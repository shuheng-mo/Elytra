import { useEffect } from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';
import { useDataSources } from '../../hooks/useDataSources';
import { DataSourceIcon } from '../icons/DataSourceIcon';
import { cn } from '../../lib/utils';

export function DataSourcePicker({ value, onChange, className }) {
  const { datasources, defaultSource, loading } = useDataSources();

  // If no value, fall back to default once loaded.
  useEffect(() => {
    if (!value && !loading && datasources.length > 0) {
      const def = defaultSource || datasources.find((d) => d.is_default)?.name || datasources[0]?.name;
      if (def) onChange(def);
    }
  }, [value, loading, datasources, defaultSource, onChange]);

  return (
    <Select value={value ?? ''} onValueChange={onChange}>
      <SelectTrigger className={cn('w-[260px]', className)}>
        <SelectValue placeholder={loading ? '加载数据源…' : '选择数据源'} />
      </SelectTrigger>
      <SelectContent>
        {datasources.map((ds) => (
          <SelectItem key={ds.name} value={ds.name}>
            <div className="flex items-center gap-2">
              <DataSourceIcon dialect={ds.dialect} size={16} />
              <span className="font-medium">{ds.name}</span>
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  ds.connected ? 'bg-[var(--accent-success)]' : 'bg-[var(--accent-error)]'
                )}
                title={ds.connected ? '已连接' : '连接失败'}
              />
              {ds.is_default && (
                <span className="text-[10px] uppercase text-[var(--text-muted)]">默认</span>
              )}
            </div>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
