-- id: cspm/google_firewalls_open_ingress
-- scenario: cspm
-- providers: google
-- params: google_project
-- expected_columns: name, network, priority, source_ranges, allowed
-- description: enabled INGRESS firewall rules with 0.0.0.0/0 in their source ranges
WITH fw AS (
  SELECT name, network, direction, disabled, priority, sourceRanges AS source_ranges, allowed
  FROM google.compute.firewalls
  WHERE project = '{{ google_project }}'
)
SELECT name, network, priority, source_ranges, allowed
FROM fw
WHERE direction = 'INGRESS'
  AND disabled IN ('false', '0', 0)
  AND source_ranges LIKE '%0.0.0.0/0%'
