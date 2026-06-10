/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-10
 */
package com.gido.flink;

import org.apache.flink.core.fs.FileSystem;
import org.apache.flink.core.fs.Path;
import org.apache.flink.util.FileUtils;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.stream.Collectors;

/**
 * 从多种来源加载 SQL 脚本：本地挂载路径、HTTP(S)、Flink 文件系统（如 s3://）。
 */
public final class SqlSourceResolver {

    private static final int HTTP_CONNECT_TIMEOUT_MS = 30_000;
    private static final int HTTP_READ_TIMEOUT_MS = 120_000;

    private SqlSourceResolver() {}

    public static String readScript(String location) throws Exception {
        String loc = (location == null ? "" : location).trim();
        if (loc.isEmpty()) {
            throw new IllegalArgumentException("SQL 脚本位置为空");
        }
        String lower = loc.toLowerCase();
        if (lower.startsWith("http://") || lower.startsWith("https://")) {
            return readHttp(loc);
        }
        if (lower.startsWith("s3://") || lower.startsWith("hdfs://") || lower.contains("://")) {
            return readFlinkFilesystem(loc);
        }
        return readLocalFile(loc);
    }

    private static String readLocalFile(String path) throws Exception {
        File file = new File(path);
        if (!file.isFile()) {
            throw new IllegalArgumentException("本地 SQL 文件不存在: " + path);
        }
        return FileUtils.readFileUtf8(file);
    }

    private static String readHttp(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(HTTP_CONNECT_TIMEOUT_MS);
        conn.setReadTimeout(HTTP_READ_TIMEOUT_MS);
        conn.setRequestMethod("GET");
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP 拉取 SQL 失败 HTTP " + code + ": " + url);
        }
        try (InputStream in = conn.getInputStream();
                BufferedReader reader =
                        new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            return reader.lines().collect(Collectors.joining("\n"));
        } finally {
            conn.disconnect();
        }
    }

    private static String readFlinkFilesystem(String uri) throws Exception {
        Path flinkPath = new Path(uri);
        FileSystem fs = FileSystem.get(flinkPath.toUri());
        try (InputStream in = fs.open(flinkPath)) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }
}
