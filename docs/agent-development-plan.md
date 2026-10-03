# OpsPilot Agent 开发基线

> 本文是项目范围、约束、里程碑和验收口径的唯一基线。最后更新：2026-10-03。

## 1. 项目目标

OpsPilot 是一个面向电商售后运营的内部 Agent 演示系统。它的目标不是做一个泛聊天机器人，而是在明确业务边界内完成：

```text
用户问题 → 核验制度与订单 → 判断处理路径 → 创建工单或发起人工审批 → 审计留痕 → 运营人员完成处置
```

首要价值是证明 Agent 能在“查信息”和“执行受控业务动作”之间建立可靠闭环。

## 2. 范围

### 本项目要做

- 处理退款/退货、物流异常、电子发票三类售后意图。
- 检索带来源和章节的正式知识资料，并向用户返回引用。
- 查询脱敏的模拟订单数据。
- 创建、指派、流转和关闭工单。
- 对退款等影响用户权益的动作执行人工审批。
- 记录工具调用、审批决定和工单状态变化的审计事件。
- 提供可本地启动的网页演示、API、测试与评测。

### 本项目暂不做

- 真实退款、真实支付、真实订单或生产用户数据接入。
- 自主执行任何资金、账户或地址变更。
- 替代法律、税务或人工客服的最终判断。
- 多 Agent 协作、长期记忆、语音交互和复杂工作流平台。
- 生产 RAG 全量替换、向量数据库服务与 Reranker；阶段 5.1 仅允许本地确定性 Agent 的显式 RAG 检索开关。

## 3. 硬性约束

| 约束 | 要求 |
|---|---|
| 人工审批 | 退款只能生成 `pending` 审批；未经人工批准，系统不得执行或声称执行退款。 |
| 数据隐私 | 只使用虚构、脱敏订单；不得存放真实姓名、完整手机号、支付信息或客户对话。 |
| 可追溯性 | 每条正式知识必须有来源 URL、章节、发布日期、抓取日和适用范围。 |
| 业务边界 | 回答只基于命中的知识和订单数据；证据不足时要求补充信息或转人工。 |
| 安全 | 拦截明显提示词注入；工具层再次校验参数和状态，不能只依赖模型提示词。 |
| 可复现 | 默认模式不能依赖模型密钥；测试、评测和演示应可在本地重跑。 |

## 4. 当前技术决策

| 层 | 当前选择 | 决策原因 |
|---|---|---|
| 服务层 | Python + FastAPI | 轻量，便于演示 API、工具和业务流程。 |
| 数据层 | SQLite | 足以保存演示期的知识块、订单、工单、审批与审计。 |
| 编排层 | LangGraph 单 Agent 状态图 | 显式保存安全检查、证据、决策、业务动作和审计的节点边界；默认无密钥模式也真实运行此图。 |
| 模型接入 | DeepSeek OpenAI-compatible 客户端 + LangGraph / ToolNode | 适配包为 langchain-openai；DeepSeek 环境配置，默认无 Key；公开离线模型注入点验证本地工具边界。 |
| 知识检索 | 默认 candidate 关键词；显式可选 BGE + FAISS | 阶段 5.1 接入证据节点，失败关闭、审计可观测、手动回退；不替换 Store 默认入口。 |
| 前端 | 原生 HTML/JS | 保持本地演示简单，不引入额外构建链。 |

**关于 LangGraph：** 阶段 3 已采用 LangGraph 作为默认编排框架。图保持单 Agent、确定性路由。阶段 5.1 仅为证据节点增加可选本地 Embedding/FAISS，不引入多 Agent、向量数据库服务、Reranker 或 LangSmith。退款、工单和审批的副作用不在模型节点中实现，必须经 Service/Store 的业务校验和审计。

## 5. 统一 Agent 工作流

```text
START
  → GUARDRAIL
  → RETRIEVE_EVIDENCE（政策检索与引用）
  → CLASSIFY_OR_DECIDE（退款 / 物流 / 发票 / 知识问答）
  → ACTION（退款审批 / 物流工单 / 发票工单 / 仅知识回复）
  → FINALIZE_AUDIT（回复、节点、工具、引用与业务 ID）
  → END
```

