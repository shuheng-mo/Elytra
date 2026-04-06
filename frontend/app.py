"""Elytra Streamlit frontend (Phase 1).

Layout:
    Sidebar — data dictionary browser + session controls
    Main    — query input, agent trace, SQL, result table, auto chart

The frontend talks to the FastAPI backend over HTTP. Set ``API_URL`` to point
at a non-default host:

    API_URL=http://backend:8000 streamlit run frontend/app.py
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
HTTP_TIMEOUT = float(os.getenv("API_TIMEOUT", "120"))


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


def api_post_query(query: str, session_id: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{API_URL}/api/query",
        json={"query": query, "session_id": session_id, "dialect": "postgresql"},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=300, show_spinner=False)
def api_get_schema() -> dict[str, Any]:
    resp = httpx.get(f"{API_URL}/api/schema", timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def api_get_history(session_id: str, limit: int = 20) -> dict[str, Any]:
    resp = httpx.get(
        f"{API_URL}/api/history",
        params={"session_id": session_id, "limit": limit},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def render_chart(rows: list[dict[str, Any]], hint: str | None) -> None:
    """Render the result table as a chart based on the agent's visualization hint."""
    if not rows:
        st.info("查询返回了 0 行结果。")
        return

    df = pd.DataFrame(rows)

    if hint == "number" and len(df) == 1 and len(df.columns) == 1:
        col = df.columns[0]
        st.metric(label=col, value=df.iloc[0, 0])
        return

    if hint in ("bar_chart", "line_chart") and len(df.columns) >= 2:
        x_col = df.columns[0]
        y_cols = [c for c in df.columns[1:] if pd.api.types.is_numeric_dtype(df[c])]
        if y_cols:
            chart_df = df.set_index(x_col)[y_cols]
            if hint == "line_chart":
                st.line_chart(chart_df)
            else:
                st.bar_chart(chart_df)
            with st.expander("查看原始数据", expanded=False):
                st.dataframe(df, use_container_width=True)
            return

    # Fallback: just show the table
    st.dataframe(df, use_container_width=True)


def render_sidebar_schema() -> None:
    st.sidebar.header("数据字典")
    try:
        schema = api_get_schema()
    except httpx.HTTPError as exc:
        st.sidebar.error(f"无法加载 schema：{exc}")
        return

    layers = schema.get("layers", {})
    layer_order = ["ODS", "DWD", "DWS"]
    for layer in layer_order:
        tables = layers.get(layer, [])
        if not tables:
            continue
        st.sidebar.subheader(f"{layer} 层 ({len(tables)})")
        for tbl in tables:
            with st.sidebar.expander(
                f"{tbl['table']} — {tbl.get('chinese_name', '')}", expanded=False
            ):
                if tbl.get("description"):
                    st.caption(tbl["description"])
                cols = tbl.get("columns", [])
                if cols:
                    cols_df = pd.DataFrame(
                        [
                            {
                                "字段": c["name"],
                                "类型": c.get("type", ""),
                                "中文": c.get("chinese_name", ""),
                                "PK": "✓" if c.get("is_primary_key") else "",
                            }
                            for c in cols
                        ]
                    )
                    st.dataframe(cols_df, use_container_width=True, hide_index=True)
                if tbl.get("common_queries"):
                    st.markdown("**常用查询示例**")
                    for q in tbl["common_queries"]:
                        st.markdown(f"- {q}")


def render_sidebar_history(session_id: str) -> None:
    st.sidebar.divider()
    st.sidebar.header("最近查询")
    try:
        data = api_get_history(session_id, limit=10)
    except httpx.HTTPError as exc:
        st.sidebar.caption(f"暂无历史（{exc}）")
        return
    items = data.get("history", [])
    if not items:
        st.sidebar.caption("当前会话还没有查询记录")
        return
    for item in items:
        ok = "✅" if item.get("execution_success") else "❌"
        with st.sidebar.expander(f"{ok} {item.get('user_query', '')[:30]}", expanded=False):
            st.caption(
                f"intent={item.get('intent', '')} | "
                f"model={item.get('model_used', '')} | "
                f"latency={item.get('latency_ms', '?')}ms"
            )
            if item.get("generated_sql"):
                st.code(item["generated_sql"], language="sql")


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Elytra — NL→SQL 数据分析",
        page_icon="🪶",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid.uuid4().hex[:12]
    if "last_response" not in st.session_state:
        st.session_state.last_response = None

    st.title("Elytra")
    st.caption("用自然语言查询数据，自动生成 SQL、执行并可视化")

    render_sidebar_schema()
    render_sidebar_history(st.session_state.session_id)

    with st.sidebar:
        st.divider()
        st.markdown("**会话**")
        st.code(st.session_state.session_id)
        if st.button("新建会话", use_container_width=True):
            st.session_state.session_id = uuid.uuid4().hex[:12]
            st.session_state.last_response = None
            st.rerun()
        st.caption(f"API: `{API_URL}`")

    # Query input
    examples = [
        "上个月销售额最高的商品品类是什么",
        "最近 7 天每天的订单数量趋势",
        "金牌用户最喜欢哪个品牌的商品",
        "哪个城市的客单价最高",
        "总共有多少注册用户",
    ]

    with st.form("query_form", clear_on_submit=False):
        query = st.text_area(
            "你的问题",
            placeholder="例如：上个月销售额最高的商品品类是什么",
            height=80,
        )
        col1, col2 = st.columns([1, 4])
        with col1:
            submitted = st.form_submit_button("查询", type="primary", use_container_width=True)
        with col2:
            st.caption("提示：在侧边栏可以浏览数据字典")

    st.markdown("**示例问题**")
    cols = st.columns(len(examples))
    for col, example in zip(cols, examples):
        with col:
            if st.button(example, key=f"ex_{example}", use_container_width=True):
                query = example
                submitted = True

    if submitted and query.strip():
        with st.spinner("Agent 正在分析…"):
            try:
                response = api_post_query(query.strip(), st.session_state.session_id)
            except httpx.HTTPError as exc:
                st.error(f"调用 API 失败：{exc}")
                return
        st.session_state.last_response = response

    response = st.session_state.last_response
    if response is None:
        st.info("输入一个问题然后点击查询")
        return

    # ---------- Result panel ----------
    if response.get("success"):
        st.success(response.get("final_answer") or "查询成功")
    else:
        st.error(response.get("final_answer") or response.get("error") or "查询失败")

    # Metrics row
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("意图", response.get("intent") or "—")
    m2.metric("模型", response.get("model_used") or "—")
    m3.metric("重试次数", response.get("retry_count", 0))
    m4.metric("延迟 (ms)", response.get("latency_ms", 0))
    m5.metric("Token", response.get("token_count", 0))

    tab_result, tab_sql, tab_raw = st.tabs(["结果", "SQL", "原始响应"])

    with tab_result:
        rows = response.get("result") or []
        render_chart(rows, response.get("visualization_hint"))

    with tab_sql:
        sql = response.get("generated_sql")
        if sql:
            st.code(sql, language="sql")
        else:
            st.info("没有生成 SQL")
        if response.get("error"):
            st.warning(f"执行错误：{response['error']}")

    with tab_raw:
        st.json(response)


if __name__ == "__main__":
    main()
