-- id: smoke/google_buckets
-- scenario: smoke
-- providers: google
-- params: google_project
-- expected_columns: name, location
-- description: storage buckets in the demo project - proves google auth
SELECT name, location
FROM google.storage.buckets
WHERE project = '{{ google_project }}'
