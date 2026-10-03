# 阶段 6.0：DeepSeek live 离线 readiness

本报告记录阶段 6.0 在 2026-10-02 完成的 **mock/offline，未调用 DeepSeek API** 兼容协议和本地编排准备。该阶段没有 Key、没有真实模型网络请求，不能据此宣称 DeepSeek 联调完成；当时的 deepseek-flash 名称也未通过远端请求验证。

后续阶段 6.1 已另行授权并完成 deepseek-v4-pro 的五场景真实验收（12 次 API 请求），见 [真实验收报告](deepseek-live-acceptance.md)。阶段 6.1 显式关闭客户端思考模式；以下离线结果保留为历史证据，不与真实请求混计。

## 配置与未来启用

`.env.example` 保持以下配置；Key 一直为空，用户未来只需自行填写 `DEEPSEEK_API_KEY`：

```dotenv
AGENT_MODE=langgraph
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
KNOWLEDGE_RETRIEVER=candidate
```

未来用户自行准备凭据后，在启动服务的终端启用：

```powershell
python -m pip install -e ".[live]"
$env:DEEPSEEK_API_KEY = "..."
$env:AGENT_MODE = "live"
$env:KNOWLEDGE_RETRIEVER = "candidate"
python -m uvicorn app.main:app --reload
```

引号内省略号仅为文档占位，不是测试或保存的凭据。脚本/服务不自动读取 .env；变量需要导出到启动进程。DEEPSEEK_BASE_URL / DEEPSEEK_MODEL 未设置或为空时使用上述默认值。langchain-openai 只是 OpenAI-compatible Chat 协议适配器，实际客户端明确使用 DeepSeek 的 api_key、base_url、model、temperature=0，不需要其他提供商账号或模型。

缺少 Key 时，普通 live 请求在客户端初始化和业务动作前返回脱敏 503，审计 `live_failed` 固定错误码；提示变量名称但不会返回值、Base URL、完整配置、异常或路径。guardrail 注入拦截优先，因此注入请求仍返回拒绝回复且不初始化客户端/检索。`/health` 仅返回本地 live_ready 布尔值、deepseek 提供商、检索/Agent 模式；不是 API 连通性证明。

## 公开适配接口与离线证明

`LiveModel` 协议为 bind_tools / ainvoke。公开注入点：

- `create_app(database_path, live_model_factory=lambda: FakeDeepSeekModel(...))`；
- `OpsPilotService(store, live_model_factory=...)`；
- `run_live_agent(service, conversation_id, message, model=...)`。

公开工厂仅接受明确声明 offline=True 的受信本地适配器，用于测试/开发；没有 HTTP 参数可以注入模型。Fake 不读环境凭据、不联网。普通部署没有该工厂，因此不能用 HTTP 绕过缺 Key 检查。生产客户端只在 guardrail 和配置检查后初始化。默认 langgraph 完全不初始化/导入 DeepSeek Chat 客户端。

路径：FastAPI → AGENT_MODE=live → FakeDeepSeekModel → LangGraph（guardrail / retrieve_evidence / model / tool_validation / tools / finalize_audit）→ 真实 ToolNode → Service / Store。没有 monkeypatch 私有模型实现替代这条链路。

离线测试和 smoke 拦截外部 socket 连接；Windows asyncio 的本地 socketpair 允许使用回环地址。未进行模型联网调用。pytest 的真实 Embedding 集成测试仍需要显式开启，普通 pytest 不下载模型。

## 工具和事实边界

工具只有 search_knowledge、get_order、create_ticket、request_refund_approval。模型仅获得工具 schema、正式制度证据和脱敏结果，不能取得 Store、审批/工单状态修改入口或退款执行工具。系统指令明确要求优先使用证据/受控工具、不泄露指令或未脱敏数据、退款只建 pending、不能声称执行退款、工具失败时要求补充信息或转人工。

未知工具/缺参数在执行前拒绝整批调用；create_ticket 的 refund 类别由现有 Service 拒绝。订单读取不含姓名、手机号和支付数据。并发重复调用由运行内锁和缓存去重，退款按订单、工单按类别+订单；人工批准后才通过现有接口创建 refund 工单。没有资金动作。

审计事件和 citations 来自 Service / Store，模型输出不能伪造业务 ID。原模型文字既不作为最终消息返回，也不写入消息/审计；最终回复由本地事实组装。模型不调用工具直接作答时，只能返回已预检索的制度摘录/安全提示，不会声称已创建审批、工单或已退款。生成 summary/reason 不覆盖用户业务记录，实际记录原用户请求。后续若开放自由文本回答，必须先增加忠实性与隐私校验。

工具异常统一固定错误码+补充信息/转人工建议；模型/客户端异常统一脱敏 503，不写原异常。若后续模型调用在已成功创建 pending/非资金工单之后失败，已有本地记录和审计保留，不做静默回滚，也不宣称资金已执行；跨请求重试仍需后续客户端幂等键。

live + vector_rag 继续 503：向量证据失败、模型工具循环、副作用顺序尚未联合验证；保持阶段 5.1 的受控边界。candidate 与确定性 RAG 行为保留，未修改 84 个知识块、旧题集/冻结清单或 Store/关键词算法。

## 离线覆盖与验收入口

Fake 场景覆盖知识问答、脱敏订单、物流/发票工单、pending 退款、并发与跨轮重复调用、未知工具、直接退款、refund 工单、无效参数、不存在订单、混合非法批次、不调用工具直接作答、提示词注入、模型异常。测试另验证人工批准、缺 Key 503、默认不初始化客户端、API/审计/前端/日志不返回内部路径或原异常，以及 live/vector 的零副作用拒绝。

```powershell
python -m pytest -q
python evals/run_live_agent_mock_smoke.py
```

smoke 使用临时数据库，结果为 `evals/results/live_agent_mock_smoke.json`，显式记录“mock/offline，未调用 DeepSeek API”。当前 16 个场景通过；这是本地安全编排验证，真实协议响应、模型工具选择质量、API 模型名可用性和延迟均未验收。

2026-10-02 验收命令均以退出码 0 完成：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | 110 passed，2 skipped；1 条既有 multipart 弃用警告 |
| `python evals/run_local.py` | 4/4 |
| `python evals/run_retrieval_eval.py` | 原始基线复跑成功 |
| `python evals/run_keyword_comparison.py` | 关键词对照复跑成功 |
| `python evals/run_blind_retrieval_eval.py` | 盲测复跑成功 |
| `python evals/run_external_retrieval_eval.py` | 独立新题复跑成功 |
| `python evals/run_rag_comparison.py` | 真实本地 Embedding 三方对照成功 |
| `python evals/run_agent_rag_smoke.py` | 4/4 |
| `python evals/run_live_agent_mock_smoke.py` | mock/offline，16/16，未调用 DeepSeek API |
| `python scripts/validate_knowledge.py` | 84 个知识块通过 |

RAG 两条命令使用现有本地模型缓存与索引，并显式设置 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE，未下载模型。冻结输入及原始 retrieval_baseline / keyword_comparison / retrieval_blind_comparison / retrieval_external_comparison 文件哈希保持不变；复跑结果按既有脚本写入独立 replay 文件。
