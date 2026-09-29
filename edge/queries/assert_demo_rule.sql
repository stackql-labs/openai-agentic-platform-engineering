-- id: edge/assert_demo_rule
-- providers: cloudflare
-- params: cloudflare_zone_id
-- expected_columns: id, rule_id, description, threshold, period
-- description: Code-owned, never shown to a model - the gate reads the first rule of the http_ratelimit phase to assert its description carries DEMO_PREFIX before a mutation and to verify the threshold after it; restore reads the current threshold with it
SELECT id, JSON_EXTRACT(rules, '$[0].id') AS rule_id, JSON_EXTRACT(rules, '$[0].description') AS description, JSON_EXTRACT(rules, '$[0].ratelimit.requests_per_period') AS threshold, JSON_EXTRACT(rules, '$[0].ratelimit.period') AS period
FROM cloudflare.rulesets.phases WHERE zone_id = '{{ cloudflare_zone_id }}' AND ruleset_phase = 'http_ratelimit'
