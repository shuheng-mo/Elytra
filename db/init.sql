-- Elytra Database Initialization
-- PostgreSQL 16 + pgvector

-- ============================================================
-- Extensions
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- ODS Layer (原始数据层)
-- ============================================================

CREATE TABLE ods_users (
    user_id         BIGINT PRIMARY KEY,
    username        VARCHAR(50) NOT NULL,
    email           VARCHAR(100),
    phone           VARCHAR(20),
    gender          VARCHAR(10),             -- male/female/unknown
    birth_date      DATE,
    city            VARCHAR(50),
    province        VARCHAR(50),
    register_date   TIMESTAMP NOT NULL,
    user_level      VARCHAR(20)              -- normal/silver/gold/platinum
);

CREATE TABLE ods_products (
    product_id      BIGINT PRIMARY KEY,
    product_name    VARCHAR(200) NOT NULL,
    category_l1     VARCHAR(50) NOT NULL,    -- 一级品类
    category_l2     VARCHAR(50),             -- 二级品类
    brand           VARCHAR(100),
    price           DECIMAL(10,2) NOT NULL,
    cost            DECIMAL(10,2),
    stock           INT,
    status          VARCHAR(20),             -- active/inactive/discontinued
    created_at      TIMESTAMP
);

CREATE TABLE ods_orders (
    order_id        BIGINT PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    product_id      BIGINT NOT NULL,
    quantity         INT NOT NULL,
    unit_price      DECIMAL(10,2) NOT NULL,
    total_amount    DECIMAL(12,2) NOT NULL,
    order_status    VARCHAR(20) NOT NULL,    -- pending/paid/shipped/completed/cancelled
    payment_method  VARCHAR(20),             -- alipay/wechat/card/cash
    created_at      TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP
);

CREATE TABLE ods_payments (
    payment_id      BIGINT PRIMARY KEY,
    order_id        BIGINT NOT NULL,
    amount          DECIMAL(12,2) NOT NULL,
    payment_method  VARCHAR(20) NOT NULL,
    payment_status  VARCHAR(20) NOT NULL,    -- success/failed/refunded
    paid_at         TIMESTAMP
);

CREATE TABLE ods_user_behavior (
    log_id          BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    product_id      BIGINT,
    behavior_type   VARCHAR(20) NOT NULL,    -- view/cart/favorite/purchase/search
    search_keyword  VARCHAR(200),
    page_name       VARCHAR(50),
    event_time      TIMESTAMP NOT NULL
);

-- ============================================================
-- DWD Layer (清洗明细层)
-- ============================================================

CREATE TABLE dwd_order_detail (
    order_id        BIGINT PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    username        VARCHAR(50),
    user_level      VARCHAR(20),
    user_city       VARCHAR(50),
    user_province   VARCHAR(50),
    product_id      BIGINT NOT NULL,
    product_name    VARCHAR(200),
    category_l1     VARCHAR(50),
    category_l2     VARCHAR(50),
    brand           VARCHAR(100),
    quantity         INT,
    unit_price      DECIMAL(10,2),
    total_amount    DECIMAL(12,2),
    cost_amount     DECIMAL(12,2),           -- quantity * cost
    profit          DECIMAL(12,2),           -- total_amount - cost_amount
    order_status    VARCHAR(20),
    payment_method  VARCHAR(20),
    payment_status  VARCHAR(20),
    order_date      DATE,
    order_hour      INT,
    paid_at         TIMESTAMP,
    created_at      TIMESTAMP
);

CREATE TABLE dwd_user_profile (
    user_id             BIGINT PRIMARY KEY,
    username            VARCHAR(50),
    gender              VARCHAR(10),
    age_group           VARCHAR(20),         -- 18-24/25-34/35-44/45+
    city                VARCHAR(50),
    province            VARCHAR(50),
    user_level          VARCHAR(20),
    register_date       DATE,
    days_since_register INT,
    total_orders        INT,
    total_spent         DECIMAL(14,2),
    avg_order_amount    DECIMAL(10,2),
    favorite_category   VARCHAR(50),         -- 最常购买的品类
    last_order_date     DATE,
    is_active           BOOLEAN              -- 30天内有下单
);

CREATE TABLE dwd_product_dim (
    product_id      BIGINT PRIMARY KEY,
    product_name    VARCHAR(200),
    category_l1     VARCHAR(50),
    category_l2     VARCHAR(50),
    brand           VARCHAR(100),
    price           DECIMAL(10,2),
    cost            DECIMAL(10,2),
    margin_rate     DECIMAL(5,4),            -- (price - cost) / price
    status          VARCHAR(20),
    total_sold      INT,                     -- 历史总销量
    total_revenue   DECIMAL(14,2)            -- 历史总收入
);

-- ============================================================
-- DWS Layer (聚合统计层)
-- ============================================================

CREATE TABLE dws_daily_sales (
    stat_date       DATE NOT NULL,
    category_l1     VARCHAR(50) NOT NULL,
    order_count     INT,
    total_amount    DECIMAL(14,2),
    total_profit    DECIMAL(14,2),
    unique_buyers   INT,
    avg_order_amount DECIMAL(10,2),
    PRIMARY KEY (stat_date, category_l1)
);

CREATE TABLE dws_user_activity (
    stat_date       DATE NOT NULL,
    user_level      VARCHAR(20) NOT NULL,
    active_users    INT,                     -- 当日下单用户数
    new_users       INT,                     -- 当日注册用户数
    total_orders    INT,
    total_amount    DECIMAL(14,2),
    PRIMARY KEY (stat_date, user_level)
);

CREATE TABLE dws_product_ranking (
    week_start      DATE NOT NULL,
    product_id      BIGINT NOT NULL,
    product_name    VARCHAR(200),
    category_l1     VARCHAR(50),
    sold_quantity   INT,
    revenue         DECIMAL(14,2),
    rank_by_revenue INT,
    rank_by_quantity INT,
    PRIMARY KEY (week_start, product_id)
);

-- ============================================================
-- System Tables (系统表)
-- ============================================================

CREATE TABLE query_history (
    id              BIGSERIAL PRIMARY KEY,
    session_id      VARCHAR(50),
    user_query      TEXT NOT NULL,
    intent          VARCHAR(30),
    generated_sql   TEXT,
    execution_success BOOLEAN,
    retry_count     INT DEFAULT 0,
    model_used      VARCHAR(50),
    latency_ms      INT,
    token_count     INT,
    estimated_cost  DECIMAL(8,6),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE schema_embeddings (
    id              BIGSERIAL PRIMARY KEY,
    source_name     VARCHAR(100) NOT NULL,    -- 数据源名称（对应 datasources.yaml）
    table_name      VARCHAR(100) NOT NULL,
    column_name     VARCHAR(100),             -- NULL 表示表级别描述
    description     TEXT NOT NULL,            -- 拼接的文本描述
    embedding       vector(1536) NOT NULL,    -- pgvector 向量字段
    metadata        JSONB                     -- 额外信息（层级、数据类型等）
);

CREATE INDEX ON schema_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX schema_embeddings_source_idx ON schema_embeddings (source_name);
