# 阶段 6.1：DeepSeek 受控真实联调验收

结论：**通过本阶段五场景验收**。真实调用发生于 2026-10-02 13:46:27–13:46:53（北京时间）；2026-10-03 完成报告与离线回归收尾。共执行 5 个场景、每个一次，发起 **12 次真实模型 API 请求**，全部获得 HTTP 200，且逐项通过实际工具调用与本地结果核验。没有重试、充值、余额查询或真实退款。

原始脱敏结果：[deepseek_live_acceptance_20261002T054624980344Z.json](../evals/results/deepseek_live_acceptance_20261002T054624980344Z.json)。结果标记 `real DeepSeek API acceptance; NOT mock/offline`。阶段 6.0 的 Fake Model 16/16 是另一份离线证据，不能代替本次真实验证。

收尾时再次核对该结果记录的全部输入代码哈希和既有冻结清单，均一致；结果文件 SHA-256 为 `AFB34C561C9474AEC6364BF72E7916FCBEFA43E9EBE6474477A5064B3D0FCD73`。10 月 3 日收尾没有新增真实模型请求。

## 新增和修改文件

- 客户端：`app/live_config.py`、`app/live_agent.py`。
- 验收脚本：新增 `evals/live_acceptance.py`、`evals/run_live_agent_real_smoke.py`。
- 离线测试：新增 `tests/test_live_acceptance.py`，更新 `tests/test_live_agent.py`。
- 文档：新增本文；更新 `README.md`、`docs/deepseek-live-readiness.md`、`docs/agent-development-plan.md`。
- 本地产物：上述脱敏 JSON、隔离 `.venv`；模型凭据未写入文件。例行评测按原脚本生成结果，没有修改检索实现或题集。

## 环境与最小协议修复

建立项目 `.venv`，Python 3.13.2，`include-system-site-packages=false`；仅在该环境安装 `.[dev,live]`。`pip check` 返回 `No broken requirements found.`，没有改动全局包。关键版本为 FastAPI 0.142.2、Starlette 1.7.0、Pydantic 2.13.5、LangGraph 1.2.12、langchain-openai 1.6.7、OpenAI 协议 SDK 3.23.0；完整版本清单已写入脱敏结果。

实际请求和响应的模型名均为 `deepseek-v4-pro`，使用 `https://api.deepseek.com/chat/completions`、`temperature=0`、`max_retries=0`、30 秒超时。Key 仅从用户环境传给客户端；检查只输出存在与否，不保存其值、片段或哈希。

