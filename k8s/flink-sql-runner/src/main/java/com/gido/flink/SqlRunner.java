/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-10
 */
package com.gido.flink;

import org.apache.flink.configuration.Configuration;
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

        TableEnvironment tableEnv = TableEnvironment.create(new Configuration());
        for (String statement : statements) {
            Matcher setMatcher = SET_STATEMENT_PATTERN.matcher(statement.trim());
            if (setMatcher.matches()) {
                String key = setMatcher.group(1);
                String value = setMatcher.group(2);
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
