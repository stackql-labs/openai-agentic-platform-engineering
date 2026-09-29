-- id: edge/rate_limit_ruleset
-- providers: cloudflare
-- params: cloudflare_zone_id
-- expected_columns: id, rule_id, description, threshold, period
-- description: The zone's http_ratelimit phase entrypoint ruleset and its first (only) rule; statement 2 of the recon agent, the gate's target assertion and its post-mutation verification
SELECT id, JSON_EXTRACT(rules, '$[0].id') AS rule_id, JSON_EXTRACT(rules, '$[0].description') AS description, JSON_EXTRACT(rules, '$[0].ratelimit.requests_per_period') AS threshold, JSON_EXTRACT(rules, '$[0].ratelimit.period') AS period
FROM cloudflare.rulesets.phases WHERE zone_id = '{{ cloudflare_zone_id }}' AND ruleset_phase = 'http_ratelimit'
