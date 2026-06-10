/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-10
 */
package com.gido.flink;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/** 解析 SQL 脚本为可执行语句列表（支持 SET、EXECUTE STATEMENT SET）。 */
public final class SqlStatementParser {

    private static final String STATEMENT_DELIMITER = ";";
    private static final String COMMENT_PATTERN = "(--.*)|(((\\/\\*)+?[\\w\\W]+?(\\*\\/)+))";
    private static final String BEGIN_CERTIFICATE = "-----BEGIN CERTIFICATE-----";
    private static final String END_CERTIFICATE = "-----END CERTIFICATE-----";
    private static final String ESCAPED_BEGIN_CERTIFICATE = "======BEGIN CERTIFICATE=====";
    private static final String ESCAPED_END_CERTIFICATE = "=====END CERTIFICATE=====";

    private SqlStatementParser() {}

    public static List<String> parseStatements(String script) {
        String formatted =
                formatSqlFile(script)
                        .replaceAll(BEGIN_CERTIFICATE, ESCAPED_BEGIN_CERTIFICATE)
                        .replaceAll(END_CERTIFICATE, ESCAPED_END_CERTIFICATE)
                        .replaceAll(COMMENT_PATTERN, "")
                        .replaceAll(ESCAPED_BEGIN_CERTIFICATE, BEGIN_CERTIFICATE)
                        .replaceAll(ESCAPED_END_CERTIFICATE, END_CERTIFICATE);

        List<String> statements = new ArrayList<>();
        StringBuilder current = null;
        boolean statementSet = false;
        for (String line : formatted.split("\n")) {
            String trimmed = line.trim();
            if (trimmed.isBlank()) {
                continue;
            }
            if (current == null) {
                current = new StringBuilder();
            }
            if (trimmed.startsWith("EXECUTE STATEMENT SET")) {
                statementSet = true;
            }
            current.append(trimmed);
            current.append("\n");
            if (trimmed.endsWith(STATEMENT_DELIMITER)) {
                if (!statementSet || trimmed.equals("END;")) {
                    statements.add(current.toString());
                    current = null;
                    statementSet = false;
                }
            }
        }
        return statements;
    }

    public static String formatSqlFile(String content) {
        String trimmed = content.trim();
        StringBuilder formatted = new StringBuilder();
        formatted.append(trimmed);
        if (!trimmed.endsWith(STATEMENT_DELIMITER)) {
            formatted.append(STATEMENT_DELIMITER);
        }
        formatted.append("\n");
        return formatted.toString();
    }
}
