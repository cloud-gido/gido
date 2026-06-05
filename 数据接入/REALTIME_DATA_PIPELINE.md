# 实时数据采集链路实现

## 架构说明

```
用户行为 → 埋点SDK → Nginx → Kafka → Flink实时ETL → Doris实时表 → BI展示
```

## 1. 埋点数据Schema定义

### Kafka Topic: user_behavior_events

```json
{
  "type": "record",
  "name": "UserBehaviorEvent",
  "namespace": "com.bigdata.events",
  "fields": [
    {"name": "event_id", "type": "string", "doc": "事件ID"},
    {"name": "user_id", "type": ["null", "string"], "default": null, "doc": "用户ID"},
    {"name": "device_id", "type": "string", "doc": "设备ID"},
    {"name": "event_type", "type": "string", "doc": "事件类型: page_view, click, purchase"},
    {"name": "event_time", "type": "long", "doc": "事件时间戳(毫秒)"},
    {"name": "platform", "type": "string", "doc": "平台: web, ios, android"},
    {"name": "app_version", "type": ["null", "string"], "default": null, "doc": "应用版本"},
    {"name": "properties", "type": "string", "doc": "事件属性(JSON字符串)"},
    {"name": "ip_address", "type": ["null", "string"], "default": null, "doc": "IP地址"},
    {"name": "user_agent", "type": ["null", "string"], "default": null, "doc": "User-Agent"}
  ]
}
```

## 2. Flink实时ETL任务

### Python Flink Job: flink_realtime_etl.py

```python
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.table import StreamTableEnvironment, EnvironmentSettings
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream.connectors import FlinkKafkaConsumer
from pyflink.common import WatermarkStrategy
from pyflink.common.time import Duration
import json
from datetime import datetime


def create_kafka_source(env):
    """创建Kafka数据源"""
    properties = {
        'bootstrap.servers': 'kafka:29092',
        'group.id': 'flink-etl-consumer-group',
        'auto.offset.reset': 'latest'
    }
    
    kafka_consumer = FlinkKafkaConsumer(
        topics='user_behavior_events',
        deserialization_schema=SimpleStringSchema(),
        properties=properties
    )
    
    return env.add_source(kafka_consumer)


def parse_and_clean_event(json_str):
    """解析和清洗事件数据"""
    try:
        event = json.loads(json_str)
        
        # 数据清洗规则
        if not event.get('event_id'):
            return None
        
        if not event.get('device_id'):
            return None
        
        # 转换时间戳
        event_time = event.get('event_time', 0)
        if event_time > 0:
            event['event_datetime'] = datetime.fromtimestamp(event_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
        
        # 提取常用字段
        properties = json.loads(event.get('properties', '{}'))
        event['page_url'] = properties.get('page_url', '')
        event['element_id'] = properties.get('element_id', '')
        event['duration'] = properties.get('duration', 0)
        
        return event
    except Exception as e:
        print(f"解析事件失败: {e}")
        return None


def main():
    # 创建执行环境
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(3)
    
    # 启用Checkpoint
    env.enable_checkpointing(60000)  # 60秒
    env.get_checkpoint_config().set_min_pause_between_checkpoints(30000)
    env.get_checkpoint_config().set_checkpoint_timeout(600000)
    
    # 创建Kafka源
    kafka_source = create_kafka_source(env)
    
    # 数据处理流程
    processed_stream = (
        kafka_source
        .map(parse_and_clean_event)  # 解析和清洗
        .filter(lambda x: x is not None)  # 过滤无效数据
    )
    
    # 分流处理：正常数据和异常数据
    valid_data = processed_stream.filter(lambda x: x.get('event_type') in ['page_view', 'click', 'purchase'])
    invalid_data = processed_stream.filter(lambda x: x.get('event_type') not in ['page_view', 'click', 'purchase'])
    
    # 写入Doris - 正常数据
    def sink_to_doris(value):
        """将数据写入Doris"""
        import pymysql
        
        try:
            connection = pymysql.connect(
                host='fe-leader',
                port=9030,
                user='root',
                password='',
                database='ods_db'
            )
            
            with connection.cursor() as cursor:
                sql = """
                INSERT INTO ods_user_behavior_realtime 
                (event_id, user_id, device_id, event_type, event_time, event_datetime,
                 platform, app_version, page_url, element_id, duration, ip_address, created_at)
                VALUES (%s, %s, %s, %s, FROM_UNIXTIME(%s/1000), %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                cursor.execute(sql, (
                    value.get('event_id'),
                    value.get('user_id'),
                    value.get('device_id'),
                    value.get('event_type'),
                    value.get('event_time'),
                    value.get('event_datetime'),
                    value.get('platform'),
                    value.get('app_version'),
                    value.get('page_url'),
                    value.get('element_id'),
                    value.get('duration', 0),
                    value.get('ip_address')
                ))
            
            connection.commit()
            connection.close()
        except Exception as e:
            print(f"写入Doris失败: {e}")
    
    valid_data.map(sink_to_doris)
    
    # 写入Kafka异常Topic - 异常数据
    # TODO: 配置异常数据sink
    
    # 执行任务
    env.execute("Realtime User Behavior ETL")


if __name__ == '__main__':
    main()
```

