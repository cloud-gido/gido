/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
package com.gido.flink;

import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.fs.Path;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Map;

/**
 * 将 SQL {@code SET 'fs.*'} 注入 Hadoop 默认 Configuration，供 Paimon {@code HadoopFileIO} 访问 s3a://。
 * Flink TableConfig 中的 fs.* 不会自动进入 Hadoop Configuration。
 */
final class HadoopFsConfigInstaller {

    private static final Logger LOG = LoggerFactory.getLogger(HadoopFsConfigInstaller.class);

    private HadoopFsConfigInstaller() {}

    static void install(Map<String, String> fsProperties) throws IOException {
        if (fsProperties == null || fsProperties.isEmpty()) {
            return;
        }
        for (Map.Entry<String, String> entry : fsProperties.entrySet()) {
            System.setProperty(entry.getKey(), entry.getValue());
        }
        java.nio.file.Path dir = Files.createTempDirectory("gido-hadoop-conf");
        java.nio.file.Path coreSite = dir.resolve("core-site.xml");
        Files.writeString(coreSite, toCoreSiteXml(fsProperties), StandardCharsets.UTF_8);
        Configuration.addDefaultResource(new Path(coreSite.toUri()).toString());
        LOG.info("已注入 {} 条 Hadoop fs.* 配置（Paimon S3）", fsProperties.size());
    }

    private static String toCoreSiteXml(Map<String, String> props) {
        StringBuilder xml = new StringBuilder(
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<configuration>\n");
        for (Map.Entry<String, String> entry : props.entrySet()) {
            xml.append("  <property><name>")
                    .append(escapeXml(entry.getKey()))
                    .append("</name><value>")
                    .append(escapeXml(entry.getValue()))
                    .append("</value></property>\n");
        }
        xml.append("</configuration>\n");
        return xml.toString();
    }

    private static String escapeXml(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&apos;");
    }
}
