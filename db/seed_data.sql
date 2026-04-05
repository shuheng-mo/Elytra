-- Elytra Seed Data
-- 使用 PostgreSQL generate_series + random 生成模拟数据

-- ============================================================
-- Helper: 随机选取数组元素
-- ============================================================
CREATE OR REPLACE FUNCTION random_element(arr TEXT[])
RETURNS TEXT AS $$
BEGIN
    RETURN arr[1 + floor(random() * array_length(arr, 1))::int];
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- ODS: ods_users (1000 用户)
-- ============================================================
INSERT INTO ods_users (user_id, username, email, phone, gender, birth_date, city, province, register_date, user_level)
SELECT
    s AS user_id,
    'user_' || s AS username,
    'user_' || s || '@example.com' AS email,
    '138' || lpad((random() * 99999999)::bigint::text, 8, '0') AS phone,
    random_element(ARRAY['male', 'female', 'unknown']) AS gender,
    '1975-01-01'::date + (random() * 17000)::int AS birth_date,
    random_element(ARRAY[
        '北京', '上海', '广州', '深圳', '杭州', '成都', '武汉', '南京',
        '重庆', '西安', '苏州', '天津', '长沙', '郑州', '东莞'
    ]) AS city,
    random_element(ARRAY[
        '北京', '上海', '广东', '广东', '浙江', '四川', '湖北', '江苏',
        '重庆', '陕西', '江苏', '天津', '湖南', '河南', '广东'
    ]) AS province,
    '2024-01-01'::timestamp + (random() * 450)::int * INTERVAL '1 day'
        + (random() * 86400)::int * INTERVAL '1 second' AS register_date,
    random_element(ARRAY['normal', 'normal', 'normal', 'silver', 'silver', 'gold', 'platinum']) AS user_level
FROM generate_series(1, 1000) AS s;

-- ============================================================
-- ODS: ods_products (200 商品)
-- ============================================================
INSERT INTO ods_products (product_id, product_name, category_l1, category_l2, brand, price, cost, stock, status, created_at)
SELECT
    s AS product_id,
    (
        CASE (s % 6)
            WHEN 0 THEN random_element(ARRAY['智能手机', '蓝牙耳机', '平板电脑', '笔记本电脑', '智能手表', '移动电源'])
            WHEN 1 THEN random_element(ARRAY['T恤', '牛仔裤', '连衣裙', '运动鞋', '羽绒服', '卫衣'])
            WHEN 2 THEN random_element(ARRAY['面霜', '洗面奶', '口红', '香水', '面膜', '防晒霜'])
            WHEN 3 THEN random_element(ARRAY['零食大礼包', '坚果混合装', '进口牛奶', '有机茶叶', '咖啡豆', '巧克力礼盒'])
            WHEN 4 THEN random_element(ARRAY['Python编程', '数据结构', '机器学习实战', '设计模式', '算法导论', '深度学习'])
            ELSE random_element(ARRAY['台灯', '收纳箱', '保温杯', '抱枕', '空气净化器', '加湿器'])
        END
    ) || ' ' || s::text AS product_name,
    (ARRAY['电子产品', '服装', '美妆', '食品', '图书', '家居'])[s % 6 + 1] AS category_l1,
    (
        CASE (s % 6)
            WHEN 0 THEN random_element(ARRAY['手机', '配件', '平板', '电脑', '穿戴', '充电'])
            WHEN 1 THEN random_element(ARRAY['上衣', '裤装', '裙装', '鞋靴', '外套', '卫衣'])
            WHEN 2 THEN random_element(ARRAY['护肤', '洁面', '彩妆', '香氛', '面膜', '防晒'])
            WHEN 3 THEN random_element(ARRAY['零食', '坚果', '乳制品', '茶饮', '咖啡', '糖果'])
            WHEN 4 THEN random_element(ARRAY['编程', '计算机', 'AI', '软件工程', '算法', 'AI'])
            ELSE random_element(ARRAY['照明', '收纳', '杯壶', '纺织', '电器', '电器'])
        END
    ) AS category_l2,
    random_element(ARRAY[
        '华为', '小米', '苹果', '三星', 'OPPO', '耐克', '阿迪达斯', '优衣库',
        '欧莱雅', '兰蔻', '三只松鼠', '良品铺子', '人民邮电', '机械工业', '宜家', '无印良品'
    ]) AS brand,
    round((50 + random() * 4950)::numeric, 2) AS price,
    round((30 + random() * 2950)::numeric, 2) AS cost,
    (50 + random() * 950)::int AS stock,
    random_element(ARRAY['active', 'active', 'active', 'active', 'inactive', 'discontinued']) AS status,
    '2023-06-01'::timestamp + (random() * 300)::int * INTERVAL '1 day' AS created_at
FROM generate_series(1, 200) AS s;