## 3. Doris表结构

### ODS层实时表

```sql
-- 创建数据库
CREATE DATABASE IF NOT EXISTS ods_db;

USE ods_db;

-- 用户行为实时表
CREATE TABLE IF NOT EXISTS ods_user_behavior_realtime (
    event_id VARCHAR(100) COMMENT '事件ID',
    user_id VARCHAR(100) COMMENT '用户ID',
    device_id VARCHAR(100) COMMENT '设备ID',
    event_type VARCHAR(50) COMMENT '事件类型',
    event_time DATETIME COMMENT '事件时间',
    event_datetime DATETIME COMMENT '事件时间(格式化)',
    platform VARCHAR(20) COMMENT '平台',
    app_version VARCHAR(20) COMMENT '应用版本',
    page_url VARCHAR(500) COMMENT '页面URL',
    element_id VARCHAR(100) COMMENT '元素ID',
    duration INT DEFAULT 0 COMMENT '停留时长(秒)',
    ip_address VARCHAR(50) COMMENT 'IP地址',
    created_at DATETIME COMMENT '入库时间'
) ENGINE=OLAP
DUPLICATE KEY(event_id)
PARTITION BY DATE_TRUNC(event_time, day)
DISTRIBUTED BY HASH(device_id) BUCKETS 10
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true",
    "dynamic_partition.enable" = "true",
    "dynamic_partition.time_unit" = "DAY",
    "dynamic_partition.start" = "-7",
    "dynamic_partition.end" = "3",
    "dynamic_partition.prefix" = "p",
    "dynamic_partition.buckets" = "10"
);
```

### DWD层明细表

```sql
CREATE DATABASE IF NOT EXISTS dwd_db;

USE dwd_db;

-- 用户行为事实表
CREATE TABLE IF NOT EXISTS dwd_user_behavior_fact (
    fact_id BIGINT AUTO_INCREMENT COMMENT '事实ID',
    event_id VARCHAR(100) COMMENT '事件ID',
    user_id VARCHAR(100) COMMENT '用户ID',
    device_id VARCHAR(100) COMMENT '设备ID',
    event_type VARCHAR(50) COMMENT '事件类型',
    event_date DATE COMMENT '事件日期',
    event_hour INT COMMENT '事件小时',
    platform VARCHAR(20) COMMENT '平台',
    page_url VARCHAR(500) COMMENT '页面URL',
    element_id VARCHAR(100) COMMENT '元素ID',
    duration INT DEFAULT 0 COMMENT '停留时长(秒)',
    province VARCHAR(50) COMMENT '省份',
    city VARCHAR(50) COMMENT '城市',
    created_at DATETIME COMMENT '创建时间'
) ENGINE=OLAP
DUPLICATE KEY(fact_id)
PARTITION BY RANGE(event_date) ()
DISTRIBUTED BY HASH(user_id) BUCKETS 10
PROPERTIES (
    "replication_num" = "1",
    "dynamic_partition.enable" = "true",
    "dynamic_partition.time_unit" = "DAY",
    "dynamic_partition.start" = "-30",
    "dynamic_partition.end" = "3"
);

-- 定时从ODS同步到DWD（每小时）
INSERT INTO dwd_user_behavior_fact
SELECT 
    NULL as fact_id,
    event_id,
    user_id,
    device_id,
    event_type,
    DATE(event_time) as event_date,
    HOUR(event_time) as event_hour,
    platform,
    page_url,
    element_id,
    duration,
    '' as province,  -- TODO: IP解析
    '' as city,
    NOW() as created_at
FROM ods_db.ods_user_behavior_realtime
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
AND event_id NOT IN (SELECT event_id FROM dwd_user_behavior_fact WHERE event_date = CURDATE());
```

