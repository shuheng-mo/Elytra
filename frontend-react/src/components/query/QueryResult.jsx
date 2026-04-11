import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { ScrollArea } from '../ui/scroll-area';
import { ChartRenderer } from './ChartRenderer';

function DataTable({ rows }) {
  if (!rows || rows.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 py-10 text-center text-sm text-[var(--text-muted)]">
        无结果行
      </div>
    );
  }

  const columns = Object.keys(rows[0]);

  return (
    <ScrollArea className="max-h-[500px] rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)]">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((c) => (
              <TableHead key={c}>{c}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {columns.map((c) => (
                <TableCell key={c} className="font-mono text-xs">
                  {row[c] == null ? (
                    <span className="text-[var(--text-muted)]">NULL</span>
                  ) : typeof row[c] === 'object' ? (
                    JSON.stringify(row[c])
                  ) : (
                    String(row[c])
                  )}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </ScrollArea>
  );
}

export function QueryResult({ rows, chartSpec, finalAnswer }) {
  const hasChart = !!chartSpec;
  const rowCount = rows?.length ?? 0;

  return (
    <div className="space-y-3">
      {finalAnswer && (
        <div className="rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 py-3 text-sm text-[var(--text-primary)]">
          {finalAnswer}
        </div>
      )}

      <Tabs defaultValue="table">
        <div className="flex items-center justify-between">
          <TabsList>
            <TabsTrigger value="table">表格</TabsTrigger>
            {hasChart && <TabsTrigger value="chart">图表</TabsTrigger>}
          </TabsList>
          <span className="text-xs text-[var(--text-secondary)]">{rowCount} 行</span>
        </div>

        <TabsContent value="table">
          <DataTable rows={rows} />
        </TabsContent>
        {hasChart && (
          <TabsContent value="chart">
            <ChartRenderer chartSpec={chartSpec} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
