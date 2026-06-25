/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
package com.gido.flink;

/** SqlRunner 命令行：脚本路径 + 可选 --preview [limit]。 */
final class PreviewOptions {

    final String scriptLocation;
    final boolean preview;
    final int limit;

    private PreviewOptions(String scriptLocation, boolean preview, int limit) {
        this.scriptLocation = scriptLocation;
        this.preview = preview;
        this.limit = limit;
    }

    static PreviewOptions parse(String[] args) {
        if (args.length < 1) {
            throw new IllegalArgumentException(
                    "须至少一个参数：SQL 脚本路径；预览模式追加 --preview [limit]");
        }
        String location = args[0].trim();
        boolean preview = false;
        int limit = 100;
        for (int i = 1; i < args.length; i++) {
            if ("--preview".equalsIgnoreCase(args[i])) {
                preview = true;
                if (i + 1 < args.length && args[i + 1].matches("\\d+")) {
                    limit = Integer.parseInt(args[++i]);
                }
            }
        }
        if (limit < 1) {
            limit = 1;
        }
        if (limit > 10000) {
            limit = 10000;
        }
        return new PreviewOptions(location, preview, limit);
    }
}
