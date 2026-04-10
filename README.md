<div align="center">

<img src="assets/elytra-logo-hex-wordmark.svg" width="320" alt="Elytra" />

# Elytra

**基于 LLM 的智能数据分析系统 — 自然语言进，SQL + 可视化出**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-latest-1c3d5a.svg)](https://github.com/langchain-ai/langgraph)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791.svg)](https://github.com/pgvector/pgvector)
[![Tests](https://img.shields.io/badge/tests-173%2F173%20passing-brightgreen.svg)](#测试)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[简体中文](README.md) | [English](README_EN.md)

</div>

---

## 目录

- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [支持的数据源](#支持的数据源)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [API 文档](#api-文档)
- [评估体系](#评估体系)
- [测试](#测试)
- [Roadmap](#roadmap)
- [架构决策](#架构决策)
- [贡献](#贡献)
- [License](#license)

---

## 项目简介

Elytra 是一个面向业务分析师的 **NL→SQL 智能数据分析系统**。用户用自然语言提问，系统自动：

1. **意图分类** — 判断是简单查询、聚合、多表关联、探索分析还是需要追问澄清
2. **Schema 召回** — BM25 + 向量混合检索 + LLM Reranker，从数据字典里挑出最相关的表
3. **SQL 生成** — 按意图选 few-shot 模板，路由到便宜或强大模型
4. **安全执行** — SELECT-only 过滤、`statement_timeout`、行数上限
5. **自修正** — 出错时把 SQL + 错误信息回喂给 LLM，最多 3 次重试
6. **结果格式化** — 根据结果形状自动推断 number/bar_chart/line_chart/table 可视化

> **它不是一个简单的 NL2SQL wrapper。** Elytra 自带完整的 ODS→DWD→DWS 三层数仓底座、Schema 智能检索、Agent 多步推理 + 自修正循环、多模型路由（成本/质量权衡），以及量化评估体系。

业务场景为模拟电商 SaaS 平台：用户、商品、订单、支付、行为日志五张原始表，加上一个订单明细宽表、一个用户画像表、一个商品维度表，再叠加 daily / weekly 的预聚合表。

---

## 核心特性

| 能力 | 实现 |
|:---|:---|
| **三层数仓** | ODS（5 表） → DWD（3 表宽表/画像/维度） → DWS（3 表预聚合） |
| **混合检索** | BM25（自定义 CJK + Latin tokenizer）+ pgvector HNSW 余弦检索 + min-max 归一化 + 加权融合（0.4 / 0.6） |
| **三种 Embedding 后端** | OpenAI 直连 / OpenRouter（支持 `openai/text-embedding-3-large`）/ 本地 `sentence-transformers`（BGE 系列） |
| **LLM Reranker** | Phase 1 用便宜 LLM 打分重排，失败时降级到上游顺序 |
| **LangGraph Agent** | 8 个节点的状态机，含意图路由、自修正回环（最多 3 次重试）、错误兜底 |
| **多模型路由** | 简单查询 → DeepSeek，多表 / 探索 / 连续失败 → Claude Sonnet |
| **SELECT-only 安全过滤** | 剥离注释和字符串字面量后扫描 16 个禁用关键字，多语句拒绝 |
| **OpenRouter 优先** | 一个 key 路由所有模型，自动 vendor 前缀；旧的 per-vendor key 仍向后兼容 |
| **可视化推断** | 按结果形状（行数 × 列数 + 列名）自动选 metric / bar / line / table |
| **量化评估** | 14 case 测试集，PASS/FAIL 阈值标注，per-category 细分，自修正成功率统计 |

---

## 系统架构

整体调用链：Streamlit 前端 → FastAPI → LangGraph Agent → 检索 / 路由 / 执行子系统 → PostgreSQL + pgvector。

```mermaid
flowchart TB
    subgraph Client["客户端"]
        UI["Streamlit Frontend<br/>frontend/app.py"]
    end

    subgraph API["FastAPI 后端"]
        QApi["POST /api/query"]
        SApi["GET /api/schema"]
        HApi["GET /api/history"]
    end

    subgraph Agent["LangGraph Agent (StateGraph)"]
        direction TB
        Intent["classify_intent"]
        Clarify["format_clarification"]
        Retrieve["retrieve_schema"]
        Gen["generate_sql"]
        Exec["execute_sql"]
        Correct["self_correction"]
        Result["format_result"]
        Err["format_error"]

        Intent -- "intent = clarification" --> Clarify
        Intent -- "其他 intent" --> Retrieve
        Retrieve --> Gen
        Gen --> Exec
        Exec -- "success" --> Result
        Exec -- "failure & retry < MAX" --> Correct
        Correct --> Gen
        Exec -- "failure & retry == MAX" --> Err
    end

    subgraph Sub["核心子系统"]
        Hybrid["Hybrid Retriever<br/>BM25 + 向量"]
        Rerank["LLM Reranker"]
        Router["Model Router<br/>cheap / strong"]
        Safety["SQL Safety Filter<br/>SELECT-only + timeout"]
    end

    subgraph Data["数据层"]
        PG[("PostgreSQL 16<br/>ODS / DWD / DWS")]
        Vec[("pgvector HNSW<br/>schema_embeddings")]
    end

    UI -->|HTTP| QApi
    UI -->|HTTP| SApi
    UI -->|HTTP| HApi
    QApi -->|run_agent| Intent
    SApi --> PG
    HApi --> PG

    Retrieve --> Hybrid
    Hybrid --> Rerank
    Hybrid --> Vec
    Gen --> Router
    Exec --> Safety
    Safety --> PG

    Clarify --> END0(["END"])
    Result --> END0
    Err --> END0
```

---

## 支持的数据源

Elytra 通过可插拔的 **DataSource Connector** 抽象层支持多种数据库引擎。新增数据源
只需实现 `DataSourceConnector` 接口（约 100 行）并在 `config/datasources.yaml`
添加一段配置——无需改动 agent / 检索 / API 任何核心代码。

| 引擎 | 状态 | 用途 |
|:---|:---|:---|
| **PostgreSQL** | ✅ 内置 | 默认电商数仓（ODS / DWD / DWS 三层模型）|
| **DuckDB** | ✅ 内置 | 嵌入式 OLAP — 含 TPC-H 标准数据集与 Brazilian Olist 真实数据集 |
| **StarRocks** | ✅ 可选 | 高性能 OLAP，MySQL 协议兼容，独立 docker compose |

详细的连接器接口在 `src/connectors/base.py::DataSourceConnector`，每个 connector
都通过 `config/datasources.yaml` 中的一个 YAML 块描述：

```yaml
default_source: ecommerce_pg

datasources:
  - name: ecommerce_pg
    dialect: postgresql
    description: "电商模拟数仓"
    connection:
      host: ${DB_HOST:-localhost}
      port: ${DB_PORT:-5432}
      database: Elytra
    overlay: db/data_dictionary.yaml      # 中文字段描述（可选）

  - name: tpch_duckdb
    dialect: duckdb
    description: "TPC-H 标准测试数据集"
    connection:
      database_path: ./datasets/tpch/tpch.duckdb
    overlay: config/overlays/tpch_duckdb.yaml
```

API 调用时通过 `source` 字段指定数据源（省略则用 `default_source`）：

```bash
curl -X POST localhost:8000/api/query -d '{
  "query": "上个月销售额最高的品类",
  "source": "tpch_duckdb"
}'
```

`GET /api/datasources` 列出全部已配置数据源及其连接状态。

### 快速体验：TPC-H

```bash
python datasets/tpch/load_tpch.py                    # 生成 SF=0.1 DuckDB（无需下载）
python -m src.retrieval.bootstrap --source tpch_duckdb
# 然后用 source=tpch_duckdb 提问
```

### 快速体验：Brazilian E-Commerce

```bash
# 1) 从 Kaggle 下载 https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
#    解压到 datasets/brazilian_ecommerce/csv/
python datasets/brazilian_ecommerce/load_brazilian.py
python -m src.retrieval.bootstrap --source brazilian_ecommerce
```

### 启用 StarRocks（可选）

```bash
docker compose -f docker/starrocks/docker-compose.starrocks.yml up -d
# 详见 docker/starrocks/README.md
```

---

## 技术栈

| 层 | 技术 |
|:---|:---|
| 语言 | Python ≥ 3.11 |
| 数据库 | PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector) / DuckDB / StarRocks（可选） |
| LLM 框架 | [LangChain](https://github.com/langchain-ai/langchain) + [LangGraph](https://github.com/langchain-ai/langgraph) |
| 后端 | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) + [Pydantic v2](https://docs.pydantic.dev/latest/) |
| 前端 | [Streamlit](https://streamlit.io/) ≥ 1.35 |
| BM25 | [rank-bm25](https://github.com/dorianbrown/rank_bm25) |
| Embedding | OpenAI / OpenRouter / [sentence-transformers](https://www.sbert.net/) |
| 数据库驱动 | psycopg2-binary / asyncpg / duckdb / aiomysql |
| 容器化 | Docker + Docker Compose |
| 包管理 | [uv](https://github.com/astral-sh/uv)（推荐） |
| 测试 | pytest + httpx TestClient |

---

## 快速开始

### 前置要求

- Python ≥ 3.11
- Docker + Docker Compose（推荐方式）
- 一个 LLM API key — 推荐 [OpenRouter](https://openrouter.ai/)（一个 key 路由所有模型）

### 方式 1：Docker Compose（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/shuheng-mo/Elytra.git
cd Elytra

# 2. 配置环境变量
cp .env.example .env
# 用编辑器打开 .env，填入 OPENROUTER_API_KEY

# 3. 启动整个栈（首次会拉 pgvector/pg16 + 构建 backend/frontend 两个镜像）
docker compose up --build -d

# 4. 等 db 健康后，初始化 schema_embeddings 向量索引（一次性）
docker compose exec backend python -m src.retrieval.bootstrap

# 5. 跑端到端评估
docker compose exec backend python eval/run_eval.py
```

服务地址：

- **前端 UI**：<http://localhost:8501>
- **API Swagger**：<http://localhost:8000/docs>
- **健康检查**：<http://localhost:8000/healthz>

### 方式 2：本地开发

```bash
# 1. 装依赖（uv 推荐）。--extra local-embed 会拉 sentence-transformers + torch
#    （arm64 ~700MB），用于本地 BGE embedding —— 这是 .env.example 里的默认。
#    如果你打算改用 OpenAI 直连 embedding，可以省掉这个 extra。
uv sync --extra local-embed

# 2. 起一个 pgvector 数据库（compose 也行）
docker run -d --name elytra-db \
  -e POSTGRES_DB=Elytra -e POSTGRES_USER=Elytra -e POSTGRES_PASSWORD=Elytra_dev \
  -p 5432:5432 \
  -v "$PWD/db/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
  -v "$PWD/db/seed_data.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro" \
  pgvector/pgvector:pg16

# 3. 配 .env，把 DATABASE_URL 和 DB_HOST 里的 `db` 改成 `localhost`
#    （`db` 只在 docker compose 内部网络解析得了）
cp .env.example .env

# 4. 初始化 schema_embeddings（首次会下载 ~100MB BGE 模型权重，离线后永久可用）
.venv/bin/python -m src.retrieval.bootstrap

# 5. 起后端
.venv/bin/uvicorn src.main:app --reload --port 8000

# 6. 在另一个终端起前端
.venv/bin/streamlit run frontend/app.py
```

### 试一下

打开 <http://localhost:8501>，点击侧边栏的"数据字典"浏览三层表结构，然后试试这些示例问题：

- 总共有多少注册用户
- 上个月销售额最高的商品品类是什么
- 最近 7 天每天的订单数量趋势
- 金牌用户最喜欢哪个品牌的商品
- 哪个城市的客单价最高

---

## 项目结构

```text
Elytra/
├── docker-compose.yml             # 三服务编排：db + backend + frontend
├── Dockerfile                     # 后端镜像
├── frontend/
│   ├── Dockerfile                 # 前端镜像
│   └── app.py                     # 单文件 Streamlit 应用
├── pyproject.toml                 # uv / pip 依赖 + ruff 配置
├── .env.example                   # API key + 模型 + 检索权重模板
│
├── config/
│   ├── datasources.yaml           # 多数据源注册表（PG / DuckDB / StarRocks）
│   ├── permissions.yaml           # 角色权限配置（analyst / operator / admin）
│   └── overlays/                  # 各数据源的 schema 富化 YAML
│
├── db/
│   ├── init.sql                   # PG 初始化（11 张业务表 + 2 张系统表，含审计字段）
│   ├── migrations/                # 增量迁移脚本（已有库使用）
│   ├── seed_data.sql              # 模拟数据
│   └── data_dictionary.yaml       # 数据字典（中英双语，同时作为 ecommerce_pg 的 overlay）
│
├── datasets/
│   ├── tpch/load_tpch.py          # DuckDB TPC-H 生成器（内置 dbgen）
│   └── brazilian_ecommerce/       # Olist Kaggle CSV → DuckDB 加载脚本
│
├── docker/
│   └── starrocks/                 # 可选 StarRocks docker compose + README
│
├── src/
│   ├── config.py                  # 全局配置（环境变量读取）
│   ├── main.py                    # FastAPI 入口（含 connector lifespan）
│   │
│   ├── models/
│   │   ├── request.py             # QueryRequest（含 source / user_id 字段）
│   │   ├── response.py            # QueryResponse / ReplayResponse / AuditStatsResponse / ...
│   │   ├── state.py               # AgentState（含 active_source / user_id / chart_spec）
│   │   └── task.py                # TaskStatus / TaskSubmitResponse / TaskStatusResponse
│   │
│   ├── connectors/                # 新增：可插拔数据源连接层
│   │   ├── base.py                # DataSourceConnector ABC + 数据类 + 安全过滤
│   │   ├── postgres_connector.py  # asyncpg
│   │   ├── duckdb_connector.py    # 嵌入式 DuckDB
│   │   ├── starrocks_connector.py # aiomysql (StarRocks MySQL 协议)
│   │   ├── factory.py             # dialect → 类，懒导入可选驱动
│   │   ├── registry.py            # 单例 + init_from_yaml + 环境变量展开
│   │   └── overlay.py             # TableMeta + YAML overlay → TableInfo
│   │
│   ├── db/
│   │   ├── connection.py          # psycopg2 上下文管理器（仅服务基础设施 DB）
│   │   └── executor.py            # 兼容 shim（重导出 + 同步 wrapper）
│   │
│   ├── retrieval/
│   │   ├── schema_loader.py       # YAML loader + load_from_connector + per-source 缓存
│   │   ├── bm25_index.py          # CJK + Latin tokenizer + BM25Okapi
│   │   ├── embedder.py            # OpenAI / OpenRouter / 本地三后端 + source 维度索引
│   │   ├── hybrid_retriever.py    # 单源 BM25 + 向量混合
│   │   ├── reranker.py            # LLM-as-Reranker
│   │   └── bootstrap.py           # 多源 bootstrap（--source 单源重建）
│   │
│   ├── auth/
│   │   └── permission.py          # YAML 驱动的角色权限过滤器
│   │
│   ├── tasks/
│   │   └── manager.py             # 内存异步任务管理器（Semaphore 并发控制）
│   │
│   ├── chart/
│   │   ├── inferrer.py            # 规则引擎：结果形状 → 图表类型推断
│   │   └── echarts_builder.py     # ECharts 兼容 JSON 配置生成
│   │
│   ├── agent/
│   │   ├── graph.py               # LangGraph 状态机（10 节点）+ run_agent_async
│   │   ├── llm.py                 # OpenRouter-first chat 调用
│   │   ├── nodes/                 # 10 个节点（含 permission_filter + chart_generator）
│   │   └── prompts/               # intent / sql_generation（含 DIALECT_INSTRUCTIONS） / ...
│   │
│   ├── router/
│   │   └── model_router.py        # 规则引擎：cheap / strong 模型路由
│   │
│   └── api/
│       ├── query.py               # POST /api/query（async，source-aware，含审计持久化）
│       ├── query_async.py         # POST /api/query/async + GET /api/task/{id}
│       ├── ws.py                  # WebSocket /ws/task/{id}（实时进度推送）
│       ├── audit.py               # POST /api/replay/{id} + GET /api/audit/stats
│       ├── schema.py              # GET  /api/schema?source=
│       ├── datasources.py         # GET  /api/datasources
│       └── history.py             # GET  /api/history
│
├── eval/
│   ├── test_queries.yaml          # 14 case 测试集
│   ├── run_eval.py                # 评估 runner
│   └── results/                   # 评估报告输出
│
├── tests/
│   ├── test_connectors.py         # 32 case — 连接器层
│   ├── test_retrieval.py          # 20 case
│   ├── test_agent.py              # 41 case
│   ├── test_api.py                # 16 case
│   ├── test_audit.py              # 9 case — 审计日志 + 回放
│   ├── test_permissions.py        # 17 case — 权限过滤
│   ├── test_tasks.py              # 10 case — 异步任务
│   └── test_chart.py              # 25 case — 图表推断 + ECharts 构建
│
├── assets/                        # 项目 logo
└── README.md
```

---

## 配置说明

所有配置都通过环境变量读取（`.env` 自动加载）。完整列表见 [.env.example](.env.example)。

### LLM Provider（二选一）

| 变量 | 说明 |
|:---|:---|
| `OPENROUTER_API_KEY` | **推荐**。一个 key 路由所有 **chat** 模型，模型名要 `vendor/model` 格式 |
| `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | 旧式 per-vendor key，仅当 OpenRouter key 为空时使用 |

> ⚠️ **OpenRouter 不代理 `/v1/embeddings` 端点**，只能路由 chat completions。
> 因此 schema embedding 必须走"本地 sentence-transformers"或"OpenAI 直连"
> 这两条路之一 —— 详见下方 [Embedding](#embedding三种后端自动选择) 一节。

### 模型

| 变量 | 默认 | 说明 |
|:---|:---|:---|
| `DEFAULT_CHEAP_MODEL` | `deepseek/deepseek-chat` | 简单查询 / 一般聚合 |
| `DEFAULT_STRONG_MODEL` | `anthropic/claude-sonnet-4` | 多表 / 探索 / 连续失败重试 |

### Embedding（两种实际可用的后端）

| 变量 | 行为 |
|:---|:---|
| `EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5` | **默认**。本地 sentence-transformers，512 维，~100MB，无需 API key、无需网络。中文场景召回质量好。需先 `uv sync --extra local-embed` |
| `EMBEDDING_MODEL=text-embedding-3-small` | OpenAI 直连，1536 维。需要单独的 `OPENAI_API_KEY`（OpenRouter key **不行**） |
| `EMBEDDING_PROVIDER` | `auto` (默认) / `openai` / `local` |
| `EMBEDDING_DIM` | 默认 0 = 自动从已知模型查表，不匹配的模型需手动指定 |

> ⚠️ **不要用 `text-embedding-3-large`（3072 维）**：pgvector 的 HNSW 索引有
> 2000 维硬上限，bootstrap 会在 `CREATE INDEX` 处直接失败。
>
> ⚠️ **不要把 `EMBEDDING_MODEL` 设成 `openai/...` 这种带 vendor 前缀的形式**
> 期望走 OpenRouter ——OpenRouter 不支持 `/v1/embeddings`，请求会无限挂到超时。
> 想用 OpenAI 模型就配 `OPENAI_API_KEY` 走直连。

> **切换 embedding 模型后必须重跑 bootstrap**：pgvector 列宽是固定维度的，
> 从 512 维换 1536 维需要 DROP + CREATE。运行 `python -m src.retrieval.bootstrap` 即可。

### 检索 / 自修正

| 变量 | 默认 | 说明 |
|:---|:---|:---|
| `BM25_WEIGHT` | `0.4` | 混合检索 BM25 权重 |
| `VECTOR_WEIGHT` | `0.6` | 混合检索向量权重 |
| `RERANK_TOP_K` | `5` | Reranker 输出的表数量 |
| `MAX_RETRY_COUNT` | `3` | 自修正最大重试次数 |
| `SQL_TIMEOUT_SECONDS` | `30` | 单条 SQL 的 `statement_timeout` |

### 数据源

| 变量 | 默认 | 说明 |
|:---|:---|:---|
| `DEFAULT_SOURCE` | _(空 → 读 YAML)_ | 覆盖 YAML 中的 `default_source`，值必须匹配 `config/datasources.yaml` 中的 `name:` |

`config/datasources.yaml` 自身支持 `${VAR:-default}` 占位符，环境相关的覆盖
建议放在 `.env`：

| 变量 | 用于 | 默认 |
|:---|:---|:---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `ecommerce_pg` 连接块 | `localhost` / `5432` / `Elytra` / `Elytra` / `Elytra_dev` |
| `STARROCKS_HOST` / `STARROCKS_PORT` / `STARROCKS_DB` / `STARROCKS_USER` / `STARROCKS_PASSWORD` | `ecommerce_starrocks` 连接块 | `localhost` / `9030` / `elytra` / `root` / `(空)` |

---

## API 文档

### `POST /api/query`

请求：

```json
{
  "query": "上个月销售额最高的商品品类是什么",
  "session_id": "optional-session-id",
  "source": "ecommerce_pg",
  "user_id": "demo_analyst"
}
```

`source` 可省略，省略时使用 `config/datasources.yaml` 中的 `default_source`。
`user_id` 可省略，省略时使用 `config/permissions.yaml` 中的 `default_role`。
SQL 方言会从 source 背后的 connector 自动派生，旧的 `dialect` 字段保留兼容
但已被忽略。

响应：

```json
{
  "success": true,
  "query": "上个月销售额最高的商品品类是什么",
  "source": "ecommerce_pg",
  "dialect": "postgresql",
  "intent": "aggregation",
  "generated_sql": "SELECT category_l1, SUM(total_amount) AS total_sales FROM dwd_order_detail ...",
  "result": [
    {"category_l1": "电子产品", "total_sales": 1523400.00}
  ],
  "visualization_hint": "bar_chart",
  "final_answer": "查询执行成功，共返回 1 行结果。",
  "model_used": "deepseek/deepseek-chat",
  "retry_count": 0,
  "latency_ms": 1240,
  "token_count": 856,
  "error": null,
  "user_role": "analyst",
  "tables_filtered": 0,
  "chart_spec": {
    "chart_type": "bar",
    "title": "上个月销售额最高的商品品类是什么",
    "x_axis": {"field": "category_l1", "data": ["电子产品"]},
    "y_axis": {"field": "total_sales"},
    "series": [{"type": "bar", "data": [1523400.00]}]
  }
}
```

### `POST /api/query/async`

异步提交查询，立即返回 `task_id`，通过轮询或 WebSocket 获取进度和结果。

请求体与 `POST /api/query` 相同。响应：

```json
{
  "task_id": "a3f7b2c1",
  "status": "pending",
  "ws_url": "ws://localhost:8000/ws/task/a3f7b2c1"
}
```

### `GET /api/task/{task_id}`

轮询异步任务状态（WebSocket 不可用时的降级方案）：

```json
{
  "task_id": "a3f7b2c1",
  "status": "running",
  "current_step": "generating_sql",
  "progress_pct": 60
}
```

### `WebSocket /ws/task/{task_id}`

连接后服务端推送 JSON 事件流：`{"type": "progress", "step": "generating_sql", "pct": 60}`，
任务完成时推送 `{"type": "complete", "status": "success"}` 后关闭连接。

### `POST /api/replay/{history_id}`

从审计日志中取出历史查询，重新执行并对比结果 hash。用于验证模型升级后结果一致性。

### `GET /api/audit/stats?days=7`

返回最近 N 天的查询审计统计（总数、成功率、平均延迟、按模型/意图/数据源/用户分布）。

### `GET /api/datasources`

列出所有已注册的数据源连接器：

```json
{
  "datasources": [
    {
      "name": "ecommerce_pg",
      "dialect": "postgresql",
      "description": "电商模拟数仓 (ODS / DWD / DWS)",
      "connected": true,
      "table_count": 13,
      "is_default": true
    },
    {
      "name": "tpch_duckdb",
      "dialect": "duckdb",
      "description": "TPC-H 标准测试数据集",
      "connected": true,
      "table_count": 8,
      "is_default": false
    }
  ],
  "default": "ecommerce_pg"
}
```

`connected: false` 表示该数据源在启动时无法 ping 通——其余数据源仍然可用。

### `GET /api/schema?source=<name>`

返回单个数据源的 schema，按数仓层（`ODS` / `DWD` / `DWS`，没有层级前缀的进入
`OTHER` 桶）分组。`?source=` 显式指定数据源，省略时使用默认。SYSTEM 层不暴露。

### `GET /api/history?session_id=xxx&limit=20`

按 `session_id` 过滤、按 `created_at desc` 排序的历史查询记录。`limit` 范围 `1..200`。

完整 OpenAPI Schema 见 <http://localhost:8000/docs>。

---

## 评估体系

测试集放在 [`eval/test_queries.yaml`](eval/test_queries.yaml)（14 case 覆盖 5 个类别），评估脚本：

```bash
python eval/run_eval.py
# 或者指定参数
python eval/run_eval.py --api-url http://localhost:8000 --filter aggregation
```

输出会落到 `eval/results/<timestamp>.{json,md}`，markdown 报告含每个指标的 PASS/FAIL 标注、按类别细分、逐 case 详情。

### 验证结果（2026-04-06）

| 指标 | 实际值 | 目标 | 状态 |
|:---|---:|---:|:---:|
| SQL 执行成功率 | 92.9 % | ≥ 85 % | ✅ PASS |
| 结果准确率 | 92.9 % | ≥ 75 % | ✅ PASS |
| Schema 召回率 | 92.9 % | ≥ 80 % | ✅ PASS |
| 平均延迟 | 204 ms | < 5 000 ms | ✅ PASS |
| 自修正成功率 | 50 % (2 retried) | informational | — |

---

## 测试

```bash
# 全部测试
.venv/bin/python -m pytest tests/

# 详细模式
.venv/bin/python -m pytest tests/ -v

# 单个文件
.venv/bin/python -m pytest tests/test_agent.py -v
```

当前 **173 / 173 passing**，约 1.3 秒跑完。覆盖：

- `test_connectors.py`（32 cases）— SQL 安全过滤、数据契约、`ConnectorFactory` 懒加载、`ConnectorRegistry` 单例、overlay 兼容
- `test_retrieval.py`（20 cases）— tokenizer、BM25、min-max 归一化、HybridRetriever 分数融合、向量降级
- `test_agent.py`（41 cases）— SQL 安全过滤、模型路由全分支、10 节点行为、graph 端到端（成功 / 重试 / 耗尽 / 澄清）
- `test_api.py`（16 cases）— `/healthz`、`/api/query`、`/api/datasources`、`/api/schema`、`/api/history`
- `test_audit.py`（9 cases）— `_compute_result_hash` 确定性、回放端点、审计统计
- `test_permissions.py`（17 cases）— 角色解析、表/列过滤、LIMIT 注入/钳制、通配符匹配
- `test_tasks.py`（10 cases）— TaskManager 生命周期、并发控制、事件订阅、异步端点
- `test_chart.py`（25 cases）— 图表类型推断（6 种）、ECharts spec 构建、chart_generator 节点

测试不依赖真实 DB 或 LLM — 通过 stub connector + 内存 registry 完成，本地可秒过。

---

## Roadmap

已完成（v0.2.0）：

- [x] **多数据源抽象层** — `DataSourceConnector` async ABC，PG / DuckDB / StarRocks 三引擎，YAML 驱动配置
- [x] **TPC-H 与 Brazilian E-Commerce 数据集** — DuckDB 内置 dbgen + Kaggle CSV 加载脚本
- [x] **方言自适应 SQL 生成** — `DIALECT_INSTRUCTIONS` 按目标引擎切换语法规则
- [x] **asyncpg 连接池** — agent 热路径全链路 async

已完成（v0.3.0）：

- [x] **异步任务架构** — `POST /api/query/async` + WebSocket 实时进度推送 + `GET /api/task/{id}` 轮询降级
- [x] **权限与多租户隔离** — YAML 配置驱动角色（analyst/operator/admin），表级通配符过滤 + 字段屏蔽 + 行数限制
- [x] **SQL 审计日志与回放** — `query_history` 扩展 9 列审计字段，`POST /api/replay/{id}` 结果一致性验证，`GET /api/audit/stats` 统计面板
- [x] **NL2Chart 自然语言生成图表** — 规则引擎推断 6 种图表类型（number_card / line / bar / pie / scatter / multi_line），输出 ECharts 兼容 JSON，Streamlit 前端自动渲染

下一阶段主要特性：

- [ ] **多轮对话** — `conversation_history` + 上下文摘要 + 指代消解
- [ ] **本地 reranker** — `bge-reranker-v2-m3` 替代 LLM-as-Reranker，加字段级检索
- [ ] **Tool-use Agent** — 升级为 function-calling 模式
- [ ] **可观测性** — 结构化 trace、token 成本追踪、错误分类、prompt 注入加固

---

## 架构决策

### 为什么不用 Celery / Redis 做异步任务？

Elytra 的吞吐瓶颈在 LLM API 的请求速率限制，而非 CPU 或 I/O。单进程 asyncio 任务管理器配合 Semaphore 并发控制即可处理约 50 个并发查询，远超典型 BI 工具的使用模式。引入 Celery 会增加运维复杂度（broker 部署、worker 管理、结果后端），但不会带来有意义的吞吐提升。

如果需要水平扩展，正确做法是：
1. 多 API Key 轮换以突破单 key 速率限制
2. 请求队列按优先级排序（交互查询 > 批量评测）
3. 在结果层面缓存高频查询

当瓶颈在外部 API 调用而非本地计算时，传统的服务分片方案收益为零。

### 权限模型为什么用 YAML 配置而非完整 RBAC？

企业数据平台需要权限管控，但 Elytra 作为分析工具（非平台基础设施），不需要实现完整的 RBAC 系统。YAML 驱动的角色配置具有以下优势：

- **零外部依赖** — 不需要额外的权限数据库或 SSO 集成
- **配置即代码** — 权限变更可纳入 Git 版本控制和代码审查
- **渐进式升级** — 当需要对接 LDAP/SSO 时，只需替换 `PermissionFilter.get_context()` 的实现，接口不变

当前实现支持：表级通配符过滤（`dws_*`）、字段级屏蔽（`denied_columns`）、角色级行数限制（`max_result_rows`）。

---

## 贡献

欢迎贡献！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解开发流程、代码规范和提交约定。

如果你发现了 bug 或有功能建议，欢迎提 [Issue](https://github.com/shuheng-mo/Elytra/issues)。

---

## License

[MIT](LICENSE) © shuheng-mo

---

<div align="center">

<img src="assets/elytra-logo-hex-icon.svg" width="48" alt="Elytra" />

**[⬆ 返回顶部](#elytra)**

</div>
