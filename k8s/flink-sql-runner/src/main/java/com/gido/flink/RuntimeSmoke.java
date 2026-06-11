package com.gido.flink;

import org.apache.hadoop.conf.Configuration;
import org.apache.paimon.catalog.CatalogContext;
import org.apache.paimon.options.Options;
import org.apache.paimon.utils.HadoopUtils;

/**
 * 镜像自检（CI verify-image.sh）：复现 SqlRunner CREATE CATALOG paimon 的 Hadoop 初始化路径。
 */
public final class RuntimeSmoke {

    private RuntimeSmoke() {}

    public static void main(String[] args) {
        Configuration conf = new Configuration();
        Options options = new Options();
        HadoopUtils.getHadoopConfiguration(options);
        CatalogContext.create(options, conf);
    }
}
