-- GIDO 流式 SQL 冒烟：内置 datagen 源 + print _sink，无需 Kafka/MySQL/S3
-- 验证：Operator 建 CR → SqlRunner 执行 → 作业 RUNNING → TM 日志有 print 输出

SET 'execution.runtime-mode' = 'streaming';

CREATE TABLE gido_smoke_source (
  event_id BIGINT,
  event_time TIMESTAMP(3),
  payload STRING,
  WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
  'connector' = 'datagen',
  'rows-per-second' = '2',
  'fields.event_id.kind' = 'sequence',
  'fields.event_id.start' = '1',
  'fields.event_id.end' = '9223372036854775807',
  'fields.event_time.kind' = 'random',
  'fields.payload.length' = '16'
);

CREATE TABLE gido_smoke_print (
  event_id BIGINT,
  event_time TIMESTAMP(3),
  payload STRING
) WITH (
  'connector' = 'print',
  'print-identifier' = 'gido-smoke',
  'standard-error' = 'false'
);

INSERT INTO gido_smoke_print
SELECT event_id, event_time, payload
FROM gido_smoke_source;