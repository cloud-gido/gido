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
import org.apache.flink.table.api.TableResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * GIDO SQL Runner：从挂载路径 / HTTP(S) / 对象存储读取脚本并执行。
 * 参数：args[0] = SQL 位置；可选 --preview [limit] 收集 batch SELECT 结果到 stdout。
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

    static boolean isSelectLike(String statement) {
        String trimmed = statement.trim();
        String upper = trimmed.toUpperCase();
        return upper.startsWith("SELECT") || upper.startsWith("WITH");
    }

    public static void main(String[] args) throws Exception {
        PreviewOptions preview = PreviewOptions.parse(args);
        String location = preview.scriptLocation;
        LOG.info("GIDO SqlRunner 加载脚本: {}{}", location, preview.preview ? " (preview)" : "");
        String script = SqlSourceResolver.readScript(location);
        List<String> statements = SqlStatementParser.parseStatements(script);
        if (statements.isEmpty()) {
            throw new IllegalStateException("SQL 脚本无有效语句: " + location);
        }

        Map<String, String> setValues = collectSetValues(statements);
        RuntimeExecutionMode runtimeMode = resolveRuntimeMode(statements);
        if (preview.preview && runtimeMode != RuntimeExecutionMode.BATCH) {
            throw new IllegalStateException("预览模式须 SET 'execution.runtime-mode' = 'batch'");
        }

        Map<String, String> hadoopFsProps = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : setValues.entrySet()) {
            if (entry.getKey().startsWith("fs.")) {
                hadoopFsProps.put(entry.getKey(), entry.getValue());
            }
        }
        HadoopFsConfigInstaller.install(hadoopFsProps);

        Configuration configuration = new Configuration();
        configuration.set(ExecutionOptions.RUNTIME_MODE, runtimeMode);
        for (Map.Entry<String, String> entry : setValues.entrySet()) {
            if (!RUNTIME_MODE_KEY.equals(entry.getKey())) {
                configuration.setString(entry.getKey(), entry.getValue());
            }
        }
        LOG.info("TableEnvironment runtime mode = {}", runtimeMode);
        TableEnvironment tableEnv = TableEnvironment.create(configuration);

        TableResult lastSelectResult = null;
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
                LOG.info("SET {} = {}", key, maskIfSensitive(key, value));
            } else if (preview.preview && isSelectLike(statement)) {
                LOG.info("Preview SELECT:\n{}", statement);
                lastSelectResult = tableEnv.executeSql(statement);
            } else {
                LOG.info("Executing:\n{}", statement);
                tableEnv.executeSql(statement);
            }
        }

        if (preview.preview) {
            if (lastSelectResult == null) {
                throw new IllegalStateException("预览模式需要至少一条 SELECT / WITH 语句");
            }
            PreviewJsonWriter.emit(lastSelectResult, preview.limit);
        }
        LOG.info("GIDO SqlRunner 完成，共 {} 条语句", statements.size());
    }

    static Map<String, String> collectSetValues(List<String> statements) {
        Map<String, String> values = new LinkedHashMap<>();
        for (String statement : statements) {
            Matcher matcher = SET_STATEMENT_PATTERN.matcher(statement.trim());
            if (matcher.matches()) {
                values.put(matcher.group(1), matcher.group(2));
            }
        }
        return values;
    }

    private static String maskIfSensitive(String key, String value) {
        String lower = key.toLowerCase();
        if (lower.contains("secret") || lower.contains("password") || lower.contains("token")) {
            return "******";
        }
        return value;
    }
}
