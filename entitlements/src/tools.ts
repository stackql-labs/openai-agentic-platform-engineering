/**
 * Function tools the sweep agent gets beside the StackQL MCP server. The model never writes SQL:
 * it asks render_query for a canonical query by id with parameter values (fan-out lists come from
 * inventory rows returned earlier) and runs the returned SQL verbatim with run_select_query.
 */

import { tool } from '@openai/agents';
import { z } from 'zod';
import { listQueries, loadQuery, renderQuery, renderUnion, sqlList } from './queries.ts';

/** JSON array -> literal IN (...) list. */
export const LIST_PARAMS = new Set(['github_repo_list']);
/** JSON array -> one SELECT per value, UNION ALL-ed (the API does not echo the key). */
export const UNION_PARAMS = new Set(['iam_user_name', 'user_name']);
/** JSON array of IAM user names with AdministratorAccess -> the aws_admins CTE body. */
export const AWS_ADMIN_USERS_PARAM = 'aws_admin_users';
export const AWS_ADMIN_LITERAL_QUERY = 'entitlements/aws_admin_user_literal';

function asStrings(v: unknown): string[] {
  return (Array.isArray(v) ? v : [v]).map((x) => String(x));
}

/** Pure implementation of the render_query tool (unit tested without the SDK). */
export function renderQueryForModel(queryId: string, paramsJson: string): string {
  const raw: unknown = JSON.parse(paramsJson || '{}');
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('params_json must be a JSON object');
  }
  const params = raw as Record<string, unknown>;
  const q = loadQuery(queryId);
  const overrides: Record<string, string> = {};
  let union: { param: string; values: string[] } | null = null;
  for (const [k, v] of Object.entries(params)) {
    if (k === AWS_ADMIN_USERS_PARAM) {
      const values = asStrings(v);
      overrides.aws_admins_sql = values.length
        ? renderUnion(loadQuery(AWS_ADMIN_LITERAL_QUERY), 'user_name', values)
        : `${renderQuery(loadQuery(AWS_ADMIN_LITERAL_QUERY), { user_name: '-' })} WHERE 1 = 0`;
    } else if (LIST_PARAMS.has(k)) {
      const values = asStrings(v);
      if (!values.length) throw new Error(`${k}: empty list - skip this query when there is nothing to fan out over`);
      overrides[k] = sqlList(values);
    } else if (UNION_PARAMS.has(k) && Array.isArray(v)) {
      union = { param: k, values: asStrings(v) };
    } else {
      overrides[k] = String(v);
    }
  }
  if (union) return renderUnion(q, union.param, union.values, overrides);
  return renderQuery(q, overrides);
}

export const renderQueryTool = tool({
  name: 'render_query',
  description:
    'Render a canonical query from the library with parameter values and return the SQL to run verbatim with run_select_query. ' +
    'List parameters (github_repo_list) take a JSON array of strings. Per-key parameters (iam_user_name) take a JSON array too: ' +
    'one SELECT per value is rendered and UNION ALL-ed. aws_admin_users takes a JSON array of IAM user names that hold ' +
    'AdministratorAccess (may be empty). Parameters with a fixed value in the environment are filled in automatically.',
  parameters: z.object({
    query_id: z.string().describe('Query id, e.g. entitlements/aws_iam_users'),
    params_json: z.string().describe('JSON object of parameter values; "{}" when none are dynamic'),
  }),
  execute: async ({ query_id, params_json }) => {
    try {
      return renderQueryForModel(query_id, params_json);
    } catch (e) {
      return `error: ${(e as Error).message}`;
    }
  },
});

export function listQueriesForModel(idp: string): string {
  return listQueries()
    .filter((q) => q.idp === null || q.idp === idp)
    .map((q) => `${q.id}: ${q.description} (params: ${q.params.join(', ') || 'none'})`)
    .join('\n');
}

export function listQueriesTool(idp: string) {
  return tool({
    name: 'list_queries',
    description: 'List the canonical query ids, descriptions and parameters available to this sweep.',
    parameters: z.object({}),
    execute: async () => listQueriesForModel(idp),
  });
}