### DWS层汇总表

```sql
CREATE DATABASE IF NOT EXISTS dws_db;

USE dws_db;

-- 用户行为小时汇总
CREATE TABLE IF NOT EXISTS dws_user_behavior_hourly (
    stat_date DATE COMMENT '统计日期',
    stat_hour INT COMMENT '统计小时',
    platform VARCHAR(20) COMMENT '平台',
    event_type VARCHAR(50) COMMENT '事件类型',
    uv_count BIGINT COMMENT '独立用户数',
    pv_count BIGINT COMMENT '页面浏览量',
    event_count BIGINT COMMENT '事件总数',
    avg_duration DECIMAL(10,2) COMMENT '平均停留时长',
    created_at DATETIME COMMENT '创建时间'
) ENGINE=OLAP
AGGREGATE KEY(stat_date, stat_hour, platform, event_type)
DISTRIBUTED BY HASH(stat_date) BUCKETS 5
PROPERTIES (
    "replication_num" = "1"
);

-- 每小时聚合
INSERT INTO dws_user_behavior_hourly
SELECT 
    event_date as stat_date,
    event_hour as stat_hour,
    platform,
    event_type,
    COUNT(DISTINCT user_id) as uv_count,
    SUM(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END) as pv_count,
    COUNT(*) as event_count,
    AVG(duration) as avg_duration,
    NOW() as created_at
FROM dwd_db.dwd_user_behavior_fact
WHERE event_date = CURDATE()
GROUP BY event_date, event_hour, platform, event_type
ON DUPLICATE KEY UPDATE
    uv_count = VALUES(uv_count),
    pv_count = VALUES(pv_count),
    event_count = VALUES(event_count),
    avg_duration = VALUES(avg_duration),
    created_at = VALUES(created_at);
```

### ADS层应用表

```sql
CREATE DATABASE IF NOT EXISTS ads_db;

USE ads_db;

-- 实时看板指标
CREATE TABLE IF NOT EXISTS ads_realtime_dashboard (
    stat_time DATETIME COMMENT '统计时间',
    platform VARCHAR(20) COMMENT '平台',
    realtime_uv INT COMMENT '实时UV(最近1小时)',
    realtime_pv INT COMMENT '实时PV(最近1小时)',
    total_uv_today INT COMMENT '今日累计UV',
    total_pv_today INT COMMENT '今日累计PV',
    created_at DATETIME COMMENT '创建时间'
) ENGINE=OLAP
DUPLICATE KEY(stat_time, platform)
DISTRIBUTED BY HASH(stat_time) BUCKETS 3
PROPERTIES (
    "replication_num" = "1"
);

-- 每5分钟更新
INSERT INTO ads_realtime_dashboard
SELECT 
    NOW() as stat_time,
    platform,
    (SELECT COUNT(DISTINCT user_id) FROM dwd_db.dwd_user_behavior_fact 
     WHERE event_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR) AND platform = t.platform) as realtime_uv,
    (SELECT COUNT(*) FROM dwd_db.dwd_user_behavior_fact 
     WHERE event_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR) AND event_type = 'page_view' AND platform = t.platform) as realtime_pv,
    (SELECT COUNT(DISTINCT user_id) FROM dwd_db.dwd_user_behavior_fact 
     WHERE event_date = CURDATE() AND platform = t.platform) as total_uv_today,
    (SELECT COUNT(*) FROM dwd_db.dwd_user_behavior_fact 
     WHERE event_date = CURDATE() AND event_type = 'page_view' AND platform = t.platform) as total_pv_today,
    NOW() as created_at
FROM (SELECT DISTINCT platform FROM dwd_db.dwd_user_behavior_fact WHERE event_date = CURDATE()) t
ON DUPLICATE KEY UPDATE
    realtime_uv = VALUES(realtime_uv),
    realtime_pv = VALUES(realtime_pv),
    total_uv_today = VALUES(total_uv_today),
    total_pv_today = VALUES(total_pv_today),
    created_at = VALUES(created_at);
```

## 4. 部署脚本

