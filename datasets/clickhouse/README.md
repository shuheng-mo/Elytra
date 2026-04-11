# ClickHouse 数据源

Elytra 的第四个数据源。依赖 `ecommerce_pg` 已经灌好数据（PG 容器要在跑）——本脚本从 PG 读数据写入 ClickHouse。

## 启动步骤

```bash
# 1. 启动 ClickHouse 容器（首次自动执行 create_tables.sql）
docker compose -f docker/clickhouse/docker-compose.clickhouse.yml up -d

# 2. 等几秒让 CH ready，然后灌数据（从 PG 读 → CH 写）
.venv/bin/python datasets/clickhouse/load_data.py

# 3. 给 Elytra 的 retrieval 层建 embedding（必须带 --source 避免删其他源）
.venv/bin/python -m src.retrieval.bootstrap --source ecommerce_clickhouse

# 4. 冒烟验证
curl -s http://localhost:8123/?query=SELECT+count%28%29+FROM+elytra.dwd_order_detail
```

## 重置

`docker compose ... down -v` 销毁 `ch_data` volume，下次 `up -d` 会重新跑 init SQL。
仅删数据但保留 schema：`python datasets/clickhouse/load_data.py` 本身幂等（会先 TRUNCATE）。

## 表结构

与 `ecommerce_pg` 同名，但引擎层完全不同：

| 表 | 引擎 | 备注 |
|---|---|---|
| ods_orders / ods_users / ods_products | MergeTree | 按主键排序 |
| dwd_order_detail | MergeTree, PARTITION BY toYYYYMM(order_date) | 按月分区 |
| dws_daily_sales / dws_user_activity | SummingMergeTree | 查询时要么 GROUP BY + sum，要么带 FINAL |
