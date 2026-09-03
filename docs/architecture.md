# 应标 Agent — 架构文档

> 本文件随代码演进持续更新（AI 编码每阶段都需保持 mermaid 与代码一致）。
> 设计说明书（需求/痛点/面试点）见父目录 `README.md`。

## Phase 0（当前）：项目骨架已就绪

- 服务层：FastAPI（`app/main.py`）`/health`、`/`，配置启动即校验。
- 模型层：`llm/` 抽象 + Mock（默认离线可跑）+ DashScope 骨架。
- 配置层：`config/config.yaml` 已预留全量参数 + `config/settings.py` 强类型加载。
- 核心层：`core/parser|ingest|retriever|generator|calculator|qa|reporter` 占位就绪，待逐 Phase 填充。

### 系统分层架构

```mermaid
flowchart TB
  subgraph UI["交互层(Phase7)"]
    S["Streamlit 面板"]
  end
  subgraph API["服务层 FastAPI"]
    E1["/health（Phase0 已通）"]
    E2["POST /tenders/parse（Phase6）"]
    E3["POST /pipeline（Phase6）"]
    E4["GET /reports（Phase6/7）"]
    E5["知识库 CRUD（Phase2/6）"]
  end
  subgraph CORE["核心业务层"]
    P["core/parser 解析引擎（Phase1）"]
    IG["core/ingest 入库分块（Phase2）"]
    RT["core/retriever 混合检索（Phase2）"]
    GN["core/generator 应答生成（Phase3）"]
    CL["core/calculator 数值核对（Phase4）"]
    QA["core/qa 自检质检（Phase5）"]
    RP["core/reporter 报告导出（Phase7）"]
  end
  subgraph LLM["模型层 llm/"]
    LP["LLMProvider 抽象"]
    MK["MockProvider(默认)"]
    DS["DashScopeProvider"]
  end
  subgraph INFRA["基础设施"]
    QD[(Qdrant 本地模式 Phase2)]
    CF["config/config.yaml"]
  end
  S --> API --> CORE
  P --> IG --> QD
  RT --> QD
  CORE --> LP
  LP --> MK
  LP --> DS
  CF -.全局配置.-> CORE
```

### 主流程（一条标跑完）

```mermaid
flowchart LR
  A["上传招标书 PDF + 语料包"] --> B["版面还原 PyMuPDF (Phase1)"]
  B --> C["结构化解析 评分点/★/参数表 (Phase1)"]
  C --> D["评分点速览(用户确认)"]
  D --> E["逐评分点循环"]
  E --> F["构造该评分点的 Query"]
  F --> G["混合检索 向量+BM25→RRF→Rerank (Phase2)"]
  G --> H["应答生成 结构化+引用 (Phase3)"]
  H --> I["数值核对 参数偏离/报价 (Phase4)"]
  I --> J["自检 旧数据检测+Judge (Phase5)"]
  J --> K{"有硬伤?"}
  K -- 是(可自动修) --> L["打回改写(限次)"]
  L --> J
  K -- 超出 --> M["标记需人工 → 风险清单"]
  K -- 否 --> N["汇总 应答包+漏项+待补+风险 (Phase7)"]
  N --> O["导出 Markdown/Word/Excel"]
  M --> N
```

## 运行方式（Phase 0）

```bash
uv run pytest                # 全部测试（离线，不联网）
uv run uvicorn app.main:app --reload   # 起服务
# 浏览器打开 http://127.0.0.1:8000/docs 看接口
```

> 换真实模型：填 `.env` 的 `DASHSCOPE_API_KEY`，把 `config/config.yaml` 的 `llm.provider` 改为 `dashscope` 即可，业务代码零改动。
