"""Intent classification prompt.

The classifier is asked to pick exactly one intent from a fixed set and emit
JSON. ``clarification`` is reserved for queries that are too ambiguous to
proceed (missing time range, undefined metric, etc.); the graph routes those
to an early-return branch.
"""

INTENT_LABELS = [
    "simple_query",   # 单表 SELECT，无聚合或仅 COUNT
    "aggregation",    # GROUP BY/SUM/AVG，可能 1-2 表
    "multi_join",     # 3 张及以上表关联
    "exploration",    # 探索性问题，需要多步推理
    "clarification",  # 信息不全，需要追问
]

INTENT_CLASSIFICATION_PROMPT = """你是一个数据分析查询意图分类器。判断用户问题属于下列哪一类：

- simple_query: 简单查询，单表，无复杂聚合（如"总共有多少用户"）
- aggregation: 聚合统计，含 SUM/AVG/COUNT/GROUP BY（如"上个月各品类销售额"）
- multi_join: 需要 3 张及以上表关联（如"金牌用户在某城市买的品牌分布"）
- exploration: 探索性、开放式分析（如"最近用户留存有什么变化"）
- clarification: 信息不完整，无法直接生成 SQL（缺时间范围、指标定义不明等）

只输出 JSON，严格如下格式：
{{"intent": "上述五个标签之一", "reason": "一句话理由", "clarification_question": "若 intent=clarification 则填追问，否则为空字符串"}}

用户问题：
{user_query}
"""
