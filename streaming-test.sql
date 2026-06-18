-- GIDO 流式 SQL 冒烟：内置 datagen 源 + print _sink，无需 Kafka/MySQL/S3
-- 验证：Operator 建 CR → SqlRunner 执行 → 作业 RUNNING → TM 日志有 print 输出
-- 注意：Flink 1.17 datagen 的 sequence 勿用 Long.MAX_VALUE 作 end（SequenceGenerator 会 IllegalArgumentException，见 FLINK-18909）

SET 'execution.runtime-mode' = 'streaming';

CREATE TABLE gido_smoke_source (
  event_id BIGINT,
  event_time TIMESTAMP(3),
  payload STRING,
  WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
  'connector' = 'datagen',
  'rows-per-second' = '2',
  'fields.event_id.kind' = 'random',
  'fields.event_id.min' = '1',
  'fields.event_id.max' = '999999999',
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