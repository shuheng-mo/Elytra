"""SQL generation prompts, organized by intent and dialect.

Each template uses ``{retrieved_schemas}`` (a pre-rendered text block from the
schema retrieval node) and ``{user_query}``. Few-shot examples are intent-
specific so the model is biased toward the right pattern.

Dialect-specific syntax instructions are appended via ``DIALECT_INSTRUCTIONS``
so the same agent can target PostgreSQL, DuckDB, or StarRocks (MySQL-compatible)
without changing the system prompt.

v0.5.0 introduces :class:`PromptContext`, a single dataclass that the sql
generator node fills in and passes to :func:`build_sql_generation_prompt`.
This lets self-evolution (dynamic few-shot from experience pool) and
multi-turn dialog (conversation context block) plug in new content without
requiring a new signature for every feature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Shared rules (dialect-neutral)
# ---------------------------------------------------------------------------

_RULES = """## 规则
1. 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP/CREATE/ALTER
2. 只能使用下方提供的表和字段，不要捏造任何不存在的表或字段
3. 涉及"上个月"、"最近 N 天"等相对时间，按目标方言的标准函数表达
4. 聚合查询必须包含 GROUP BY，且 SELECT 列要么是分组列要么是聚合函数
5. 排序加 ORDER BY，结果默认 LIMIT 100，除非用户明确要求全部
6. 优先使用 DWD/DWS 层（已清洗/聚合），ODS 层只在缺字段时使用
7. 单语句、不带分号、不要解释、不要 markdown 代码块，只返回纯 SQL"""


# ---------------------------------------------------------------------------
# Dialect-specific syntax instructions
# ---------------------------------------------------------------------------

DIALECT_INSTRUCTIONS: dict[str, str] = {
    "postgresql": """## PostgreSQL 方言要求
- 字符串拼接用 `||`
- 日期截断用 `DATE_TRUNC('month', col)`
- 相对时间用 `CURRENT_DATE - INTERVAL '7 days'`
- 模糊匹配可用 `ILIKE`
- 分页用 `LIMIT n OFFSET m`""",
    "duckdb": """## DuckDB 方言要求
- 大致兼容 PostgreSQL 语法
- 字符串拼接用 `||` 或 `CONCAT()`
- 日期截断用 `DATE_TRUNC('month', col)`
- 相对时间用 `CURRENT_DATE - INTERVAL '7 days'`
- 支持 `LIST` / `STRUCT` 类型；可直接 `read_parquet('path')`
- 分页用 `LIMIT n OFFSET m`""",
    "starrocks": """## StarRocks 方言要求 (MySQL 兼容)
- 字符串拼接用 `CONCAT(a, b)`，**不支持 `||`**
- 日期格式化用 `DATE_FORMAT(col, '%Y-%m-%d')`，**不支持 `TO_CHAR`**
- 相对时间用 `DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)`
- **不支持 `ILIKE`**，用 `LOWER(col) LIKE LOWER('%pattern%')` 替代
- 分页用 `LIMIT offset, count` 或 `LIMIT count OFFSET offset`
- 不支持 `WITH RECURSIVE`""",
    "clickhouse": """## ClickHouse 方言要求
- 月份截断用 `toStartOfMonth(col)`，**不支持 `DATE_TRUNC('month', col)`**
- 周截断用 `toMonday(col)`，年截断用 `toStartOfYear(col)`
- 日期格式化用 `formatDateTime(col, '%Y-%m-%d')`，**不支持 `TO_CHAR`**
- 当前日期用 `today()`，**不是 `CURRENT_DATE`**；当前时间用 `now()`
- 日期加减用 `col - INTERVAL 1 MONTH` 或 `addDays(col, 7)` / `subtractMonths(col, 1)`
- 字符串拼接用 `concat(a, b, c)`，**不支持 `||`**
- 大小写转换用 `lower()` / `upper()`
- 子串用 `substring(col, start, length)` 或 `position(col, 'pattern')`
- **不支持 `ILIKE`**，用 `lower(col) LIKE lower('%pattern%')` 或 `positionCaseInsensitive()`
- 空值处理用 `ifNull(col, default)`，也可以用 `coalesce()`
- 条件表达式用 `if(cond, then, else)` 或标准的 `CASE WHEN`
- 整数除法默认取整，浮点结果用 `col1 * 1.0 / col2`
- 聚合去重优先用 `uniqExact(col)`（精确）或 `uniq(col)`（近似，快 10 倍 ~2% 误差），避免 `COUNT(DISTINCT col)` 性能差
- 分页用 `LIMIT n OFFSET m`
- NULL 排序默认靠前，如需靠后用 `ORDER BY col NULLS LAST`
- SummingMergeTree 聚合结果查询时应主动 `GROUP BY` + `sum(...)`，或表名后加 ` FINAL`
- **不支持 UPDATE / DELETE / 事务 / 递归 CTE**（MergeTree 引擎限制）""",
    "hiveql": """## HiveQL 方言要求
- 字符串拼接用 `CONCAT(a, b)`
- 日期函数：`DATE_FORMAT` / `FROM_UNIXTIME`
- 不支持 `ILIKE`""",
    "sparksql": """## Spark SQL 方言要求
