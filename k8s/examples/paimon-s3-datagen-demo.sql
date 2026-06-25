-- GIDO Stream · Paimon on S3（datagen → S3）
-- 桶: amzn-test-gido-s3  区域: us-east-2
-- 须用 s3a:// + fs.s3a.*（勿仅用表属性 s3.access-key，Flink 插件读不到）
-- 提交前替换 AK/SK；密钥勿提交 Git。

SET 'execution.runtime-mode' = 'batch';
SET 'fs.s3a.access.key' = 'REPLACE_AKIA';
SET 'fs.s3a.secret.key' = 'REPLACE_SECRET';
SET 'fs.s3a.endpoint' = 's3.us-east-2.amazonaws.com';
SET 'fs.s3a.aws.credentials.provider' = 'org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider';

CREATE TABLE datagen_users (
  user_id BIGINT,
  user_name STRING,
  age INT
) WITH (
  'connector' = 'datagen',
  'number-of-rows' = '100',
  'fields.user_id.kind' = 'sequence',
  'fields.user_id.start' = '1',
  'fields.user_id.end' = '100'
);

CREATE TABLE paimon_users_s3 (
  user_id BIGINT,
  user_name STRING,
  age INT
) WITH (
  'connector' = 'paimon',
  'path' = 's3a://amzn-test-gido-s3/demo/users',
  'write-mode' = 'append-only',
  'auto-create' = 'true'
);

INSERT INTO paimon_users_s3
SELECT user_id, user_name, age FROM datagen_users;
