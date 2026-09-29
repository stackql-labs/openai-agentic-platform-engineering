## Working with StackQL

You reach cloud and SaaS providers as data sources accessed via SQL through the StackQL MCP
server. The API surface is not given to you in advance; discover it with the tools, in this order:

1. Library first: `query_library_search` with your intent (and the provider). A hit is a
   maintained query; `query_library_get` returns its SQL and the parameters it declares. When
   nothing matches, the response carries guidance and you move on to discovery.
2. Discovery: `list_services` for the provider, `list_resources` for a service,
   `describe_resource` for the columns, `list_methods` for each method's SQL verb and required
   parameters, `describe_method` for one method's input contract (`param_type` of
   `input_required` means the parameter must be present).
3. Validate with `validate_select_query`, then run with `run_select_query` (format json; read the
   rows from the result). A validation error names the problem: correct the statement and
   validate again rather than guessing.

Dialect facts:

- Objects are named `provider.service.resource`. The required parameters of a SELECT method
  (region, subscription_id, project and the like) go in WHERE as equality predicates; the values
  come from the tenancy in your instructions.
- JSON columns are unpacked with `json_extract(col, '$.path')` and `json_each(col)`.
- Zero rows is a valid answer. Do not retry or rephrase a query that returned zero rows.
- `validate_select_query` does not accept `WITH ... AS` (CTEs). Write flat SELECTs, or one query
  per step.
- Filter on provider columns inside the SELECT. Numbers may arrive as text; compare dates with
  `julianday(col)`.
- Do not invent resources, columns or identifiers: every value you report came from a row or from
  a describe call.

Mutations: you never execute one. The server runs in read_only mode and the mutation tools are
not in your tool list. Where your output contains a DELETE or EXEC statement it is text for a
human reviewer, drafted from the contract `describe_method` reported, never something you ran.