### start-realtime-pipeline.sh

```bash
#!/bin/bash

echo "启动实时数据采集链路..."

# 1. 启动Kafka
echo "启动Kafka..."
docker-compose -f docker-compose-infrastructure.yml up -d kafka schema-registry
# Schema Registry 对外为 http://localhost:8082（8081 留给 Flink JM，见 docker-compose-infrastructure.yml）

# 2. 创建Kafka Topic
echo "创建Kafka Topic..."
docker exec -it kafka-broker kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic user_behavior_events \
  --partitions 6 \
  --replication-factor 1

# 3. 部署 Flink Session（Kubernetes，仓库根 k8s/flink.yaml）
echo "部署 Flink Session 到 Kubernetes..."
kubectl apply -f /path/to/bigdata_all/k8s/flink.yaml
bash /path/to/bigdata_all/k8s/redeploy-flink.sh

# （可选）GIDO 后端在 Docker 内需提交 K8s Application 时：自建 compose override 挂载 kubeconfig 到 /root/.kube/host-kubeconfig，或设 FLINK_K8S_KUBECONFIG_PATH（见 gido/backend/docker-entrypoint.sh）

# 4. 提交 Flink 任务（见 Flink 官方文档 flink run / SQL Client；GIDO 实时开发走 SQL Gateway）
echo "提交 Flink 任务：见 Flink 官方文档 flink run / SQL Client"

# 5. 初始化Doris表
echo "初始化Doris表结构..."
mysql -h fe-leader -P 9030 -u root -p < init_realtime_tables.sql

echo "实时数据采集链路启动完成！"
echo "访问以下服务:"
echo "  - Flink Dashboard: http://localhost:8081"
echo "  - Doris FE: http://localhost:8030"
echo "  - Kafka Manager: 使用kafka-tool连接 localhost:9092"
```

## 5. 测试数据生成器

### generate_test_data.py

```python
import json
import uuid
import time
import random
from kafka import KafkaProducer
from datetime import datetime

def generate_event():
    """生成模拟用户行为事件"""
    event_types = ['page_view', 'click', 'purchase']
    platforms = ['web', 'ios', 'android']
    
    event = {
        'event_id': str(uuid.uuid4()),
        'user_id': f'user_{random.randint(1, 1000)}',
        'device_id': f'device_{uuid.uuid4().hex[:12]}',
        'event_type': random.choice(event_types),
        'event_time': int(time.time() * 1000),
        'platform': random.choice(platforms),
        'app_version': f'{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}',
        'properties': json.dumps({
            'page_url': f'/page/{random.randint(1, 50)}',
            'element_id': f'btn_{random.randint(1, 100)}',
            'duration': random.randint(1, 300)
        }),
        'ip_address': f'{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}',
        'user_agent': 'Mozilla/5.0 (Test Agent)'
    }
    
    return event


def main():
    # 创建Kafka生产者
    producer = KafkaProducer(
        bootstrap_servers=['localhost:9092'],
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    
    print("开始发送测试数据...")
    
    try:
        while True:
            event = generate_event()
            producer.send('user_behavior_events', value=event)
            print(f"发送事件: {event['event_id']} - {event['event_type']}")
            
            # 每秒发送1-5个事件
            time.sleep(random.uniform(0.2, 1.0))
    except KeyboardInterrupt:
        print("\n停止发送数据")
    finally:
        producer.flush()
        producer.close()


if __name__ == '__main__':
    main()
```

## 6. 监控查询

### 实时监控SQL

```sql
-- 最近1小时各平台PV趋势
SELECT 
    DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:00') as time_slot,
    platform,
    COUNT(*) as pv
FROM dwd_db.dwd_user_behavior_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
AND event_type = 'page_view'
GROUP BY time_slot, platform
ORDER BY time_slot;

-- 实时在线用户数（最近5分钟有行为的用户）
SELECT 
    platform,
    COUNT(DISTINCT user_id) as online_users
FROM dwd_db.dwd_user_behavior_fact
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
GROUP BY platform;

-- 今日各时段UV/PV
SELECT 
    event_hour,
    COUNT(DISTINCT user_id) as uv,
    COUNT(*) as pv
FROM dwd_db.dwd_user_behavior_fact
WHERE event_date = CURDATE()
GROUP BY event_hour
ORDER BY event_hour;
```
