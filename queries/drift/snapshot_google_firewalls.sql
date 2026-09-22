-- id: drift/snapshot_google_firewalls
-- scenario: drift
-- providers: google
-- params: google_project
-- expected_columns: selfLink, name, network, direction, disabled, sourceRanges, allowed, priority
-- description: snapshot source - firewall rules in the demo project; materialized as snap_<ts>_google_firewalls
SELECT selfLink, name, network, direction, disabled, sourceRanges, allowed, priority
FROM google.compute.firewalls
WHERE project = '{{ google_project }}'
