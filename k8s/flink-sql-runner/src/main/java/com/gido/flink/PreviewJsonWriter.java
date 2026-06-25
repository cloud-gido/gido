/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
package com.gido.flink;

import org.apache.flink.table.api.TableResult;
import org.apache.flink.table.catalog.ResolvedSchema;
import org.apache.flink.table.types.DataType;
import org.apache.flink.types.Row;
import org.apache.flink.util.CloseableIterator;

import java.time.temporal.Temporal;
import java.util.ArrayList;
import java.util.List;

/** 将 batch SELECT 结果序列化为 GIDO 后端可解析的 JSON 行。 */
final class PreviewJsonWriter {

    static final String MARKER = "GIDO_PREVIEW_JSON:";

    private PreviewJsonWriter() {}

    static void emit(TableResult result, int limit) throws Exception {
        ResolvedSchema schema = result.getResolvedSchema();
        List<String> columns = schema.getColumnNames();
        List<String> columnTypes = new ArrayList<>();
        for (DataType dt : schema.getColumnDataTypes()) {
            columnTypes.add(dt.getLogicalType().asSummaryString());
        }

        List<List<Object>> rows = new ArrayList<>();
        int total = 0;
        boolean truncated = false;
        try (CloseableIterator<Row> it = result.collect()) {
            while (it.hasNext()) {
                Row row = it.next();
                total++;
                if (rows.size() < limit) {
                    rows.add(rowToList(row, columns.size()));
                } else {
                    truncated = true;
                }
            }
        }

        StringBuilder sb = new StringBuilder(MARKER);
        sb.append('{');
        appendStringArray(sb, "columns", columns);
        sb.append(',');
        appendStringArray(sb, "column_types", columnTypes);
        sb.append(',');
        sb.append("\"rows\":");
        appendRows(sb, rows);
        sb.append(',');
        sb.append("\"total\":").append(total);
        sb.append(',');
        sb.append("\"truncated\":").append(truncated);
        sb.append('}');
        System.out.println(sb);
        System.out.flush();
    }

    private static List<Object> rowToList(Row row, int n) {
        List<Object> out = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            out.add(jsonValue(row.getField(i)));
        }
        return out;
    }

    private static Object jsonValue(Object v) {
        if (v == null) {
            return null;
        }
        if (v instanceof String || v instanceof Number || v instanceof Boolean) {
            return v;
        }
        if (v instanceof Temporal) {
            return v.toString();
        }
        if (v instanceof byte[]) {
            return new String((byte[]) v, java.nio.charset.StandardCharsets.UTF_8);
        }
        return String.valueOf(v);
    }

    private static void appendStringArray(StringBuilder sb, String key, List<String> values) {
        sb.append('"').append(key).append("\":[");
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append('"').append(escapeJson(values.get(i))).append('"');
        }
        sb.append(']');
    }

    private static void appendRows(StringBuilder sb, List<List<Object>> rows) {
        sb.append('[');
        for (int r = 0; r < rows.size(); r++) {
            if (r > 0) {
                sb.append(',');
            }
            sb.append('[');
            List<Object> row = rows.get(r);
            for (int c = 0; c < row.size(); c++) {
                if (c > 0) {
                    sb.append(',');
                }
                appendJsonValue(sb, row.get(c));
            }
            sb.append(']');
        }
        sb.append(']');
    }

    private static void appendJsonValue(StringBuilder sb, Object v) {
        if (v == null) {
            sb.append("null");
        } else if (v instanceof Number || v instanceof Boolean) {
            sb.append(v);
        } else {
            sb.append('"').append(escapeJson(String.valueOf(v))).append('"');
        }
    }

    private static String escapeJson(String s) {
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}
