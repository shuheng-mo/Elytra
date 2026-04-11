"""Prompt template for conversation summarization (Phase 5).

Kept here so the summarize_conversation node stays short. The template is
designed to produce a compact Chinese summary that fits in ~200 tokens and
captures entities, time windows, and filter conditions — the things a
follow-up turn is most likely to refer back to via pronouns.
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一个对话压缩助手。用户在和数据分析 Agent 多轮交流。
请读取最近几轮对话并总结出一份简明中文摘要，用于后续轮次的上下文注入。

要求：
1. 明确识别所提及的实体（用户群体 / 商品品类 / 时间范围 / 地理区域等）
2. 保留过滤条件和聚合维度，因为下一轮用户很可能用"他们"/"这些"指代前面的对象
3. 不要包含 SQL，摘要是给下一轮 SQL 生成用的上下文，不是 SQL 样例
4. 长度控制在 200 字以内
5. 直接输出摘要文本，不要解释，不要前缀"""


def build_summary_prompt(
    turns: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build messages for the cheap-model conversation summarizer.

    ``turns`` is a list of ``{user_query, generated_sql}`` dicts in
    chronological order. The SQL is included so the summarizer can infer
    what columns and time ranges were actually queried.
    """
    turn_blocks: list[str] = []
    for i, t in enumerate(turns, 1):
        q = t.get("user_query") or ""
        sql = t.get("generated_sql") or ""
        turn_blocks.append(f"第 {i} 轮\n用户：{q}\nSQL：{sql}")

    user_content = (
        "## 最近几轮对话\n\n"
        + "\n\n".join(turn_blocks)
        + "\n\n## 请输出摘要"
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
