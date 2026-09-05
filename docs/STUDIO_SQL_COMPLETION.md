# Studio / Probe SQL 库表补全 · 手测清单

对接 Doris（含跨库 `bigdata_ads` / `bigdata_dw`）时按下列项勾选。

## 前置

- [ ] 空间默认数据源或节点配置绑定 Doris FE（MySQL 协议）
- [ ] 账号能 `SHOW DATABASES` 并读 `information_schema`

## 补全

- [ ] 输入 `FROM big` → 出现 schema / 表建议
- [ ] 输入 `bigdata_ads.` → 仅该库下表名
- [ ] 输入 `bigdata_ads.ads_xxx.` → 列名 + 类型
- [ ] 裸表名补全插入为 `catalog.table`，可直接运行
- [ ] 未绑数据源时补全为空、不报错刷屏

## 库表抽屉

- [ ] 工具栏「库表」打开右侧抽屉
- [ ] 展开默认库见表；双击插入 `catalog.table`
- [ ] 展开表见列；双击插入列名

## Probe 对齐

- [ ] 数据探查同一数据源下补全体感与 Studio 一致
