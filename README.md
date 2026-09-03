# bid-response-rag-engine — 应标 Agent（RAG）

把招标响应从"售前熬夜翻资料"变成流水线：**招标书解析 → 逐评分点检索应答 → 数值核对 → 漏项/风险自检 → 报告导出**。
面向真实投标痛点（★实质性条款漏答=废标、评分点漏项丢分、复用旧标书张冠李戴、参数负偏离），工程级而非 demo。

- 后端 **FastAPI**；技术底座 Python/uv + pydantic v2 结构化输出 + **LLM Provider 抽象**（Mock 离线默认，无 key 全链路可跑）+ 配置外置 YAML + 全链路落盘可审计。
- **一个 Agent 三层防幻觉**：空上下文宁缺毋滥 → 引用编号代码校验 → LLM-as-Judge + 改写闭环限次。

## 快速开始（离线，无 key）

```bash
uv sync
uv run pytest                 # 99 passed，确定性可回归
uv run python -m evals.run    # eval-harness：跑评测 + 阈值门禁 → data/eval/eval_report.{json,md}
uv run uvicorn app.main:app --reload    # 服务层；浏览器 http://127.0.0.1:8000/docs
```

## Eval-Harness（Phase 8）与实测报告

仓库带 **gold 评测集**（解析 6 评分点/★5/参数表 7 行逐点人工标 + 检索证据 + 质检 1 好例 4 坏例）回放整条流水线，出**确定性指标 + 阈值门禁**（防引擎改坏静默回退）。

```bash
uv run python -m evals.run          # mock 确定性基线，五道门禁
```

- 报告（人读 md / 机器读 json）：运行后见 `data/eval/eval_report.{md,json}`（data/ 不入库，随时重跑生成）。
- **实测摘要（离线 mock eval、确定性，非真实投标效果宣称）**：解析评分点 F1=1.0、逐点一致 1.0；混合检索 Recall@5 0.7333 / MRR@5 0.7000、纯向量基线 0.7833 / 0.8000（mock embedding 下混合未显优势，差值如实为负）；质检坏例检出 3/3=1.0、合规草稿 0 误报；端到端 0.155s / 21 LLM calls。
- 设计与口径：见 [`docs/eval.md`](docs/eval.md)。真模型版需实现 DashScope LLM/Embedding Provider 后跑同一命令另测（当前 run.py 对 dashscope 明确拒绝）。

## 文档索引

| 文件 | 内容 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 分层架构 + 主流程 mermaid、运行方式 |
| [`docs/parser.md`](docs/parser.md) · [`docs/retrieval.md`](docs/retrieval.md) · [`docs/generator.md`](docs/generator.md) | Phase 1–3：解析 / 入库与混合检索 / 应答生成 |
| [`docs/calculator.md`](docs/calculator.md) | Phase 4：数值核对（确定性计算，能算的不让模型算） |
| [`docs/qa.md`](docs/qa.md) | Phase 5：自检质检三层防线 + LLM-as-Judge + 改写闭环 |
| [`docs/api.md`](docs/api.md) · [`docs/report.md`](docs/report.md) | Phase 6/7：API 服务层 / 报告导出 |
| [`docs/eval.md`](docs/eval.md) | Phase 8：eval-harness 评测集/指标/门禁/局限 |

## 目录速览

```
app/       FastAPI 服务层（任务编排/报告路由）
core/      parser·ingest·retriever·generator·calculator·qa·reporter
evals/     eval-harness（dataset·adversarial·metrics·harness·run）
fixtures/  样例招标书 PDF + 自备语料（评测/演示底座）
llm/       Provider 抽象：Mock（默认离线）+ DashScope（骨架）
tests/     99 条（离线确定性）
```

## 设计取舍（一句话）

- **能算的绝不让模型算**：数值核对、指标、引用编号校验、覆盖扫描都是确定性代码，LLM 只在抽取/生成上出力。
- **评测集 gold 人工策展、绝不拿模型输出当 gold**；坏例期望=注入语义；指标=确定性代码计数。
- **诚实边界**：离线 mock 评测 ≠ 真实投标效果；真模型版（多标/真实脱敏标书）评测是后续，数字不提前宣称。
