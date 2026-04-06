"""Self-correction prompt: feed the previous SQL + DB error back to the LLM.

Strategy: instead of asking the model to "fix" the SQL in place, we give it
the original question, the schema, and the failed attempt with the error
message, and ask for a complete rewrite. This avoids the model getting stuck
patching syntax in a structurally wrong query.
"""

SELF_CORRECTION_SYSTEM = "你是一个 PostgreSQL 错误修复专家。你将看到一段 SQL 和它的报错，请基于 Schema 重写一段正确的 SQL。"


SELF_CORRECTION_TEMPLATE = """## 用户问题
{user_query}

## 数据库 Schema
{retrieved_schemas}

## 上一次失败的 SQL
{failed_sql}

## 错误信息
{error_message}

## 历史尝试（如果有）
{history}

## 修复指引
1. 仔细分析报错原因（字段名错误？类型不匹配？语法问题？聚合规则？）
2. 重写一段完整的、独立可执行的 SELECT 语句
3. 不要解释、不要 markdown，只返回纯 SQL，单语句、不带分号

## 修复后的 SQL"""


def build_self_correction_prompt(
    user_query: str,
    retrieved_schemas: str,
    failed_sql: str,
    error_message: str,
    history: list[dict] | None = None,
) -> list[dict[str, str]]:
    history_text = "无" if not history else "\n".join(
        f"- 尝试 {i+1}: {h.get('error', '')[:200]}" for i, h in enumerate(history)
    )
    user_content = SELF_CORRECTION_TEMPLATE.format(
        user_query=user_query,
        retrieved_schemas=retrieved_schemas,
        failed_sql=failed_sql,
        error_message=error_message,
        history=history_text,
    )
    return [
        {"role": "system", "content": SELF_CORRECTION_SYSTEM},
        {"role": "user", "content": user_content},
    ]