各阶段职责：

- `GUARDRAIL`：拒绝越权指令和不在服务范围内的操作。
- `RETRIEVE_EVIDENCE`：只返回可追溯的制度依据；订单读取只能经脱敏工具。
- `CLASSIFY_OR_DECIDE`：决定是否需要订单号、是否创建工单、是否必须人工审批。
- `ACTION`：由业务服务执行，模型不能绕过审批或直接改写资金状态。
- `FINALIZE_AUDIT`：写入回复，并记录图节点、工具、引用、审批和工单 ID。

## 6. 开发阶段与完成标准

### 阶段 0：项目定义与风险边界（已完成）

**产物**：本文档、明确场景、约束与成功口径。

**完成标准**：任何人能够说明项目解决什么问题、不会做什么、为什么退款必须人工审批。

### 阶段 1：领域数据与可运行原型（已完成）

**已完成**：84 条正式来源知识块、63 条脱敏虚拟订单、SQLite 数据模型、基础网页与 API。

**完成标准**：本地启动后能查询订单，返回正式资料引用，并且不包含真实个人数据。

### 阶段 2：业务闭环与运营台（已完成，后续按需优化）

**已完成**：退款审批、物流/发票工单创建、工单接单/指派/关闭、审计日志、基础运营指标。

**后续小项**：工单筛选、处理备注时间线、SLA 超时标识、审批历史查看。

**完成标准**：用一次退款或物流异常演示，能够从用户输入一路展示到人工处理和审计记录。

### 阶段 3：基于 LangGraph 的 Agent 编排与工具契约（已完成）

**已完成**：

1. 默认路径迁移为 `START → guardrail → retrieve_evidence → classify_or_decide → action → finalize_audit → END` 的单 Agent LangGraph 图，并将图节点写入审计。
2. 为 `search_knowledge`、`get_order`、`create_ticket`、`request_refund_approval` 固化输入、输出、失败、权限、幂等性和审计契约，见 [`tool-contracts.md`](tool-contracts.md)。
3. 提供 `AGENT_MODE=live` 的 LangGraph 模型节点 + 工具扩展；阶段 6.0 已迁移为 DeepSeek 配置，缺少 `DEEPSEEK_API_KEY` 会明确失败，默认模式无需密钥。
4. 增加默认图经过节点、pending 审批、人工批准后退款工单、物流可追溯工单、注入拦截及 live 工具脱敏/去重的测试。

**完成标准**：默认本地模式可复现且每次经过 LangGraph；live 模式的工具定义可在不调用付费模型的测试中验证。实际模型调用仍须由部署者设置有效 Key 并另行验证；退款无论模式均只能停在人工审批，且结果保留审计证据。

### 阶段 4：检索评测与知识质量（基线与质量检查已完成）

**已完成**：

1. 已建立 `evals/retrieval_cases.jsonl`：24 条中文题，四类来源各 6 条，含来源、章节及标注理由。
2. 已提供 `python evals/run_retrieval_eval.py`，实际调用 Store；来源 Recall@1 为 75.00%、Recall@3 为 87.50%，错误引用率和无依据率均为 12.50%，章节 Recall@3 为 62.50%。
3. 已扫描 84 块的重复、长度、标题与命中覆盖；未发现需要直接改写或删除的明确问题，保留全部语料与元数据。检索算法保持原样作为原始基线，轻量优化留待独立对照。

**产物**：[基线报告](retrieval-baseline.md) 和 `evals/results/retrieval_baseline.json`，含逐题 Top-3、失败原因、四来源指标、质量统计与输入哈希。无依据率是检索代理指标，不是最终回答幻觉率。当前结果证明检索需要改进，尚不能证明向量 RAG 更好；后续需以实测对照判断收益。

### 阶段 4.1：轻量关键词优化与对照（已完成）

