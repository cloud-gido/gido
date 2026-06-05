package com.gido.mini;

import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.source.legacy.RichParallelSourceFunction;
import org.apache.flink.streaming.api.functions.source.legacy.SourceFunction;

/**
 * 不依赖 Kafka / JDBC / 文件等：子任务内自增序列 + {@code print()} 打到 TaskManager 日志。
 * 与 K8s {@code apache/flink:2.0.1-java11} 一致；打包为薄 JAR（Flink 由集群提供）。
 */
public final class SelfContainedStreamingJob {

    public static void main(String[] args) throws Exception {
        int parallelism = 1;
        if (args != null && args.length > 0) {
            parallelism = Math.max(1, Integer.parseInt(args[0].trim()));
        }
        long intervalMs = 300L;
        if (args != null && args.length > 1) {
            intervalMs = Math.max(50L, Long.parseLong(args[1].trim()));
        }

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(parallelism);

        DataStream<Long> ticks = env.addSource(new TickSource(intervalMs)).name("tick-source");

        ticks.map(
                        new MapFunction<Long, String>() {
                            @Override
                            public String map(Long value) {
                                return "[mini-demo] seq=" + value;
                            }
                        })
                .name("to-string")
                .print();

        env.execute("self-contained-mini-demo");
    }

    private static final class TickSource extends RichParallelSourceFunction<Long> {
        private static final long serialVersionUID = 1L;

        private final long intervalMs;
        private volatile boolean running = true;

        TickSource(long intervalMs) {
            this.intervalMs = intervalMs;
        }

        @Override
        public void run(SourceFunction.SourceContext<Long> ctx) throws Exception {
            long i = 0;
            while (running) {
                synchronized (ctx.getCheckpointLock()) {
                    ctx.collect(++i);
                }
                Thread.sleep(intervalMs);
            }
        }

        @Override
        public void cancel() {
            running = false;
        }
    }
}
