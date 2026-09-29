-- id: edge/zone_traffic
-- providers: cloudflare
-- params: cloudflare_zone_id, since, until
-- expected_columns: datetime, client_country_name, edge_response_status, client_request_http_method_name, requests, bytes
-- description: HTTP request groups for the demo zone over the recon window (zone analytics, adaptive sampling); statement 1 of the recon agent
SELECT datetime, client_country_name, edge_response_status, client_request_http_method_name, requests, bytes
FROM cloudflare.zones.http_requests_adaptive_groups
WHERE zone_tag = '{{ cloudflare_zone_id }}' AND since = '{{ since }}' AND until = '{{ until }}'