- 新增独立开发集 12 题，各来源 3 题；原 24 题及阶段 4 结果保留，固定题只用于本轮最终对照，因前阶段已公开分析，不视为严格盲测。
- `app/retrieval.py` 提供无外部依赖的词法候选；正文/标题/标签/章节加权、短语扩展、稳定排序，无证据允许空结果。
- 固定集来源 Recall@1/3 从 75.00%/87.50% 到 91.67%/95.83%；章节 Recall@3 从 62.50% 到 66.67%；错误引用率从 12.50% 到 0%，无依据率从 12.50% 到 4.17%。
- 已接入默认 Store 检索；baseline 原逻辑保留在 `search_knowledge_baseline`。旧指标复跑输出到 `retrieval_baseline_replay.json`，不覆盖历史文件。无业务权限变更，84 块保持不变。
- [对照报告](keyword-retrieval-comparison.md) 记录全部指标、两题章节退化及适用限制。入口：`python evals/run_keyword_comparison.py`。当前证据不支持直接实施向量 RAG。

### 阶段 4.2：独立新题与检索泛化验证（已完成，严格盲测独立性有限）

- 先根据知识库创建 24 道新正例（四类各 6）及 8 道负例，记录时间与 SHA-256 后才读取评测实现；题集、语料、检索代码冻结校验通过。既有对话含前阶段上下文，不宣称完全未见盲测。
- baseline / candidate 来源 Recall@1 为 62.50% / 79.17%，Recall@3 为 79.17% / 91.67%，章节 Recall@3 为 50% / 75%；错误引用率为 20.83% / 0%，无依据率为 20.83% / 8.33%。
- 负例拒识为 0/8 / 5/8，候选在编程、医疗和系统设置题仍误召回；退款来源有一题退化，不能认为泛化和拒识已全面验收。
- 暂维持当前候选，无检索或业务代码调整；尚不足以证明必须准备向量 RAG 对照。应先增加独立作者新题和难负例。
- 入口：`python evals/run_blind_retrieval_eval.py`；见 [泛化验证报告](retrieval-blind-evaluation.md)、`evals/retrieval_blind_freeze.json` 和 `evals/results/retrieval_blind_comparison.json`。

### 阶段 4.3：独立新题检索验证（流程完成，作者独立性有限）

- 冻结前本轮仅读取两份知识来源，生成24道正例（每来源6题）、12道负例；2026-09-22 冻结题集、语料及检索代码 SHA-256 后再运行评测。历史对话含既有上下文，不宣称完全未见。
- baseline / candidate 来源 Recall@1 为70.83% / 83.33%，Recall@3 为87.50% / 91.67%，章节 Recall@3 为62.50% / 79.17%；错误引用率和无依据率分别均为12.50% / 8.33%。
- 负例拒识为0/12 / 5/12（41.67%），退款来源候选从5/6降至4/6；总体收益不代表拒识可靠。
- 保持检索、语料及旧题旧结果不变。维持演示候选，建议准备受控 RAG 对照来验证语义收益，同时单独衡量拒识；不等于证明 RAG 更好，本轮未实施。
- 入口：`python evals/run_external_retrieval_eval.py`；见 [新题报告](retrieval-external-evaluation.md) 和 `evals/results/retrieval_external_comparison.json`。

### 阶段 5.0：受控语义 RAG 检索对照（已完成）

- 真实本地 `BAAI/bge-small-zh-v1.5`，固定 revision `7999e1d3359715c523056ef9478215996d62a620`，MIT、512 维、CPU、`trust_remote_code=False`；84 块归一化向量持久化为 FAISS 内积索引。
- 仅用原开发集 12 正例及新开发负例 8 条校准阈值 0.49；没有根据 blind/external 回调模型、前缀、参数或阈值。
- blind 的 vector 来源/章节 Recall@3 为 95.83%/87.50%，external 为 100%/83.33%；负例拒识分别 87.50%/83.33%，两套均超过 candidate，但仍有条款混淆及跨域误召回。
- 冻结输入运行前后校验；旧题、旧结果、84 块、`app/retrieval.py`、`app/store.py` 不变。索引校验模型配置、语料、维度、向量文件和权重；普通查询不下载、不重建。
- 入口：`pip install -e ".[rag]"`、`python scripts/build_vector_index.py`、`python evals/run_rag_comparison.py`；结果 `evals/results/rag_comparison.json`，详见 [实验报告](rag-comparison.md)。
- 满足双方来源 Recall@3 不降、章节至少多命中一题、拒识不降的实验门槛，建议下一阶段受控接入验证；**本阶段没有接入 Agent、没有替换 candidate**。