- 字符串拼接用 `||` 或 `CONCAT()`
- 日期函数：`DATE_TRUNC` / `DATE_FORMAT` 都支持""",
}


def _get_dialect_instructions(dialect: str) -> str:
    return DIALECT_INSTRUCTIONS.get(dialect, DIALECT_INSTRUCTIONS["postgresql"])


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


SYSTEM_PROMPT = "你是一个专业的数据分析师。根据用户的自然语言问题和提供的 Schema，生成准确的 SQL 查询。严格按照下方指定的 SQL 方言书写。"


# ---------------------------------------------------------------------------
# v0.5.0 PromptContext — single struct that carries everything the prompt
# builder might need. Features add fields instead of new function signatures.
# ---------------------------------------------------------------------------


@dataclass
class PromptContext:
    user_query: str
    retrieved_schemas: str
    intent: str = "aggregation"
    dialect: str = "postgresql"
    # Self-evolution: dynamic few-shot from experience_pool + query_feedback
    dynamic_examples: dict[str, Any] = field(default_factory=dict)
    # Multi-turn: recent turns from query_history + compressed summary
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    context_summary: str | None = None


def build_dynamic_few_shot_block(dynamic_examples: dict[str, Any]) -> str:
    """Render the dynamic few-shot block from experience pool + feedback.

    Phase 4b populates this with three groups:
        - ``corrections``: past failure → fix pairs (avoid these mistakes)
        - ``golden``: user-upvoted examples (these worked)
        - ``negative``: user-downvoted examples (don't do this)

    v0.5.0-phase4a: this function exists so sql_generator can already call it
    even when the dict is empty. Once Phase 4b wires retrieve_experience, the
    dict will be populated and the block will appear in the prompt.
    """
    if not dynamic_examples:
        return ""

    blocks: list[str] = []

    golden = dynamic_examples.get("golden") or []
    if golden:
        blocks.append("## 参考：过去被验证正确的类似查询")
        for ex in golden[:2]:
            blocks.append(f"用户：{ex.get('user_query', '')}")
            sql = ex.get("generated_sql") or ex.get("sql") or ""
            blocks.append(f"SQL：{sql}")
            blocks.append("")

    corrections = dynamic_examples.get("corrections") or []
    if corrections:
        blocks.append("## 注意：以下是过去类似查询中常见的错误和修正")
        for ex in corrections[:2]:
            blocks.append(f"用户：{ex.get('user_query', '')}")
            blocks.append(f"错误SQL：{ex.get('failed_sql', '')}")
            blocks.append(f"错误原因：{ex.get('error_message', '')}")
            blocks.append(f"正确SQL：{ex.get('corrected_sql', '')}")
            blocks.append("")

    negatives = dynamic_examples.get("negative") or []
    if negatives:
        blocks.append("## 警告：以下 SQL 曾被用户标记为错误，请避免类似写法")
        for ex in negatives[:1]:
            blocks.append(f"用户：{ex.get('user_query', '')}")
            sql = ex.get("generated_sql") or ex.get("sql") or ""
            blocks.append(f"被拒绝的SQL：{sql}")
            blocks.append("")

    return "\n".join(blocks).strip()


def build_conversation_context_block(
    history: list[dict[str, Any]],
    summary: str | None,
) -> str:
    """Render the multi-turn conversation context block.

    Phase 5 populates this from the resolve_context node, which reads the
    last 3 successful turns from ``query_history`` by ``session_id`` plus
    the current ``conversation_summary`` if one exists.
    """
    if not history and not summary:
        return ""

    blocks: list[str] = []
    if summary:
        blocks.append("## 对话摘要")
        blocks.append(summary)
        blocks.append("")

    if history:
        blocks.append("## 最近对话（按时间倒序，最近的在前）")
        for turn in history[:3]:
            q = turn.get("user_query") or turn.get("query") or ""
            sql = turn.get("generated_sql") or turn.get("sql") or ""
            blocks.append(f"用户：{q}")
            if sql:
                blocks.append(f"SQL：{sql}")
            blocks.append("")

    return "\n".join(blocks).strip()


def build_sql_generation_prompt(
    user_query: str,
    retrieved_schemas: str,
    intent: str,
    dialect: str = "postgresql",
    *,
    dynamic_examples: dict[str, Any] | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    context_summary: str | None = None,
) -> list[dict[str, str]]:
    """Return a chat-completion ``messages`` list for SQL generation.

    The ``dialect`` argument controls which ``DIALECT_INSTRUCTIONS`` block is
    appended to the user content; few-shot examples remain shared because the
    business intent is dialect-independent.

    New in v0.5.0: callers can pass ``dynamic_examples`` and conversation
    context via kwargs. These slot in between the schema block and the
    static few-shot so the LLM sees dynamic/recent context first, with the
    static templates as a fallback. The prompt remains backward-compatible
    — omit the kwargs to get the old behavior.
    """
    few_shot = _TEMPLATES_BY_INTENT.get(intent, _FEW_SHOT_AGGREGATION)

    dynamic_block = build_dynamic_few_shot_block(dynamic_examples or {})
    conversation_block = build_conversation_context_block(
        conversation_history or [],
        context_summary,
    )

    parts = [
        _RULES,
        _get_dialect_instructions(dialect),
        _SCHEMA_BLOCK.format(retrieved_schemas=retrieved_schemas),
    ]
    if dynamic_block:
        parts.append(dynamic_block)
    if conversation_block:
        parts.append(conversation_block)
    parts.append(few_shot)
    parts.append(_USER_BLOCK.format(user_query=user_query))

    user_content = "\n\n".join(parts)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def build_sql_generation_prompt_from_context(
    ctx: PromptContext,
) -> list[dict[str, str]]:
    """Dataclass-based wrapper — the preferred v0.5.0 call site.

    Equivalent to :func:`build_sql_generation_prompt` but accepts a single
    ``PromptContext`` so new fields can be added without touching every
    caller.
    """
    return build_sql_generation_prompt(
        user_query=ctx.user_query,
        retrieved_schemas=ctx.retrieved_schemas,
        intent=ctx.intent,
        dialect=ctx.dialect,
        dynamic_examples=ctx.dynamic_examples,
        conversation_history=ctx.conversation_history,
        context_summary=ctx.context_summary,
    )
