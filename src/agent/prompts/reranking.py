"""Reranker prompt template (Phase 1: LLM-as-Reranker).

The actual rerank loop lives in ``src/retrieval/reranker.py``; this module is
the canonical home of the prompt so it can be edited without touching
retrieval code. ``reranker.py`` re-imports the constant.
"""

RERANK_PROMPT = """你是一个数据库 Schema 检索 Reranker。给定用户的自然语言查询和若干候选数据表的描述，请判断每张表与该查询的相关性，并给出 0-10 的整数评分。

评分原则：
- 10 = 这张表几乎一定要用到；0 = 与查询完全无关
- 优先选择 DWD/DWS 层（已清洗或聚合，更贴近分析需求），ODS 层只在 DWD/DWS 缺失对应字段时才高分
- 对每张候选表都要给出评分，不要遗漏

只输出 JSON，不要任何额外文本，格式严格如下：
{{"scores": [{{"table": "表名", "score": 整数, "reason": "一句话理由"}}]}}

用户查询：
{query}

候选表：
{candidates}
"""