-- ============================================================
-- ODS: ods_orders (5000 订单)
-- ============================================================
INSERT INTO ods_orders (order_id, user_id, product_id, quantity, unit_price, total_amount, order_status, payment_method, created_at, updated_at)
SELECT
    s AS order_id,
    (1 + floor(random() * 1000))::bigint AS user_id,
    (1 + floor(random() * 200))::bigint AS product_id,
    (1 + floor(random() * 5))::int AS quantity,
    round((50 + random() * 2000)::numeric, 2) AS unit_price,
    0 AS total_amount, -- 后面用 UPDATE 修正
    random_element(ARRAY['pending', 'paid', 'paid', 'shipped', 'completed', 'completed', 'completed', 'cancelled']) AS order_status,
    random_element(ARRAY['alipay', 'alipay', 'wechat', 'wechat', 'card', 'cash']) AS payment_method,
    '2025-10-01'::timestamp
        + (random() * 180)::int * INTERVAL '1 day'
        + (random() * 86400)::int * INTERVAL '1 second' AS created_at,
    NULL AS updated_at
FROM generate_series(1, 5000) AS s;

-- 修正 total_amount = unit_price * quantity
UPDATE ods_orders SET total_amount = round(unit_price * quantity, 2);

-- 修正 updated_at（非 pending 订单有更新时间）
UPDATE ods_orders
SET updated_at = created_at + (random() * 72)::int * INTERVAL '1 hour'
WHERE order_status != 'pending';

-- ============================================================
-- ODS: ods_payments (为非 cancelled/pending 订单生成支付记录)
-- ============================================================
INSERT INTO ods_payments (payment_id, order_id, amount, payment_method, payment_status, paid_at)
SELECT
    row_number() OVER (ORDER BY o.order_id) AS payment_id,
    o.order_id,
    o.total_amount AS amount,
    o.payment_method,
    CASE
        WHEN o.order_status = 'cancelled' THEN 'refunded'
        WHEN random() < 0.03 THEN 'failed'
        ELSE 'success'
    END AS payment_status,
    o.created_at + (random() * 30)::int * INTERVAL '1 minute' AS paid_at
FROM ods_orders o
WHERE o.order_status NOT IN ('pending');

-- ============================================================
-- ODS: ods_user_behavior (10000 行为日志)
-- ============================================================
INSERT INTO ods_user_behavior (user_id, product_id, behavior_type, search_keyword, page_name, event_time)
SELECT
    (1 + floor(random() * 1000))::bigint AS user_id,
    CASE WHEN random() < 0.15 THEN NULL ELSE (1 + floor(random() * 200))::bigint END AS product_id,
    random_element(ARRAY['view', 'view', 'view', 'cart', 'favorite', 'purchase', 'search']) AS behavior_type,
    CASE
        WHEN random() < 0.15 THEN random_element(ARRAY['手机', '连衣裙', '面膜', '零食', '笔记本', '耳机', '口红', '运动鞋'])
        ELSE NULL
    END AS search_keyword,
    random_element(ARRAY['home', 'category', 'product_detail', 'cart', 'search_result', 'order_confirm']) AS page_name,
    '2025-10-01'::timestamp
        + (random() * 180)::int * INTERVAL '1 day'
        + (random() * 86400)::int * INTERVAL '1 second' AS event_time
FROM generate_series(1, 10000) AS s;

-- ============================================================
-- DWD: dwd_order_detail (从 ODS 层关联清洗)
-- ============================================================
INSERT INTO dwd_order_detail (
    order_id, user_id, username, user_level, user_city, user_province,
    product_id, product_name, category_l1, category_l2, brand,
    quantity, unit_price, total_amount, cost_amount, profit,
    order_status, payment_method, payment_status,
    order_date, order_hour, paid_at, created_at
)
SELECT
    o.order_id,
    o.user_id,
    u.username,
    u.user_level,
    u.city AS user_city,
    u.province AS user_province,
    o.product_id,
    p.product_name,
    p.category_l1,
    p.category_l2,
    p.brand,
    o.quantity,
    o.unit_price,
    o.total_amount,
    round(p.cost * o.quantity, 2) AS cost_amount,
    round(o.total_amount - p.cost * o.quantity, 2) AS profit,
    o.order_status,
    o.payment_method,
    COALESCE(pay.payment_status, 'pending') AS payment_status,
    o.created_at::date AS order_date,
    EXTRACT(HOUR FROM o.created_at)::int AS order_hour,
    pay.paid_at,
    o.created_at
FROM ods_orders o
JOIN ods_users u ON o.user_id = u.user_id
JOIN ods_products p ON o.product_id = p.product_id
LEFT JOIN ods_payments pay ON o.order_id = pay.order_id;

