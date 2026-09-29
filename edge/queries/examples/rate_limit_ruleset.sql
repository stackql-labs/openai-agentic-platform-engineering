-- id: edge/examples/rate_limit_ruleset
-- providers: cloudflare
-- params: cloudflare_zone_id
-- expected_columns: id, rule_id, description, threshold, period
-- description: Example of the shape only, not run by code - the rate limit phase entrypoint ruleset and its first rule; the recon agent finds the equivalent through the query library (cloudflare/rulesets/rate-limit-ruleset)
SELECT id, JSON_EXTRACT(rules, '$[0].id') AS rule_id, JSON_EXTRACT(rules, '$[0].description') AS description, JSON_EXTRACT(rules, '$[0].ratelimit.requests_per_period') AS threshold, JSON_EXTRACT(rules, '$[0].ratelimit.period') AS period
FROM cloudflare.rulesets.phases WHERE zone_id = '{{ cloudflare_zone_id }}' AND ruleset_phase = 'http_ratelimit'
