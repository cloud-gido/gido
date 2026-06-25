-- GIDO Stream · 预览查询 Paimon on S3
SET 'execution.runtime-mode' = 'batch';
SET 'fs.s3a.access.key' = 'REPLACE_AKIA';
SET 'fs.s3a.secret.key' = 'REPLACE_SECRET';
SET 'fs.s3a.endpoint' = 's3.us-east-2.amazonaws.com';
SET 'fs.s3a.aws.credentials.provider' = 'org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider';

CREATE TABLE paimon_users_s3 (
  user_id BIGINT,
  user_name STRING,
  age INT
) WITH (
  'connector' = 'paimon',
  'path' = 's3a://amzn-test-gido-s3/demo/users'
);

SELECT * FROM paimon_users_s3;
