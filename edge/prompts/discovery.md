# Working with the StackQL tools

The tools expose cloud and SaaS providers as data sources accessed via SQL. Objects are named
`provider.service.resource`. You are not given resource names, columns or SQL: discover them.

Order of work for every question:

1. `query_library_search` with your intent (and the provider) first. When a hit fits, call
   `query_library_get` with its id and the params it declares; the rendered template is ready to run.
2. When nothing fits, drill down: `list_services` -> `list_resources` -> `describe_resource` (output
   columns) -> `list_methods` (the SQL verb and required params per method) -> `describe_method` (the
   full input contract, `param_type` says which inputs are required).
3. `validate_select_query` on any SELECT you composed yourself, then `run_select_query` with
   `format` set to `json`. Templates from the library do not need a separate validation call.

Dialect facts that matter here:

- Required params of a method are WHERE conditions (`WHERE zone_id = '...' AND ...`); a query without
  them fails, it does not scan everything.
- JSON columns are read with `json_extract(col, '$.path')` and expanded with `json_each`.
- Zero rows is a valid answer. Do not retry a query that returned no rows, do not widen it and do not
  invent resources or values to fill the gap.
- `validate_select_query` does not accept WITH / CTEs: write flat SELECTs, one query per step.
- Time bounds and ids are literals in the statement; the credentials are on the server, you never
  see or supply them.

A mutation is never executed by you. When a change is called for, put the exact statement in your
output and stop; code checks it, an operator approves it, and code runs it.
