-- id: finops/aws_stale_snapshots
-- scenario: finops
-- providers: aws
-- params: aws_region, snapshot_max_age_days
-- expected_columns: snapshot_id, volume_id, size_gb, start_time, encrypted, tags, age_days, est_monthly_usd
-- description: EBS snapshots owned by the account (owner=self) older than the threshold (0.05 USD/GB-month standard tier)
WITH s AS (
  SELECT snapshot_id, volume_id, volume_size, start_time, encrypted, tags
  FROM aws.ec2.snapshots
  WHERE region = '{{ aws_region }}'
    AND owner = 'self'
)
SELECT snapshot_id, volume_id, volume_size AS size_gb, start_time, encrypted, tags,
  ROUND(julianday('now') - julianday(start_time), 0) AS age_days,
  ROUND(volume_size * 0.05, 2) AS est_monthly_usd
FROM s
WHERE julianday(start_time) < julianday('now', '-{{ snapshot_max_age_days }} days')
ORDER BY age_days DESC
