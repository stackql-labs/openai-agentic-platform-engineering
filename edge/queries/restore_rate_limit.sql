-- id: edge/restore_rate_limit
-- providers: cloudflare
-- params: cloudflare_zone_id, demo_prefix, threshold
-- expected_columns: none (mutation)
-- description: Code-owned, used only by the restore subcommand where no model is involved - REPLACE the http_ratelimit phase with the single demo rule at the given requests per 10 seconds; the run path executes the statement the decision agent discovered and proposed instead
REPLACE cloudflare.rulesets.phases SET rules = '[{"action":"block","ratelimit":{"characteristics":["ip.src","cf.colo.id"],"period":10,"requests_per_period":{{ threshold }},"mitigation_timeout":10},"expression":"(starts_with(http.request.uri.path, \"/\"))","description":"{{ demo_prefix }} rate limit (managed by stackql-deploy)","enabled":true}]' WHERE zone_id = '{{ cloudflare_zone_id }}' AND ruleset_phase = 'http_ratelimit'