首次真实请求前核对了[官方思考模式协议](https://api-docs.deepseek.com/guides/thinking_mode/)和安装客户端源码：服务端默认思考模式要求工具往返保留 `reasoning_content`，当前 ChatOpenAI 不保留该扩展字段。因此在 `DeepSeekConfig.client_parameters()` 显式传入 `extra_body={"thinking":{"type":"disabled"}}`。这是本轮唯一模型协议行为调整，已有离线回归覆盖，本次 12 个真实请求也逐条检查了该参数。思考模式未验收。

新增 `observe_live_http()` 仅为受信本地脚本提供 SDK HTTP 观测客户端；未替换模型、ToolNode 或 Service。默认无观测上下文时不增加客户端参数。默认确定性路径、candidate 检索以及 live + vector_rag 的 503 边界保持原样。

## 五个真实场景

| 场景 | 预期工具 | 模型实际请求的工具 | HTTP 请求数 | 场景耗时 | 输入 / 输出 Token | 结果 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 只读知识 | search_knowledge | search_knowledge | 2 | 4.811 秒 | 2169 / 150 | 通过，引用对应实际检索文档 |
| 脱敏订单 | get_order | get_order | 2 | 3.743 秒 | 1608 / 120 | 通过，仅脱敏字段，无业务写入 |
| 物流异常 | get_order、create_ticket | get_order、create_ticket | 3 | 7.074 秒 | 2773 / 339 | 通过，1 个 delivery/open 工单 |
| 发票申请 | create_ticket | get_order、create_ticket | 2 | 5.678 秒 | 1865 / 384 | 通过，1 个 invoice/open 工单 |
| 退款申请 | request_refund_approval | get_order、request_refund_approval | 3 | 6.039 秒 | 2703 / 257 | 通过，1 个 pending 审批，0 个退款工单 |

发票场景在同一模型响应中请求两个工具，因此该场景仅需两次模型 HTTP 请求。知识问题明确要求调用搜索工具，其他问题明确提出查询或办理意图；这是定向能力验收，不代表随机线上问题的工具选择成功率。

场景耗时包含客户端初始化、LangGraph、工具和校验，单次 HTTP 耗时另存于结果文件。所有 Token 数均来自真实响应 `usage`，不是本地估算；合计输入 **11118**、输出 **1250**、总计 **12368**，其中输入缓存命中 5760、未命中 5358。

## 费用依据

根据 2026-10-02 核对的[DeepSeek 官方人民币价格表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，V4 Pro 每百万 Token 的空闲/高峰单价分别为：输入缓存命中 0.15/0.30 元、未命中 4.5/9 元、输出 13.5/27 元。

费用按 `(缓存命中 Token × 命中价 + 未命中 Token × 未命中价 + 输出 Token × 输出价) / 1000000` 计算。本次完整用量的空闲/高峰估算区间为 **0.04185–0.08370 元**；调用时间处于公开空闲窗口，对应估算 0.04185 元。该值不等于账单核对结果；没有查询账户、扣费或余额接口。脚本保留价格来源、核对日和区间，未来复跑应重新核对价格，不能把静态价格当成实时价格。

## 验收和数据边界

- 真实路径为 `OpsPilotService.handle_live_message → LangGraph → DeepSeek Chat → ToolNode → Service/Store`；执行时验证 `AGENT_MODE=live` 和 `KNOWLEDGE_RETRIEVER=candidate`。未注入 Fake，也未将真实客户端标记为 offline。
- HTTP 观测器直接提取真实响应的工具名，并检查后续请求携带的本地工具结果；前置 `retrieve_evidence` 的搜索事件不算模型工具调用。最终模型响应必须正常结束，不能把 HTTP 200、有引用或本地模板回复单独当作通过。
- 每场景使用独立临时 SQLite 数据库和内置脱敏模拟订单，全部临时库在运行后释放。脚本直接使用 Service，避免导入 `app.main` 时初始化默认数据库。SQL authorizer 限制首两场景不得写入工单/审批，后三场景只允许对应表 INSERT，始终禁止更改订单/知识和审批决定。
- 引用 ID 必须等于最后一次 `retrieval_completed` 和响应检索轨迹；引用所有字段与原 KnowledgeDocument 转换结果逐项相等。工单/审批对象必须与数据库一致，其 ID 与创建事件、`agent_completed` 一致，退款没有 `approval_decided` 事件。
- 订单工具结果仅允许六个脱敏字段。报告不保留请求头、模型原文、推理内容、工具参数全文、原始异常或本地绝对路径。第三方日志关闭，输出只做内存检查后丢弃；本次全部隐私检查通过。API 脱敏和 live/vector 拒绝边界由离线回归另外覆盖。
- 注入输入在禁止网络连接的上下文运行，经过 `guardrail → finalize_audit`，模型工厂调用数和 HTTP 请求数均为 0，没有检索、工单或审批。
- 启动前和结束后校验既有冻结输入；结果记录知识库、检索/服务/模型配置和验收脚本的 SHA-256。没有改动 84 个知识块、旧题集、冻结清单、Store 或 candidate 算法。

## 显式复跑

复跑会再次产生真实费用；普通测试、导入脚本或无参数运行均不会调用模型。未传 `--allow-real-api` 时退出码为 2，连 Key 配置都不读取。

```powershell
cd E:\agent\ops-pilot
# 仅新机器首次创建；不要覆盖已有环境。
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,live]"
.\.venv\Scripts\python.exe -m pip check

# 从已配置的 Windows 用户环境导入，切勿回显此变量。
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
$env:DEEPSEEK_MODEL = 'deepseek-v4-pro'
$env:DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
$env:AGENT_MODE = 'live'
$env:KNOWLEDGE_RETRIEVER = 'candidate'
.\.venv\Scripts\python.exe evals/run_live_agent_real_smoke.py --allow-real-api
```

脚本固定顺序、最多五个场景、每个只尝试一次。每场景沿用图的最多六次模型调用上限；无 SDK 自动重试或 HTTP 跳转。首个模型、工具或验收失败即取消后续场景，没有自动修复/重跑。未知错误只记录固定分类；拿不到 Token 时记录 null。结果按运行时间新建 JSON，不覆盖此前真实验收结果。每完成一个场景立即保存；若后续发生本地异常，已知请求数不会被抹掉，无法准确计数时标记 `request_count_complete=false`。HTTP 计数定义为 SDK 发送尝试；传输失败不代表远端收到。本次 12 次均取得响应，无计数歧义。

## 离线回归与局限

以下检查均在项目虚拟环境执行，退出码为 0：

| 检查 | 结果 |
| --- | --- |
| `python -m pip check` | 无依赖冲突 |
| `python -m pytest -q` | 130 通过、2 跳过 |
| `python evals/run_local.py` | 4/4 |
| `python evals/run_retrieval_eval.py` | 基线复跑成功 |
| `python evals/run_keyword_comparison.py` | 关键词对照成功 |
| `python evals/run_blind_retrieval_eval.py` | 盲测复跑成功 |
| `python evals/run_external_retrieval_eval.py` | 独立题复跑成功 |
| `python evals/run_live_agent_mock_smoke.py` | mock/offline 16/16，零模型请求 |
| `python scripts/validate_knowledge.py` | 84 个知识块通过 |

两项跳过是显式启用的真实 Embedding 集成测试；本阶段虚拟环境只装 dev/live，没有重新运行 BGE/FAISS 真实索引实验。RAG 受控模式的离线安全测试仍在完整 pytest 中通过。

本次只有每类一个样本、一个模型版本、非思考模式，不能推出生产稳定性、通用答案质量或思考模式兼容性。最终回复仍由本地事实模板生成；未验收自由生成答案。模型若在创建工单或 pending 审批后才失败，已有记录不会自动回滚，跨请求重试仍需业务幂等策略。网络、额度、服务端版本和价格仍可能变化；全局 Python 的既有依赖冲突未修复，项目须使用 `.venv`。live + vector_rag 继续 503。