### 阶段 5.1：RAG 受控 Agent 接入验证（已完成）

- `KNOWLEDGE_RETRIEVER` 默认 candidate；只有显式 vector_rag 才在 guardrail 之后加载本地索引。注入拦截不执行检索；请求不下载、不构建、不静默回退。
- 兼容响应字段 `retrieval` 和 `retrieval_completed` 审计含模式、ID、分数/阈值、模型/revision、清单 SHA-256、耗时及空结果；页面显示模式、数量、耗时，不泄露路径。
- RAG 任何初始化/验证/加载/查询错误均 503 关闭，写入脱敏失败审计，零审批/工单副作用；空结果提示补充信息或转人工并跳过业务节点。
- 真实 `python evals/run_agent_rag_smoke.py` 通过 4 项 Service + LangGraph 路径验证；退款仍只建 pending，人工批准接口与工单状态机未修改。
- 没有使用 LLM 生成带引用答案；live + vector_rag 组合本阶段拒绝。默认不需要 RAG 依赖，Store/retrieval 实现、语料、题集/冻结文件和旧结果不变。RAG 对照复跑改写独立的 `rag_comparison_replay.json`。
- 详见 [RAG Agent 接入报告](rag-agent-integration.md)。可准备另行授权的真实 LLM 隔离联调，不应直接把向量模式设为默认。

### 阶段 6.0：DeepSeek Live Agent 联调准备（离线完成，真实联调未进行）

- 默认 `AGENT_MODE=langgraph`，`DEEPSEEK_API_KEY` 留空；Base URL 默认 `https://api.deepseek.com`、模型默认 `deepseek-flash`，按用户指定配置，未验证远端模型可用性。
- live + candidate 保留 LangGraph 和真实 ToolNode；仅四个受控工具，模型调用前 guardrail，缺 Key/配置/客户端错误脱敏 503，live + vector_rag 仍拒绝。
- 公开 `LiveModel` / `live_model_factory` 支持无 Key 的 FakeDeepSeekModel。四工具、退款 pending、人工批准、单运行并发重复幂等、无效工具/直接退款/refund 工单、工具失败、无工具作答、注入和异常脱敏已离线覆盖。
- 模型文本不是审计事实；最终 citation、工单、审批、工具事件来自本地 Service，最终回复按本地结果组装，原模型文字不写 API、回复持久化或审计。
- 入口 `python evals/run_live_agent_mock_smoke.py`，结果明确标注 mock/offline，未调用 DeepSeek API。见 [readiness 报告](deepseek-live-readiness.md)。

### 阶段 6.1：DeepSeek live 受控真实联调（五场景验收通过）

- 建立隔离 `.venv`，安装 dev/live 后 `pip check` 通过；全局包未修改。真实请求使用 Windows 用户环境凭据，未打印、写入文件或记录 Key。
- `deepseek-v4-pro` 在临时数据库中完成知识、脱敏订单、物流工单、发票工单、退款审批五场景，各一次，共 12 次真实模型 HTTP 请求，无重试。模型实际请求工具、ToolNode 结果、引用及本地审计逐项核验；退款仅一个 pending，零退款工单。
- 根据官方工具协议显式关闭思考模式，避免当前兼容客户端不保留 reasoning_content；公开 HTTP 观测上下文只用于本地验收，不修改默认路径、candidate 或 live/vector 503 边界。
- 真实脚本 `evals/run_live_agent_real_smoke.py --allow-real-api` 必须显式启用；首个失败立即取消后续，脱敏结果按时间戳独立保存，含请求数、工具、Token、耗时、费用依据与代码哈希。注入拦截在禁网上下文验证，零模型请求。
- 完整离线回归 130 通过、2 个真实 Embedding 选跑跳过；mock smoke 仍为 16/16。真实五场景通过不代表生产稳定性或自由生成答案质量。详见 [真实验收报告](deepseek-live-acceptance.md)。

