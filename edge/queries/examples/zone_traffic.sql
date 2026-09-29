-- id: edge/examples/zone_traffic
-- providers: cloudflare
-- params: cloudflare_zone_id, since, until
-- expected_columns: datetime, client_country_name, edge_response_status, client_request_http_method_name, requests, bytes
-- description: Example of the shape only, not run by code - HTTP request groups for the demo zone over a window; the recon agent finds the equivalent through the query library (cloudflare/zones/http-analytics) or by describing the resource
SELECT datetime, client_country_name, edge_response_status, client_request_http_method_name, requests, bytes
FROM cloudflare.zones.http_requests_adaptive_groups
WHERE zone_tag = '{{ cloudflare_zone_id }}' AND since = '{{ since }}' AND until = '{{ until }}'
