/**
 * Minimal SQL splitting for validation only (never used to build queries).
 *
 * validate_select_query in stackql 0.12.718 rejects `WITH name AS (...)` statements that
 * run_select_query executes fine. The validate subcommand therefore validates each CTE body (the
 * provider-facing SELECT, where resource and column names matter) through the MCP tool, and
 * checks the shape of the whole statement offline with node:sqlite - the same SQL engine StackQL
 * uses as its backend - by replacing every CTE body with a stub table that has the body's
 * projected columns.
 */

export interface Cte {
  name: string;
  body: string;
}

export interface SplitSql {
  ctes: Cte[];
  outer: string;
}

/** Index of the parenthesis that closes the one at `open`, honouring single-quoted strings. */
function matchParen(sql: string, open: number): number {
  let depth = 0;
  let inStr = false;
  for (let i = open; i < sql.length; i += 1) {
    const c = sql[i];
    if (inStr) {
      if (c === "'") {
        if (sql[i + 1] === "'") i += 1;
        else inStr = false;
      }
      continue;
    }
    if (c === "'") inStr = true;
    else if (c === '(') depth += 1;
    else if (c === ')') {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  throw new Error('unbalanced parentheses');
}

/** Split `WITH a AS (...), b AS (...) SELECT ...` into its CTE bodies and the outer statement. */
export function splitCtes(sql: string): SplitSql {
  const text = sql.trim();
  if (!/^WITH\s/i.test(text)) return { ctes: [], outer: text };
  const ctes: Cte[] = [];
  let pos = 4;
  for (;;) {
    const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(/i.exec(text.slice(pos));
    if (!m) throw new Error(`cannot parse CTE at position ${pos}`);
    const open = pos + m[0].length - 1;
    const close = matchParen(text, open);
    ctes.push({ name: m[1]!, body: text.slice(open + 1, close).trim() });
    pos = close + 1;
    const next = /^\s*,/.exec(text.slice(pos));
    if (!next) break;
    pos += next[0].length;
  }
  return { ctes, outer: text.slice(pos).trim() };
}

/** Split on top-level commas (outside parentheses and strings). */
export function splitTopLevel(s: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let inStr = false;
  let cur = '';
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i]!;
    if (inStr) {
      cur += c;
      if (c === "'") inStr = false;
      continue;
    }
    if (c === "'") inStr = true;
    else if (c === '(') depth += 1;
    else if (c === ')') depth -= 1;
    if (c === ',' && depth === 0) {
      out.push(cur.trim());
      cur = '';
    } else cur += c;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/** Output column names of a simple SELECT (alias, else the last dotted identifier). */
export function projectedColumns(selectSql: string): string[] {
  const first = splitTopLevel(selectSql.replace(/\s+/g, ' '))
    .join(',')
    .replace(/^\s*SELECT\s+/i, '');
  // the select list ends at the first top-level FROM
  let depth = 0;
  let inStr = false;
  let end = first.length;
  for (let i = 0; i < first.length; i += 1) {
    const c = first[i]!;
    if (inStr) {
      if (c === "'") inStr = false;
      continue;
    }
    if (c === "'") inStr = true;
    else if (c === '(') depth += 1;
    else if (c === ')') depth -= 1;
    else if (depth === 0 && /\sFROM\s/i.test(first.slice(Math.max(0, i - 1), i + 5))) {
      end = i;
      break;
    }
  }
  return splitTopLevel(first.slice(0, end)).map((expr) => {
    const alias = /\sAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$/i.exec(expr);
    if (alias) return alias[1]!;
    const ident = /([A-Za-z_][A-Za-z0-9_]*)\s*$/.exec(expr);
    return ident ? ident[1]! : `col${Math.random().toString(36).slice(2, 6)}`;
  });
}

/** `UNION ALL` of literal rows (the rendered aws_admins CTE) -> its column list. */
export function unionColumns(body: string): string[] {
  const firstBranch = body.split(/\bUNION\s+ALL\b/i)[0]!;
  return projectedColumns(firstBranch);
}

/** `provider.service.resource` of a simple provider SELECT, or null. */
export function resourceOf(selectSql: string): { provider: string; service: string; resource: string } | null {
  const m = /\bFROM\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)/i.exec(selectSql);
  return m ? { provider: m[1]!, service: m[2]!, resource: m[3]! } : null;
}

/**
 * Column names a simple provider SELECT reads (bare identifiers in the select list, the source
 * side of `col AS alias`); expressions, functions and literals are skipped. Used to check a CTE
 * body against describe_resource when the provider refuses dummy credentials before the
 * column check would have run.
 */
export function sourceColumns(selectSql: string): string[] {
  const one = selectSql.replace(/\s+/g, ' ').trim();
  const list = /^SELECT\s+(.*?)\s+FROM\s/i.exec(one);
  if (!list) return [];
  const out: string[] = [];
  for (const expr of splitTopLevel(list[1]!)) {
    const src = expr.replace(/\s+AS\s+[A-Za-z_][A-Za-z0-9_]*\s*$/i, '').trim();
    if (/^[A-Za-z_][A-Za-z0-9_@]*$/.test(src)) out.push(src);
  }
  return out;
}
