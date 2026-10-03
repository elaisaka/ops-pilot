# OpsPilot：可审计企业知识库与工单处置 Agent

一个面向电商售后运营的 Agent MVP。它不是“能回答问题的聊天框”，而是一个有业务边界的闭环：检索制度 → 查询订单 → 创建工单或发起人工审批 → 记录可审计轨迹。

**阶段 7：本地简历演示版。不是生产系统。** 默认无 Key 的 LangGraph + candidate，使用模拟订单，不进行真实资金操作。本阶段零真实模型调用；历史 DeepSeek 验收仅阶段 6.1 的五场景（12 次模型请求），不能据此宣称线上稳定性。

入口：[5 分钟演示与简历表述](docs/demo-guide.md) · [角色/API 权限表](docs/demo-security.md) · [交付验收与未验证项](docs/project-delivery.md)。

项目范围、硬性约束、技术决策和阶段计划见 [Agent 开发基线](docs/agent-development-plan.md)。
受限业务工具的输入、输出与安全边界见 [工具契约](docs/tool-contracts.md)。

## 已实现的业务闭环

- **真实知识库与引用**：内置 84 个可追溯知识块，覆盖退款退货、电商交易、快递服务、数电发票四类正式资料；每次匹配都会返回文档 ID、章节、来源网址和抓取日。
- **订单工具**：仅返回脱敏后的客户数据，使用内置的模拟订单系统。
- **退款审批**：任何退款请求只能生成 `pending` 审批；只有认证主管批准后才创建退款工单。
- **工单路由与流转**：物流异常生成高优先级工单；发票申请生成中优先级工单；工单可接单、指派负责人、填写处理结果后关闭。
- **运营面板**：内置 63 条脱敏虚拟订单，展示订单池、待审批数、工单状态和知识块总数。
- **LangGraph 编排与审计**：默认无密钥模式也会执行单 Agent 状态图；每次工具调用、节点流转、审批和工单创建都会记录，基础提示词注入尝试会被阻断。
- **评测**：提供真实服务路径上的本地 JSONL 用例，验证引用、审批、工单与安全行为。

## 架构

```text
浏览器 UI / REST API
        │
        ├─ 运营/主管/审计凭据 → FastAPI 角色校验 → 受控工单/审批/审计接口
        │
        ├─ Idempotency-Key → SQLite 请求/动作账本（跨请求防重、失败恢复）
        │
        ▼
  LangGraph single-agent state graph
        │
        ▼
 START → guardrail → retrieve_evidence → classify_or_decide
                                      │
                 ┌────────────────────┼─────────────────────┐
                 ▼                    ▼                     ▼
       request_refund_approval  create delivery/invoice   knowledge reply
                 │                    │                     │
                 └────────────────────┴──────────┬──────────┘
                                                   ▼
                                      finalize_audit → END
                                                   │
                                                   ▼
      OpsPilot Service / Store (masked orders, pending approvals, tickets, audit_events)
```

## 正式知识源

知识块位于 [`knowledge/official_sources.jsonl`](knowledge/official_sources.jsonl)，来源注册表在 [`knowledge/source_registry.json`](knowledge/source_registry.json)。它们是带来源元数据的结构化归纳，不是抓取商业客服语料。当前包含：