-- ============================================================
-- DWD: dwd_user_profile (用户画像)
-- ============================================================
INSERT INTO dwd_user_profile (
    user_id, username, gender, age_group, city, province, user_level,
    register_date, days_since_register, total_orders, total_spent,
    avg_order_amount, favorite_category, last_order_date, is_active
)
SELECT
    u.user_id,
    u.username,
    u.gender,
    CASE
        WHEN EXTRACT(YEAR FROM AGE(u.birth_date)) < 25 THEN '18-24'
        WHEN EXTRACT(YEAR FROM AGE(u.birth_date)) < 35 THEN '25-34'
        WHEN EXTRACT(YEAR FROM AGE(u.birth_date)) < 45 THEN '35-44'
        ELSE '45+'
    END AS age_group,
    u.city,
    u.province,
    u.user_level,
    u.register_date::date,
    (CURRENT_DATE - u.register_date::date) AS days_since_register,
    COALESCE(os.total_orders, 0) AS total_orders,
    COALESCE(os.total_spent, 0) AS total_spent,
    COALESCE(os.avg_order_amount, 0) AS avg_order_amount,
    os.favorite_category,
    os.last_order_date,
    COALESCE(os.last_order_date >= CURRENT_DATE - 30, false) AS is_active
FROM ods_users u
LEFT JOIN (
    SELECT
        d.user_id,
        COUNT(*) AS total_orders,
        SUM(d.total_amount) AS total_spent,
        round(AVG(d.total_amount), 2) AS avg_order_amount,
        (ARRAY_AGG(d.category_l1 ORDER BY cnt DESC))[1] AS favorite_category,
        MAX(d.order_date) AS last_order_date
    FROM dwd_order_detail d
    JOIN (
        SELECT user_id, category_l1, COUNT(*) AS cnt
        FROM dwd_order_detail
        GROUP BY user_id, category_l1
    ) cat ON d.user_id = cat.user_id
    WHERE d.order_status != 'cancelled'
    GROUP BY d.user_id
) os ON u.user_id = os.user_id;

-- ============================================================
-- DWD: dwd_product_dim (商品维度)
-- ============================================================
INSERT INTO dwd_product_dim (
    product_id, product_name, category_l1, category_l2, brand,
    price, cost, margin_rate, status, total_sold, total_revenue
)
SELECT
    p.product_id,
    p.product_name,
    p.category_l1,
    p.category_l2,
    p.brand,
    p.price,
    p.cost,
    CASE WHEN p.price > 0 THEN round((p.price - p.cost) / p.price, 4) ELSE 0 END AS margin_rate,
    p.status,
    COALESCE(os.total_sold, 0) AS total_sold,
    COALESCE(os.total_revenue, 0) AS total_revenue
FROM ods_products p
LEFT JOIN (
    SELECT
        product_id,
        SUM(quantity) AS total_sold,
        SUM(total_amount) AS total_revenue
    FROM dwd_order_detail
    WHERE order_status != 'cancelled'
    GROUP BY product_id
) os ON p.product_id = os.product_id;

-- ============================================================
-- DWS: dws_daily_sales (每日销售聚合)
-- ============================================================
INSERT INTO dws_daily_sales (stat_date, category_l1, order_count, total_amount, total_profit, unique_buyers, avg_order_amount)
SELECT
    order_date AS stat_date,
    category_l1,
    COUNT(*) AS order_count,
    SUM(total_amount) AS total_amount,
    SUM(profit) AS total_profit,
    COUNT(DISTINCT user_id) AS unique_buyers,
    round(AVG(total_amount), 2) AS avg_order_amount
FROM dwd_order_detail
WHERE order_status != 'cancelled'
GROUP BY order_date, category_l1;

-- ============================================================
-- DWS: dws_user_activity (用户活跃度聚合)
-- ============================================================
INSERT INTO dws_user_activity (stat_date, user_level, active_users, new_users, total_orders, total_amount)
SELECT
    d.order_date AS stat_date,
    d.user_level,
    COUNT(DISTINCT d.user_id) AS active_users,
    COUNT(DISTINCT CASE WHEN u.register_date::date = d.order_date THEN u.user_id END) AS new_users,
    COUNT(*) AS total_orders,
    SUM(d.total_amount) AS total_amount
FROM dwd_order_detail d
JOIN ods_users u ON d.user_id = u.user_id
WHERE d.order_status != 'cancelled'
GROUP BY d.order_date, d.user_level;

-- ============================================================
-- DWS: dws_product_ranking (商品排行 - 周维度)
-- ============================================================
INSERT INTO dws_product_ranking (week_start, product_id, product_name, category_l1, sold_quantity, revenue, rank_by_revenue, rank_by_quantity)
SELECT
    week_start,
    product_id,
    product_name,
    category_l1,
    sold_quantity,
    revenue,
    RANK() OVER (PARTITION BY week_start ORDER BY revenue DESC) AS rank_by_revenue,
    RANK() OVER (PARTITION BY week_start ORDER BY sold_quantity DESC) AS rank_by_quantity
FROM (
    SELECT
        DATE_TRUNC('week', order_date)::date AS week_start,
        d.product_id,
        d.product_name,
        d.category_l1,
        SUM(d.quantity) AS sold_quantity,
        SUM(d.total_amount) AS revenue
    FROM dwd_order_detail d
    WHERE d.order_status != 'cancelled'
    GROUP BY DATE_TRUNC('week', order_date)::date, d.product_id, d.product_name, d.category_l1
) sub;

-- ============================================================
-- Cleanup helper function
-- ============================================================
DROP FUNCTION IF EXISTS random_element(TEXT[]);
