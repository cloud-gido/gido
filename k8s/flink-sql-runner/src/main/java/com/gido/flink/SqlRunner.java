/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-10
 */
package com.gido.flink;

import org.apache.flink.api.common.RuntimeExecutionMode;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.configuration.ExecutionOptions;
import org.apache.flink.table.api.TableEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * GIDO SQL Runner：从挂载路径 / HTTP(S) / 对象存储读取脚本并执行。
 * 参数：args[0] = SQL 位置（文件路径或 URI）。
 */
public class SqlRunner {

    private static final Logger LOG = LoggerFactory.getLogger(SqlRunner.class);

    private static final Pattern SET_STATEMENT_PATTERN =
            Pattern.compile("SET\\s+'(\\S+)'\\s+=\\s+'(.*)';", Pattern.CASE_INSENSITIVE);

    private static final String RUNTIME_MODE_KEY = "execution.runtime-mode";

    static RuntimeExecutionMode resolveRuntimeMode(List<String> statements) {
        for (String statement : statements) {
            Matcher matcher = SET_STATEMENT_PATTERN.matcher(statement.trim());
            if (matcher.matches() && RUNTIME_MODE_KEY.equals(matcher.group(1))) {
                String value = matcher.group(2).trim();
                if ("batch".equalsIgnoreCase(value)) {
                    return RuntimeExecutionMode.BATCH;
                }
                if ("streaming".equalsIgnoreCase(value)) {
                    return RuntimeExecutionMode.STREAMING;
                }
                throw new IllegalArgumentException(
                        "不支持的 execution.runtime-mode: " + value + "（仅 batch / streaming）");
            }
        }
        return RuntimeExecutionMode.STREAMING;
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException(
                    "须且仅需一个参数：SQL 脚本路径或 URI（file / http(s) / s3 等）");
        }
        String location = args[0].trim();
        LOG.info("GIDO SqlRunner 加载脚本: {}", location);
        String script = SqlSourceResolver.readScript(location);
        List<String> statements = SqlStatementParser.parseStatements(script);
        if (statements.isEmpty()) {
            throw new IllegalStateException("SQL 脚本无有效语句: " + location);
        }

        RuntimeExecutionMode runtimeMode = resolveRuntimeMode(statements);
        Configuration configuration = new Configuration();
        configuration.set(ExecutionOptions.RUNTIME_MODE, runtimeMode);
        LOG.info("TableEnvironment runtime mode = {}", runtimeMode);
        TableEnvironment tableEnv = TableEnvironment.create(configuration);
        for (String statement : statements) {
            Matcher setMatcher = SET_STATEMENT_PATTERN.matcher(statement.trim());
            if (setMatcher.matches()) {
                String key = setMatcher.group(1);
                String value = setMatcher.group(2);
                if (RUNTIME_MODE_KEY.equals(key)) {
                    LOG.info("SET {} = {}（已在创建 TableEnvironment 时应用）", key, value);
                    continue;
                }
                tableEnv.getConfig().getConfiguration().setString(key, value);
                LOG.info("SET {} = {}", key, value);
            } else {
                LOG.info("Executing:\n{}", statement);
                tableEnv.executeSql(statement);
            }
        }
        LOG.info("GIDO SqlRunner 完成，共 {} 条语句", statements.size());
    }
}
