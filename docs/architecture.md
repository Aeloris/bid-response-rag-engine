# 应标 Agent — 架构文档

> 本文件随代码演进持续更新（AI 编码每阶段都需保持 mermaid 与代码一致）。
> 设计说明书（需求/痛点/面试点）见父目录 `README.md`。

## Phase 5（当前）：自检质检就绪

- 服务层：FastAPI（`app/main.py`）`/health`、`/`，配置启动即校验。
- 模型层：`llm/` 抽象 + Mock（默认离线可跑）+ DashScope 骨架。
- 配置层：`config/config.yaml` 已预留全量参数 + `config/settings.py` 强类型加载（embedding/rerank/generator 默认 mock）。
- 解析层：`core/parser/` 双通道（规则锚点 + LLM 抽取）已实现，PDF→`TenderDoc`+`ParseReport`。详见 [`docs/parser.md`](parser.md)。
- 检索层：`core/ingest`(切块入库) + `core/embeddings`(Provider化) + `core/vector_store`(qdrant本地) +
  `core/retriever`(Dense+BM25→RRF→Rerank) 已实现。详见 [`docs/retrieval.md`](retrieval.md)。
- 生成层：`core/generator/` 逐评分点检索→限量上下文→批量结构化应答→三层幻觉控制（prompt 约束/空上下文 gap/引用编号代码校验）已实现。
  详见 [`docs/generator.md`](generator.md)。
- 数值核对层：`core/calculator/` 招标数值要求 × 我方能力 → 逐条偏离判定（纯代码计算器，能算的绝不让模型算；
  数字陷阱防误抽 + 单位归一 + 三态一灰 + ★负偏离记账）。详见 [`docs/calculator.md`](calculator.md)。
- 自检质检层：`core/qa/` 三层防线收口 —— 代码判三路（覆盖率/数值偏离复核/数值自洽·超承诺）+
  LLM-as-Judge（旧数据/不实/答非所问，防御校验）+ 改写闭环限次 → QaReport 风险清单
  （BLOCK 即 escalation）。详见 [`docs/qa.md`](qa.md)。
- 占位待填：`core/reporter`（Phase 7）。

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

## 运行方式（Phase 5）

```bash
uv run pytest                # 全部测试（离线，不联网，71 passed）
uv run python scripts/make_tender_fixture.py   # 重新生成样例招标书 PDF fixture
uv run python - <<'PY'       # 一条标：解析 → 入库 → 逐点应答 → 数值核对 → 自检质检（demo）
import asyncio
from pathlib import Path
from config.settings import get_settings
from core.calculator import Calculator, extract
from core.generator import Generator
from core.ingest import ingest_corpus
from core.parser.pipeline import parse_tender
from core.qa import QaService
from core.retriever import Retriever
from core.vector_store import VectorStore
from llm.mock_provider import MockProvider
async def main():
    s = get_settings(); llm = MockProvider(s)
    doc, _ = await parse_tender("fixtures/tender_sample.pdf", llm)     # ① 解析：评分点+参数表+★
    st = VectorStore(s.vector_db.collection, s.embedding.dimension, path=":memory:")
    await ingest_corpus("fixtures/corpus", st, s)
    answers, gsum = await Generator(s, llm).generate(                   # ② 应答生成（Phase3）
        doc.score_points, Retriever(s, st).retrieve, doc.tender_title)
    print("GEN", gsum)
    offers = []                                                         # ③ 数值核对（Phase4）
    for f in ["product-guide.md", "qualifications-and-service.md", "cases.md"]:
        offers += extract.from_text((Path('fixtures/corpus') / f).read_text(encoding='utf-8'), f)
    checks, csum = Calculator(s).check(extract.from_tender_doc(doc), offers)
    print("CALC", csum.model_dump())
    # ④ 自检质检（Phase5）：三路代码判 + Judge + 改写闭环
    qrep, _ = await QaService(s, llm).run(points=doc.score_points, answers=answers, checks=checks,
        offers=offers, tender_title=doc.tender_title, buyer=doc.buyer, deadline=doc.deadline)
    print("QA", qrep.block_count, qrep.warn_count, qrep.info_count, qrep.escalation_required)
    for i in qrep.issues:
        print("  -", i.severity.value.upper(), i.kind.value, i.point_id, "|", i.reason[:70])
asyncio.run(main())
PY
uv run uvicorn app.main:app --reload   # 起服务
# 浏览器打开 http://127.0.0.1:8000/docs 看接口
```

> 换真实模型：填 `.env` 的 `DASHSCOPE_API_KEY`，把 `config/config.yaml` 的 `llm.provider` 改为 `dashscope` 即可，业务代码零改动。
