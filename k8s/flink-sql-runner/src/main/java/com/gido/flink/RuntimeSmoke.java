/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
package com.gido.flink;

import org.apache.hadoop.conf.Configuration;
import org.apache.paimon.catalog.CatalogContext;
import org.apache.paimon.options.Options;
import org.apache.paimon.utils.HadoopUtils;

/**
 * 镜像自检（CI verify-image.sh）：复现 Paimon S3 catalog 与 Parquet 写路径的 Hadoop 依赖。
 */
public final class RuntimeSmoke {

    private RuntimeSmoke() {}

    public static void main(String[] args) throws Exception {
        Configuration conf = new Configuration();
        Options options = new Options();
        HadoopUtils.getHadoopConfiguration(options);
        CatalogContext.create(options, conf);

        // Paimon 写 Parquet 统计信息时 ParquetReadOptions 依赖 FileInputFormat
        Class.forName("org.apache.hadoop.mapreduce.lib.input.FileInputFormat");
        // Paimon S3 catalog 走 HadoopFileIO，S3A 必须在主 classpath（非 s3-fs-hadoop 插件）
        Class.forName("org.apache.hadoop.fs.s3a.S3AFileSystem");
        org.apache.paimon.shade.org.apache.parquet.ParquetReadOptions.builder();
    }
}
