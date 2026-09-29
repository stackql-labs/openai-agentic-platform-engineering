StackQL discovery briefing (shared by every agent in this use case)

How to find what to query, in this order:
1. query_library_search with your intent (and the provider). Hits carry a query id;
   query_library_get with that id and its params renders a vetted SELECT that needs no further
   discovery.
2. Otherwise drill down: list_services -> list_resources -> describe_resource (the output
   columns of the primary read method) -> list_methods (SQL verb and required params per
   method) -> describe_method (the full input contract; param_type input_required means the
   value must be in WHERE).
3. validate_select_query when a statement is new or wide, then run_select_query. Pass format
   "json" and read the rows.

Dialect facts that matter:
- Objects are named provider.service.resource; there is no other table namespace.
- A method's required parameters go in WHERE as equality predicates. An equality predicate on a
  column that is itself a parameter of another method of the same resource routes the query to
  that method, which then needs its own required parameters: satisfy that method's contract, or
  filter such a column in a second step.
- JSON columns are read with json_extract(col, '$.path') and unnested with json_each(col).
- Booleans arrive as 0/1 or 'true'/'false' depending on the provider; compare against both.
- Zero rows is a valid answer. Do not retry a query that returned nothing.
- validate_select_query does not accept WITH / common table expressions: write flat SELECTs, or
  run one query per step and combine the rows yourself.
- A credential or authorization error names the provider; report it, do not retry.

A mutation is never executed by a model in this use case. The server runs in read_only mode and
the mutation tools are not in your tool list; a statement that would change something is text in
your output for a human to review.
