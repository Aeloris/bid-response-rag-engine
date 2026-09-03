# bid-response-rag-engine — 应标 Agent（RAG）

面向企业投标的招标响应 Agent：把"资深售前逐条人工比对"变成一条可离线复现的流水线
**招标书解析 → 逐评分点检索应答 → 数值核对 → 漏项/风险自检 → 报告导出**。

> 不是"把标书丢给 GPT"的对话 demo：面向真实投标痛点，工程级实现，自带 **eval-harness 评测与回归门禁**。

## 为什么做（真实痛点）

| 痛点 | 后果 |
|---|---|
| ★实质性条款漏答 / 负偏离 | 评标直接判**无效标**，整标作废 |
| 评分点漏项 | 白丢几分，竞争激烈时决定中标与否 |
| 复用旧标书错写采购人/项目/工期（张冠李戴） | 印象分崩塌甚至被判无效 |
| 参数不核对（承诺 ≥400 万像素，产品 300 万） | 负偏离、废标级风险 |
| 每份标书数百页、资料散落 | 资深售前人工比对一份标动辄数天 |

## 主流程（一张图看懂）

```mermaid
flowchart LR
  A[招标书 PDF] --> B[解析：评分点/★/参数表]
  C[公司语料] --> D[入库分块]
  B --> E[逐评分点独立检索<br/>Dense+BM25→RRF→Rerank]
  D --> E
  E --> F[应答生成<br/>结构化+引用[R#]]
  B --> G[数值核对<br/>确定性计算偏离判定]
  F --> H[自检质检三层防线<br/>覆盖率/偏离复核/自洽 + LLM-as-Judge]
  G --> H
  H --> I[报告：风险清单/逐点应答/核对/待补材料]
  E --> H
```

## 特性

- **解析**：PyMuPDF 还原版面 + "章节锚点规则 + LLM 抽取"双通道 → pydantic 强类型 `TenderDoc`（评分点/★实质性条款/技术参数表/时间线）。
- **混合检索**：标题层级感知分块保留 来源+章节 元数据；向量 + BM25 → RRF 融合 → 重排；每评分点独立 Top-k，防点间互相污染。
- **应答生成**：三层幻觉控制——① 提示词硬约束只许引用给定块 ② 空上下文宁缺毋滥标"需人工" ③ 引用编号 R# 代码校验，出处用索引元数据回填。
- **数值核对**：`能算的绝不让模型算`——参数表偏离判定是确定性代码（单位归一 / 数字陷阱防误抽 / ★负偏离记账），规避模型对数字"凭感觉口算"。
- **自检质检**：三层防线收口（★/普通点覆盖率、偏离复核、数值自洽·超承诺）+ LLM-as-Judge 判语义风险（张冠李戴/不实/答非所问）+ 改写闭环限次；BLOCK=废标级风险直接 escalation。
- **报告导出**：报告 = 已算好产物的**纯派生视图**（绝不重跑引擎）；HTML 报告页 + Markdown + Excel 多出口。
- **可评测**：`evals/` eval-harness，带 gold 评测集回放 + 确定性指标 + 阈值门禁（详见下文实测）。

## 快速开始（离线 · 无需任何 API key）

```bash
uv sync                      # 装依赖（Python 3.11/3.12）
uv run pytest                # 99 passed，离线确定性

# ① eval-harness：评测回放 + 阈值门禁
uv run python -m evals.run   # → data/eval/eval_report.{json,md}，五道门禁全过 exit=0

# ② 服务层 API（一条标 = 一个 job，五步流水线）
uv run uvicorn app.main:app --reload    # 浏览器 http://127.0.0.1:8000/docs
curl -s -F "file=@fixtures/tender_sample.pdf" http://127.0.0.1:8000/tenders/parse   # 评分点速览
curl -s -F "file=@fixtures/tender_sample.pdf" http://127.0.0.1:8000/tasks          # 跑完整条标
curl -s http://127.0.0.1:8000/tasks/{JOB_ID}/result   # 产物：gen/calc/qa + needs_material + escalation
# ③ 报告（Phase 7）：目录页 → HTML 报告页 → 导出 md/xlsx
curl -s http://127.0.0.1:8000/reports/{JOB_ID}
curl -s -o report.md  http://127.0.0.1:8000/reports/{JOB_ID}/export?fmt=md
```

> 换真实模型：复制 `.env.example` 为 `.env` 填 `DASHSCOPE_API_KEY`，把 `config/config.yaml` 的 `llm.provider` 改 `dashscope`，业务代码零改动。
> DashScope LLM/Embedding Provider 当前为骨架：`evals.run` 会明确拒绝 dashscope 模式（见下文口径），需先实现 Provider 再切真模型评测。

