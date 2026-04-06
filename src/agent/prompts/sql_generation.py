"""SQL generation prompts, organized by intent.

Each template uses ``{retrieved_schemas}`` (a pre-rendered text block from the
schema retrieval node) and ``{user_query}``. Few-shot examples are intent-
specific so the model is biased toward the right pattern.
"""

# ---------------------------------------------------------------------------
# Shared rules
# ---------------------------------------------------------------------------

_RULES = """## 规则
1. 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP/CREATE/ALTER
2. 只能使用下方提供的表和字段，不要捏造任何不存在的表或字段
3. 日期使用 PostgreSQL 语法，例如 WHERE order_date >= DATE '2026-03-01'
4. 涉及"上个月"、"最近 N 天"等相对时间，必须用 DATE_TRUNC / CURRENT_DATE / INTERVAL 表达
5. 聚合查询必须包含 GROUP BY，且 SELECT 列要么是分组列要么是聚合函数
6. 排序加 ORDER BY，结果默认 LIMIT 100，除非用户明确要求全部
7. 优先使用 DWD/DWS 层（已清洗/聚合），ODS 层只在缺字段时使用
8. 单语句、不带分号、不要解释、不要 markdown 代码块，只返回纯 SQL"""


_SCHEMA_BLOCK = """## 数据库 Schema
{retrieved_schemas}"""


_USER_BLOCK = """## 用户问题
{user_query}

## SQL"""


# ---------------------------------------------------------------------------
# Intent-specific few-shot examples
# ---------------------------------------------------------------------------

_FEW_SHOT_SIMPLE = """## 示例
用户：总共有多少注册用户
SQL：
SELECT COUNT(*) AS user_count FROM dwd_user_profile

用户：查询所有金牌等级用户的城市分布
SQL：
SELECT city, COUNT(*) AS user_count
FROM dwd_user_profile
WHERE user_level = 'gold'
GROUP BY city
ORDER BY user_count DESC
LIMIT 100"""


_FEW_SHOT_AGGREGATION = """## 示例
用户：上个月各品类的销售额是多少
SQL：
SELECT category_l1, SUM(total_amount) AS total_sales
FROM dwd_order_detail
WHERE order_date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
  AND order_date <  DATE_TRUNC('month', CURRENT_DATE)
GROUP BY category_l1
ORDER BY total_sales DESC

用户：最近7天每天的订单数量趋势
SQL：
SELECT order_date, COUNT(*) AS order_count
FROM dwd_order_detail
WHERE order_date >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY order_date
ORDER BY order_date

用户：哪个城市的客单价最高
SQL：
SELECT user_city, AVG(total_amount) AS avg_order_amount
FROM dwd_order_detail
WHERE order_status = 'completed'
GROUP BY user_city
ORDER BY avg_order_amount DESC
LIMIT 10"""


_FEW_SHOT_MULTI_JOIN = """## 示例
用户：金牌用户最喜欢哪个品牌的商品
SQL：
SELECT brand, COUNT(*) AS purchase_count, SUM(total_amount) AS total_spent
FROM dwd_order_detail
WHERE user_level = 'gold'
GROUP BY brand
ORDER BY purchase_count DESC
LIMIT 10

用户：上个月各等级用户的人均消费
SQL：
SELECT user_level,
       COUNT(DISTINCT user_id) AS user_count,
       SUM(total_amount) AS total_revenue,
       SUM(total_amount) / NULLIF(COUNT(DISTINCT user_id), 0) AS avg_per_user
FROM dwd_order_detail
WHERE order_date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
  AND order_date <  DATE_TRUNC('month', CURRENT_DATE)
GROUP BY user_level
ORDER BY avg_per_user DESC"""


_FEW_SHOT_EXPLORATION = """## 示例
用户：分析一下电子产品品类最近的销售趋势
SQL：
SELECT order_date,
       SUM(total_amount) AS daily_revenue,
       COUNT(DISTINCT order_id) AS order_count,
       SUM(profit) AS daily_profit
FROM dwd_order_detail
WHERE category_l1 = '电子产品'
  AND order_date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY order_date
ORDER BY order_date"""


_TEMPLATES_BY_INTENT = {
    "simple_query":  _FEW_SHOT_SIMPLE,
    "aggregation":   _FEW_SHOT_AGGREGATION,
    "multi_join":    _FEW_SHOT_MULTI_JOIN,
    "exploration":   _FEW_SHOT_EXPLORATION,
}


SYSTEM_PROMPT = "你是一个专业的 PostgreSQL 数据分析师。根据用户的自然语言问题和提供的 Schema，生成准确的 SQL 查询。"


def build_sql_generation_prompt(
    user_query: str,
    retrieved_schemas: str,
    intent: str,
) -> list[dict[str, str]]:
    """Return a chat-completion ``messages`` list for SQL generation."""
    few_shot = _TEMPLATES_BY_INTENT.get(intent, _FEW_SHOT_AGGREGATION)
    user_content = "\n\n".join(
        [
            _RULES,
            _SCHEMA_BLOCK.format(retrieved_schemas=retrieved_schemas),
            few_shot,
            _USER_BLOCK.format(user_query=user_query),
        ]
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
