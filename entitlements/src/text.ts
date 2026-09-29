/** Small text helpers shared by the console output, the ledger and the report. */

/** Fixed-width text table. Cells are truncated to keep the console readable. */
export function table(headers: string[], rows: string[][], maxWidth = 60): string {
  const clip = (s: string) => (s.length > maxWidth ? `${s.slice(0, maxWidth - 3)}...` : s);
  const all = [headers, ...rows.map((r) => r.map(clip))];
  const widths = headers.map((_, i) => Math.max(...all.map((r) => (r[i] ?? '').length)));
  const line = (r: string[]) => r.map((c, i) => (c ?? '').padEnd(widths[i]!)).join('  ').trimEnd();
  const sep = widths.map((w) => '-'.repeat(w)).join('  ');
  return [line(headers), sep, ...rows.map((r) => line(r.map(clip)))].join('\n');
}

/** UTC timestamp for file names: 20260929T081500Z */
export function timestamp(d: Date): string {
  return d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}
