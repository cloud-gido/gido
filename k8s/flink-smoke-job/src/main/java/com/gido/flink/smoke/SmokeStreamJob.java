package com.gido.flink.smoke;

import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * GIDO Operator JAR 冒烟作业：内置序列源 + print，不依赖 Kafka / S3 / Paimon。
 *
 * <p>入口类：{@code com.gido.flink.smoke.SmokeStreamJob}
 *
 * <p>环境变量（可选）：
 * <ul>
 *   <li>{@code GIDO_SMOKE_ROWS} — 最大事件数，默认 200；0 表示无限</li>
 *   <li>{@code GIDO_SMOKE_INTERVAL_MS} — 每条间隔毫秒，默认 500</li>
 * </ul>
 */
public final class SmokeStreamJob {

    private SmokeStreamJob() {}

    public static void main(String[] args) throws Exception {
        long maxRows = parseLongEnv("GIDO_SMOKE_ROWS", 200L);
        long intervalMs = parseLongEnv("GIDO_SMOKE_INTERVAL_MS", 500L);
        if (intervalMs < 0) {
            intervalMs = 0;
        }

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);

        long end = maxRows > 0 ? maxRows : Long.MAX_VALUE;
        final long sleepMs = intervalMs;
        DataStream<String> lines = env
                .fromSequence(1, end)
                .map((MapFunction<Long, String>) value -> {
                    if (sleepMs > 0) {
                        Thread.sleep(sleepMs);
                    }
                    return "gido-smoke-jar event=" + value;
                })
                .returns(Types.STRING);

        lines.print().name("gido-smoke-print").setParallelism(1);
        env.execute("gido-smoke-jar");
    }

    private static long parseLongEnv(String key, long defaultValue) {
        String raw = System.getenv(key);
        if (raw == null || raw.isBlank()) {
            return defaultValue;
        }
        try {
            return Long.parseLong(raw.trim());
        } catch (NumberFormatException ex) {
            return defaultValue;
        }
    }
}
