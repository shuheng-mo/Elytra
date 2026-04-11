-- ClickHouse 电商数仓 DDL
--
-- 表名与 ecommerce_pg 保持一致，这样 overlay YAML 与 Agent 生成的 SQL 可以
-- 跨源复用。MergeTree 族的关键差异：
--   * 主键 ≠ 唯一约束，只是排序键（ORDER BY）
--   * Nullable(...) 会降低性能，能避就避
--   * LowCardinality(String) 给小基数字符串列（状态枚举、品类名）自动建字典索引
--   * SummingMergeTree 对相同 ORDER BY 的行自动按后续数值字段求和（异步合并，
--     查询时要么带 FINAL 要么主动 GROUP BY + sum）
--
-- 重启 compose 后 Docker 不会重跑本文件（只在 volume 第一次初始化时执行）；
-- 如需强制重建：docker compose down -v 再 up -d。

CREATE DATABASE IF NOT EXISTS elytra;

-- ============================================================
-- ODS Layer
-- ============================================================

CREATE TABLE IF NOT EXISTS elytra.ods_orders (
    order_id        UInt64,
    user_id         UInt64,
    product_id      UInt64,
    quantity        UInt32,
    unit_price      Float64,
    total_amount    Float64,
    order_status    LowCardinality(String),
    payment_method  LowCardinality(String),
    created_at      DateTime,
    updated_at      Nullable(DateTime)
) ENGINE = MergeTree()
ORDER BY (order_id)
COMMENT '原始订单表';

CREATE TABLE IF NOT EXISTS elytra.ods_users (
    user_id         UInt64,
    username        String,
    email           Nullable(String),
    phone           Nullable(String),
    gender          LowCardinality(String),
    birth_date      Nullable(Date),
    city            LowCardinality(String),
    province        LowCardinality(String),
    register_date   DateTime,
    user_level      LowCardinality(String)
) ENGINE = MergeTree()
ORDER BY (user_id)
COMMENT '原始用户表';

CREATE TABLE IF NOT EXISTS elytra.ods_products (
    product_id      UInt64,
    product_name    String,
    category_l1     LowCardinality(String),
    category_l2     LowCardinality(String),
    brand           LowCardinality(String),
    price           Float64,
    cost            Nullable(Float64),
    stock           Nullable(UInt32),
    status          LowCardinality(String),
    created_at      Nullable(DateTime)
) ENGINE = MergeTree()
ORDER BY (product_id)
COMMENT '原始商品表';

-- ============================================================
-- DWD Layer
-- ============================================================

CREATE TABLE IF NOT EXISTS elytra.dwd_order_detail (
    order_id        UInt64,
    user_id         UInt64,
    username        String,
    user_level      LowCardinality(String),
    user_city       LowCardinality(String),
    user_province   LowCardinality(String),
    product_id      UInt64,
    product_name    String,
    category_l1     LowCardinality(String),
    category_l2     LowCardinality(String),
    brand           LowCardinality(String),
    quantity        UInt32,
    unit_price      Float64,
    total_amount    Float64,
    cost_amount     Nullable(Float64),
    profit          Nullable(Float64),
    order_status    LowCardinality(String),
    payment_method  LowCardinality(String),
    order_date      Date,
    order_hour      UInt8,
    created_at      DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(order_date)
ORDER BY (order_date, user_id, order_id)
COMMENT '订单明细宽表（按月分区）';

-- ============================================================
-- DWS Layer
-- ============================================================

CREATE TABLE IF NOT EXISTS elytra.dws_daily_sales (
    stat_date        Date,
    category_l1      LowCardinality(String),
    order_count      UInt64,
    total_amount     Float64,
    total_profit     Float64,
    unique_buyers    UInt64,
    avg_order_amount Float64
) ENGINE = SummingMergeTree()
ORDER BY (stat_date, category_l1)
COMMENT '每日品类销售聚合';

CREATE TABLE IF NOT EXISTS elytra.dws_user_activity (
    stat_date     Date,
    user_level    LowCardinality(String),
    active_users  UInt64,
    new_users     UInt64,
    total_orders  UInt64,
    total_amount  Float64
) ENGINE = SummingMergeTree()
ORDER BY (stat_date, user_level)
COMMENT '用户活跃度聚合';