- [市场监管总局：网络购买商品七日无理由退货暂行办法](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_26ca8fe29e184edd899fa0a7a060d935.html)
- [市场监管总局：中华人民共和国电子商务法](https://www.samr.gov.cn/zfjcj/tzgg/art/2023/art_d337c3291e8b40459ca03dea54395856.html)
- [司法部国家行政法规库：快递暂行条例（2025 年修订）](https://xzfg.moj.gov.cn/front/law/detail?LawID=1768&Query=)
- [国家税务总局：数电发票全国推广说明](https://www.chinatax.gov.cn/chinatax/n810356/n3010387/c5236173/content.html)

每个知识块均保存 `source_url`、`source_published_at`、`source_fetched_at`、`section` 和 `scope`。可通过 `GET /api/knowledge` 查看，也可在回答下方点击引用直达原始页面。它们用于信息检索和演示，并不构成法律或税务意见；上线前应配置定期复核与版本更新流程。

如需更新已提交的知识块，可运行 `python scripts/build_official_corpus.py`；该脚本会从已核对的条款归纳生成 84 条 JSONL 记录，不会联网抓取网页。

## 快速启动

建议 Python 3.13（本机已验证版本）。依赖约束记录在 constraints-demo.txt；只安装演示依赖，不下载 BGE。以下命令显式覆盖继承的 live 环境变量，`.env.example` 不会自动加载。

```powershell
cd E:\agent\ops-pilot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c constraints-demo.txt -e ".[dev,live]"
$env:AGENT_MODE = 'langgraph'
$env:KNOWLEDGE_RETRIEVER = 'candidate'
$env:DEEPSEEK_API_KEY = ''
python scripts/create_demo_accounts.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`，可尝试：

```text
订单 ORD-10001 我想退款，商品有问题
订单 ORD-10002 物流三天没有更新
订单 ORD-10001 申请电子发票
```

提问不要求登录。管理面板需将本机 `data/demo-credentials.json` 中对应的随机凭据粘贴到页面登录框。运营 operator 处理工单，主管 supervisor 决定退款，auditor 只读审计。凭据不在页面持久化，刷新后重新登录；请求幂等标识仍保留在同标签页会话存储。初始化脚本不会覆盖已有账号，不输出凭据。不要提交/分享 data 文件夹。

### Docker Compose（本机引擎不可用，尚未实测容器启动）

```powershell
docker compose up --build -d
docker compose exec ops-pilot python scripts/create_demo_accounts.py
docker compose cp ops-pilot:/opt/ops-pilot/data/demo-credentials.json ./data/demo-credentials.json
```

复制前创建本地 `data` 文件夹，避免覆盖已有本地凭据；也可复制到其他受保护位置。Compose 只监听本机 127.0.0.1，命名卷保存 SQLite/演示身份。镜像只装核心依赖，不含 live/RAG，不传 Key、不下载模型；账号必须显式初始化。`docker compose down` 保留卷，**不要加 `-v`**，否则业务与幂等账本、账号会被删除。`docker compose config --quiet` 已验证；Docker Desktop 引擎未运行，镜像构建/运行尚未验证。首次构建需要联网下载 Python 镜像和包，与模型调用不同。

### 一键离线验收

```powershell
.\.venv\Scripts\python.exe scripts/check_offline.py
.\.venv\Scripts\python.exe scripts/run_demo_smoke.py
```

第一条运行依赖检查、完整 pytest、Fake smoke、本地业务 smoke、知识校验与冻结哈希校验，使用临时数据库，结果写 `.local-results/stage7`，不覆盖历史结果。第二条实际启动本地 Uvicorn，通过 HTTP 验证完整角色/业务演示后退出。可选 live 适配包是为了运行 Fake 测试，不代表真实调用。自动测试配置位于 `.github/workflows/offline.yml`，没有远程仓库或远程执行记录。

## 验证

```powershell
python -m pytest -q
python evals/run_local.py
python evals/run_retrieval_eval.py
python evals/run_keyword_comparison.py
python evals/run_blind_retrieval_eval.py
python evals/run_external_retrieval_eval.py
python scripts/validate_knowledge.py
```

评测结果写入 `evals/results/latest.json`，没有密钥也可复现。

阶段 4 的原始 [基线结果](evals/results/retrieval_baseline.json) 保持不变，24 题的原始来源 Recall@1/3 为 75.00%/87.50%，章节 Recall@3 为 62.50%。旧算法已提取为 `Store.search_knowledge_baseline`，`run_retrieval_eval.py` 将复跑结果写入独立的 `retrieval_baseline_replay.json`；原始口径见 [基线报告](docs/retrieval-baseline.md)。

阶段 4.1 已完成：新增四类各 3 题的开发集，默认检索采用独立的轻量词法候选（正文加权、短语同义词、稳定排序及空结果）。固定 24 题来源 Recall@1/3 为 91.67%/95.83%，章节 Recall@3 为 66.67%，错误引用率 0%、无依据率 4.17%。对照见 [`keyword_comparison.json`](evals/results/keyword_comparison.json) 和 [对照报告](docs/keyword-retrieval-comparison.md)，其中明确记录两题章节退化。84 块及其来源未修改，未引入 RAG。

阶段 4.2 已完成冻结新题泛化检查：24 道正例（四类各 6）和 8 道负例。候选来源 Recall@3 为 91.67%、章节 Recall@3 为 75%，但负例拒识仅 62.5%，不能认为拒识稳定。本轮未调整检索或语料，暂维持候选，未进入 RAG。冻结过程、既有对话上下文导致的盲测局限、逐项对照见 [泛化验证报告](docs/retrieval-blind-evaluation.md)，结果见 [`retrieval_blind_comparison.json`](evals/results/retrieval_blind_comparison.json)。

阶段 4.3 已完成先冻结后评测的新题验证（24 正例、12 负例；同一对话历史暴露限制严格独立性）。候选来源 Recall@3 为 91.67%、章节 Recall@3 为 79.17%、负例拒识为 41.67%；维持演示方案，建议准备受控 RAG 对照但尚未实施。检索代码和语料不变，详见 [独立新题报告](docs/retrieval-external-evaluation.md) 与 [结果](evals/results/retrieval_external_comparison.json)。

## 阶段 5.0：可选本地语义检索对照

已完成真实 Embedding + FAISS 检索层实验。阶段 5.0 当时未接入 Agent；阶段 5.1 已提供下述显式开关，**默认仍是 candidate 关键词检索**。不需要 API Key，不运行 LLM，不使用混合召回或 Reranker。

```powershell
pip install -e ".[rag]" # 若 pip 不在 PATH，可用 python -m pip
$env:RAG_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
$env:RAG_MODEL_CACHE = ".rag-models"
$env:RAG_INDEX_PATH = ".rag-index"
python scripts/build_vector_index.py # 首次联网下载固定 revision；显式构建/重建及开发集阈值校准
python evals/run_rag_comparison.py    # 只读本地缓存与索引；不自动下载或重建
```

上述变量可省略以使用项目目录下的默认路径；脚本不自动加载 `.env`。只支持已核对的 BGE-small-zh-v1.5 配置，换模型必须重新审核配置并构建索引。模型缓存、FAISS 索引及其清单均不提交仓库。

普通 `python -m pytest -q` 不下载模型。显式运行真实缓存集成测试：

```powershell
$env:RUN_RAG_INTEGRATION = "1"
python -m pytest tests/test_rag_retrieval.py -q
Remove-Item Env:RUN_RAG_INTEGRATION
```

开发集阈值为 0.49。vector 在 blind/external 的来源 Recall@3 为 95.83%/100%，章节 Recall@3 为 87.50%/83.33%，负例拒识为 87.50%/83.33%。达到下一阶段受控接入的实验门槛，但不等于生产安全验收。完整模型、失败案例、延迟和局限见 [RAG 对照报告](docs/rag-comparison.md)，阶段 5.0 逐题输出保留为 `evals/results/rag_comparison.json`，之后复跑写入独立的 `rag_comparison_replay.json`。旧题、旧结果、84 块语料、默认检索及业务权限保持不变。

## 阶段 5.1：受控 RAG 接入 Agent

不设置 `KNOWLEDGE_RETRIEVER`（或设置为 `candidate`）时，服务仍走关键词检索，不需要 RAG 依赖、模型或索引。显式启用：

```powershell
pip install -e ".[rag]"
python scripts/build_vector_index.py # 仅此显式命令允许下载/构建
$env:AGENT_MODE = "deterministic"
$env:KNOWLEDGE_RETRIEVER = "vector_rag"
python evals/run_rag_comparison.py
python evals/run_agent_rag_smoke.py
python -m uvicorn app.main:app --reload
```

请求经过 guardrail 后才加载本地模型与索引。RAG 空结果会要求补充信息或转人工，并跳过审批/工单动作。依赖、模型缓存、索引或检索失败时返回 **503「RAG 检索不可用」**，只记录脱敏错误，不自动回退、不继续执行业务动作；管理员应修复配置、显式重建索引，或手动切回：

```powershell
$env:KNOWLEDGE_RETRIEVER = "candidate"
python -m uvicorn app.main:app --reload # 停止旧服务后用新环境重启
```

更改 `.env.example` 或另一个终端的环境不会改变已经运行的服务；请在启动服务的终端设置变量并重启。健康检查不预加载 RAG，也不证明索引就绪。

回复新增兼容字段 `retrieval`，页面显示“关键词检索 / 向量 RAG”、命中数量和耗时；注入拦截显示“本次未执行检索”。审计事件包含来源 ID、相似度、阈值、模型/revision、清单 SHA-256、耗时与空结果状态，不展示本地路径。当前使用固定模板/知识摘录回复，**没有使用 LLM 生成带引用答案**；`AGENT_MODE=live` 与 `vector_rag` 组合明确返回 503，待后续另行联调。

真实 smoke 使用临时数据库，覆盖法规知识、物流工单、pending 退款审批和无关问题拒识，结果为 `evals/results/agent_rag_smoke.json`。架构、失败策略及推广条件见 [接入验证报告](docs/rag-agent-integration.md)。

## 阶段 6.0：DeepSeek live 离线准备


默认 `AGENT_MODE=langgraph` 适合无密钥演示，并真实经过 LangGraph 图。live 使用 DeepSeek 的 OpenAI-compatible 协议；`langchain-openai` 仅为协议适配器。Key 保持空值，缺少 `DEEPSEEK_API_KEY` 时普通 live 请求返回脱敏 503，不初始化模型、不创建审批或工单。注入输入优先被 guardrail 拦截。

`.env.example` 配置为：

```dotenv
AGENT_MODE=langgraph
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
KNOWLEDGE_RETRIEVER=candidate
```

未来用户自行填写 Key 后，启动服务前设置：

```powershell
pip install -e ".[live]"
$env:DEEPSEEK_API_KEY = "..."
$env:AGENT_MODE = "live"
$env:KNOWLEDGE_RETRIEVER = "candidate"
```

Base URL 和模型名未设置时分别使用 `https://api.deepseek.com` 和用户指定的 `deepseek-flash`。本阶段只支持 live + candidate，live + vector_rag 继续 503。`/health` 返回脱敏的 `llm_provider`、`live_ready` 和 `knowledge_retriever`；live_ready 只是本地配置检查，不是连通性证明。

无需 Key 的离线验收：

```powershell
python -m pytest -q
python evals/run_live_agent_mock_smoke.py
```

FakeDeepSeekModel 通过公开的 `create_app(..., live_model_factory=...)` / Service 注入点运行真实 live 图、ToolNode 与业务方法，不读取 Key、不联网。模型仅提出工具调用；最终业务消息、引用、工单、审批和工具事件来自本地结果，未采用模型原文作为事实。mock smoke 明确标注“mock/offline，未调用 DeepSeek API”，详见 [离线 readiness 报告](docs/deepseek-live-readiness.md)。阶段 6.0 仅提供离线验证；后续真实结果见下节。

## 阶段 6.1：DeepSeek 受控真实联调

已在独立 `.venv` 安装 `.[dev,live]` 并通过 `pip check`，没有修改全局包。`deepseek-v4-pro` 非思考模式的知识、脱敏订单、物流、发票、退款审批五场景通过，实际发起 12 次模型 API 请求。每项均核验模型实际工具调用、ToolNode 结果、引用及数据库审计；退款只生成 pending。默认代码仍是 langgraph + candidate，live + vector_rag 仍返回 503。

真实脚本必须显式启用，复跑会产生模型费用。使用已配置的 Windows 用户凭据，不回显或写入文件：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,live]"
.\.venv\Scripts\python.exe -m pip check
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
$env:DEEPSEEK_MODEL = 'deepseek-v4-pro'
$env:AGENT_MODE = 'live'
$env:KNOWLEDGE_RETRIEVER = 'candidate'
.\.venv\Scripts\python.exe evals/run_live_agent_real_smoke.py --allow-real-api
```

每场景只尝试一次，失败即停止后续场景；使用临时 SQLite，结果按时间戳新建。省略启用参数时不读取 Key、不发请求。客户端显式关闭思考模式以满足当前兼容适配器的工具往返协议。实际工具、Token、延迟、费用估算、离线回归和剩余限制见 [真实验收报告](docs/deepseek-live-acceptance.md)。

上线前应将 SQLite 替换为 PostgreSQL/pgvector，将内置订单数据替换为有权限控制的业务 API，并接入身份认证、速率限制和更完整的评测集。

## 阶段 6.2：跨请求幂等与失败恢复

消息入口支持可选 `Idempotency-Key`；页面发送前保存标识，失败或刷新后“重试原提交”复用原消息与标识。同会话同键不同消息返回 409；不同键属于新请求。不传标识兼容旧客户端，但不提供跨请求防重。

新增 SQLite 请求与动作账本，不清空已有业务表。业务动作、唯一账本和首次审计同事务提交，支持并发与进程重启防重。动作落库后模型失败返回明确的部分失败 503；同键重试直接恢复已存工单/pending 审批与真实引用，不调用模型、不重复创建、不声称资金退款。人工审批重复操作仍 409。

账本永久保留；崩溃请求需等待 300 秒租约到期后接管。没有自动补办未完成模型计划，后续需转人工。默认 langgraph + candidate、live + vector_rag 的 503 边界不变。本阶段仅离线 Fake 验证，零真实模型请求、无全局包修改。

使用项目 `.venv` 运行 `python -m pip check`、`python -m pytest -q`、`python evals/run_live_agent_mock_smoke.py` 和 `python evals/run_local.py`。详见 [接口契约](docs/message-api-contract.md) 与 [验收报告](docs/idempotency-recovery.md)。

## 简历表述（可在完成演示与指标后使用）

> 独立设计并实现可审计的企业知识库与工单处置 Agent：通过制度检索、订单查询、工单路由和人工审批完成售后闭环；构建引用溯源、敏感操作拦截与审计日志，并以自动化评测覆盖退款审批、物流升级、引用准确性和提示词注入防护。