## 实测与评测（诚实口径）

评测 = **带 gold 的评测集**回放整条流水线（解析 6 评分点/★5/参数表 7 行逐点人工标 + 检索证据 + 质检 1 好例 4 坏例），指标全部是**确定性代码计数**。运行 `uv run python -m evals.run` 生成 `data/eval/eval_report.md`（data/ 不入库，随时重跑）。

| 维度 | 实测（离线 mock · 确定性基线） |
|---|---|
| 解析 | 评分点 P/R/F1 = **1.0/1.0/1.0**，逐点(分值,★)一致率 **1.0** |
| 检索 Recall@5 / MRR@5 | 混合检索 **0.7333 / 0.7000**；纯向量基线 0.7833 / 0.8000（**mock embedding 下差值如实为负 → 不宣称"混合更优"**） |
| 质检 | 坏例检出 **3/3 = 1.0**（★负偏离/漏答/超承诺，代码判确定性）；合规草稿 **0** 误报 |
| 性能 | 样例标端到端 **0.155s**，21 次 LLM 调用（离线 mock 墙钟） |
| 门禁 | 五道全过（解析 F1≥0.9 / 混合 R@5≥0.6 / MRR≥0.6 / 检出率=1.0 / 误报=0），`99 tests` 绿 |

> **口径声明**：以上数字只对「本评测集 + mock provider」成立，是防引擎改坏静默回退的回归基线，**不是对真实投标效果的宣称**。需 LLM 语义判断的坏例（张冠李戴）mock 下诚实门控跳过、不计入检出率。真模型版（含真实脱敏多标评测）是后续工作，`README`/简历引用时请保留本口径。详见 [`docs/eval.md`](docs/eval.md)。

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 分层架构 + 主流程 mermaid、运行方式 |
| [`docs/parser.md`](docs/parser.md) · [`docs/retrieval.md`](docs/retrieval.md) · [`docs/generator.md`](docs/generator.md) | Phase 1–3：解析 / 入库与混合检索 / 应答生成 |
| [`docs/calculator.md`](docs/calculator.md) | Phase 4：数值核对（确定性计算） |
| [`docs/qa.md`](docs/qa.md) | Phase 5：自检质检三层防线 + LLM-as-Judge + 改写闭环 |
| [`docs/api.md`](docs/api.md) · [`docs/report.md`](docs/report.md) | Phase 6/7：API 服务层 / 报告导出 |
| [`docs/eval.md`](docs/eval.md) | Phase 8：eval-harness 评测集/指标/门禁/局限 |

## 目录速览

```
app/       FastAPI 服务层（任务编排/报告路由）
core/      parser · ingest · retriever · generator · calculator · qa · reporter
evals/     eval-harness（dataset · adversarial · metrics · harness · run）
fixtures/  样例招标书 PDF + 自备语料（评测/演示底座）
llm/       Provider 抽象：Mock（默认离线）+ DashScope（骨架）
tests/     99 条（离线确定性）
config/    config.yaml 全量参数外置 + settings.py 强类型加载
```

## 工程取舍

- **能算的绝不让模型算**：数值核对、评测指标、引用编号校验、覆盖扫描全部确定性代码；LLM 只在抽取/生成/语义复审上出力。
- **Provider 抽象 + Mock 默认**：无 API key 也能全链路跑、测试确定性；换真模型只改 config 不动业务代码。`fail-fast` 配置：缺 key/坏配置启动即报。
- **一条标 = 一个 job，产物落盘**：`data/jobs/{id}/` 存 input + state + 每步引擎产物 JSON = 审计(错在哪步) + 可复现(eval 回放) + 报告纯派生读它。
- **API 不吞引擎的诚实信号**：gap/UNKNOWN/BLOCK/needs_material 是结果不是失败；只有代码真异常才 failed 并精确到段。
- **LLM-as-Judge 结果不可信，必须被代码防一道**：类别白名单 / reason 非空 / 严重级按类别映射而非模型自报。金句："LLM 是提出怀疑的人，代码是决定信不信的人。"

## 明确不做（v1 边界）

- 不自动报价（价格分需我方真实报价，语料无报价能力 → 标"需人工"单列）。
- 不自建第二个服务/前端框架（报告页由 FastAPI 同进程出 HTML）。
- 真实模型版评测、真实脱敏多标评测集、Word 导出为后续工作，指标未测前不宣称。

## License

[MIT](LICENSE)。