### 阶段 6.2：跨请求幂等与失败恢复（离线实现）

- 客户端管理可选 Idempotency-Key，网页失败/刷新复用，同键异内容 409；无键保持旧兼容行为且明确不受保护。
- 新增 SQLite 请求、动作、审批决定账本；原 Store 检索文件及冻结资产不变。复用现有 Store 校验，动作、唯一账本、首次审计同事务提交，迁移不删除旧业务数据。
- Fake 在动作提交后失败时首轮 503 partial，重试只恢复已落库结果；并发、进程重启、崩溃租约接管、过期执行者隔离、人工审批重复操作均以实际数据库和审计验证。
- 仅离线回归，无真实 DeepSeek 请求、无全局环境变更。默认检索与 live/vector 边界保持。有效范围、永久保存策略、300 秒租约和未覆盖情况见 [接口契约](message-api-contract.md)，验收结果见 [阶段报告](idempotency-recovery.md)。

### 阶段 7：本地可复现演示交付（已完成，容器运行待验证）

- 本地显式生成随机 bearer 演示身份，服务端摘要校验；运营处理工单、主管决定退款、审计只读，匿名仍可模拟提问。没有固定密码，不接受自报角色。决定人/负责人采用认证身份。
- 阶段 6.2 请求/动作账本保持，权限回归验证 401/403、同键复用、重复审批 409 及实际数据库/审计记录。
- 提供 Dockerfile、Compose（loopback 端口、命名卷 SQLite、非 root、默认无 Key）、Windows 启动说明、经过验证的依赖约束；不自动下载 BGE。Docker 客户端存在但引擎不可用，仅 Compose 配置校验，未声称容器构建/启动成功。
- 提供无密钥 CI 配置和统一离线检查脚本，未创建远程仓库或运行远程 CI。旧检索结果保持，临时结果改写到被忽略的 .local-results。
- 本机真实 Uvicorn + HTTP 演示链路通过；完整离线测试 152 通过、2 跳过，Fake 16/16、业务 4/4、知识 84 块、依赖与冻结检查通过。本阶段真实模型请求 0。
- README 架构、[权限/API 矩阵](demo-security.md)、[五分钟演示与简历](demo-guide.md)、[交付验收](project-delivery.md) 齐备。结论是本地简历演示完成，不是生产可用。

### 后续：生产化方向（不属于本地演示交付）

**要做**：

1. 展示会话、工具轨迹、引用、耗时、模型 Token 与失败原因。
2. 将本地共享演示凭据升级为个人身份、会话隔离、TLS 与速率限制。
3. 在可用 Docker 主机和经授权的远程仓库执行容器/CI 验证。
4. 完成演示脚本、架构图、项目复盘和简历表述。

**完成标准**：新机器可一键启动；演示能在 5 分钟内完整展示“问题 → Agent → 人工审批 → 工单关闭 → 审计”。

## 7. 当前验收指标

| 指标 | 当前目标 |
|---|---|
| 退款未经审批执行率 | 0 |
| 工单全流程可用性 | 退款、物流、发票三个主场景均可走通 |
| 正式知识可追溯率 | 100%（每块都有来源元数据） |
| 隐私数据 | 0 条真实个人数据 |
| 本地回归测试 | 所有测试和 JSONL 评测通过 |

## 8. 下一步

阶段 7 已完成本地演示交付；保持阶段 6.1 真实联调、6.2 幂等与本次离线验证证据分离。维持 candidate 默认与 live/vector 503 边界；后续生产身份、真实流量答案质量与外部事务恢复另行规划，不将演示验收视为生产验收。
