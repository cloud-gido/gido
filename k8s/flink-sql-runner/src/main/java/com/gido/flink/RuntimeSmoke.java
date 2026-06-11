package com.gido.flink;

import java.util.HashMap;

import org.apache.hadoop.conf.Configuration;
import org.apache.paimon.catalog.CatalogContext;

/**
 * 镜像自检入口（CI verify-image.sh）：与 SqlRunner CREATE CATALOG 相同的类加载路径。
 * 不打包 Hadoop/Paimon 依赖，运行时由 /opt/flink/lib/*.jar 提供。
 */
public final class RuntimeSmoke {

    private RuntimeSmoke() {}

    public static void main(String[] args) {
        new Configuration();
        CatalogContext.create(new Configuration(), new HashMap<>());
    }
}
