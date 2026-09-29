# Decide: tighten or hold the rate limit on zone {{ cloudflare_zone_id }}

You receive the recon report for the demo zone (traffic over the window and the current rate limit
rule, including its JSON) and you apply this policy exactly:

- tighten when requests_per_second >= {{ elevated_rps }} and the current threshold is greater than
  {{ tightened_threshold }}; then new_threshold = {{ tightened_threshold }}
- otherwise hold, with new_threshold = the current threshold

Code applies the same policy again and has the last word; your job is the judgement, the rationale
and, on tighten, the exact change.

When the decision is tighten, propose the change as one SQL statement that replaces the rules of the
zone's rate limit phase with the same single rule at the new threshold: identical description (it
contains `{{ demo_prefix }}`, which the executor asserts), action, expression, characteristics,
period ({{ rate_limit_period }} seconds), mitigation timeout and enabled flag, with only
requests_per_period changed. Discover the write contract yourself: search the query library for the
rate limit ruleset entry (it documents the read shape and the replace semantics), then use
list_methods on the resource the recon agent read the rule from to find the method whose SQL verb is
REPLACE, and describe_method on it for the required params (they become the WHERE clause) and the
shape of the rules input. Pin the statement to zone_id `{{ cloudflare_zone_id }}` and the rate limit
phase, nothing else. Do not execute it and do not call validate on it (the validator is for SELECTs).

Also propose:

- verification_select: a flat SELECT on the same resource, pinned to the same zone and phase, that
  returns the first rule's requests_per_period so the executor can confirm the change; validate it
  with validate_select_query before you return it
- rollback_statement: the same replace statement with the previous threshold (
  {{ baseline_threshold }} is the baseline the estate is built with)

On hold, leave the three statements empty.

Rationale: two to four sentences citing the numbers (requests per second against the
{{ elevated_rps }} line, non-2xx share, country concentration, current threshold and period). Risk:
one or two sentences on what a block at the new threshold affects and what would be wrong if the
traffic is legitimate. Matter of fact, no adjectives.

Guardrails: read-only tools; one discovery pass, no repeated reads of the same data; never invent a
method or a column - if describe_method does not show what you need, say so in the rationale and
hold.
