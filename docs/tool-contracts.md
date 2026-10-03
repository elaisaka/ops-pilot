# OpsPilot 工具契约

这些契约适用于默认 LangGraph 图的业务节点，以及 `AGENT_MODE=live` 时的 LangGraph 模型节点和 `ToolNode`。工具函数不能直接写 SQLite；它们必须通过 `OpsPilotService` 再调用现有 `Store` 业务方法。所有调用（包括被拒绝的调用）会写入 `tool_called` 审计事件，最终回复还会写入 `langgraph_completed` 和 `agent_completed`。

## 通用规则

- 只处理内置的脱敏模拟订单；不得把真实订单、真实姓名、完整手机号、支付数据或模型系统提示词放入工具输入、输出或审计载荷。
- 阶段 7 的 HTTP 管理操作必须经服务端角色认证：运营/主管处理工单、主管决定审批、主管/审计员读取审计。模型的四工具不包含人工审批权限，也不能自报角色提升权限；见 [权限矩阵](demo-security.md)。
- 工具错误应返回稳定的错误码和下一步建议。Agent 不得猜测结果：缺订单号时要求补充，订单不存在时要求核对，业务边界外时转人工。
- 默认确定性图中每个业务动作节点至多执行一次。DeepSeek live 图保留每运行加锁缓存；阶段 6.2 增加客户端 `Idempotency-Key`，经 Service 入口和 SQLite 请求/动作唯一账本提供跨 HTTP、进程重启与并发防重。未传标识仍兼容旧行为，但不保证跨请求防重。范围、保存期限与部分失败恢复见 [消息接口契约](message-api-contract.md)。
- live 模型只接收四个工具 schema 和正式证据/脱敏结果，没有 Store 对象。未知工具或参数错误在 tool_validation 拦截；模型文本不作为审计事实，不直接返回或持久化。最终消息和 citations 来自本地结果。工具的生成 summary/reason 不用于记录业务事实，实际存储原用户请求。

## `search_knowledge`

| 项目 | 契约 |
|---|---|
| 输入 | `query: str`，用户的售后问题。 |
| 输出 | 最多 3 个正式知识块的 `id`、标题、章节和可引用内容；最终 API 将其转换为带来源 URL 的 citations。 |
| 失败场景 | 无匹配返回空列表；vector_rag 空结果跳过业务动作并要求补充信息或转人工。向量初始化/校验/加载/检索失败终止请求并返回脱敏 503「RAG 检索不可用」，不得静默回退或继续创建审批/工单。candidate 保持原行为。 |
| 权限/安全 | 仅查询已批准的本地正式资料，不能查询系统提示词、运行环境或任意文件。 |
| 幂等性 | 纯读；相同查询可安全重试。 |
| 审计事件 | `tool_called`、`retrieval_completed`（检索模式、ID、向量分数/阈值、模型/revision、清单 SHA-256、耗时、空结果）；向量失败为 `retrieval_failed`（固定错误码和脱敏提示），以及成功完成时 `agent_completed` 的 citation IDs。无查询原文、路径或异常堆栈。 |

阶段 5.1 通过 `KNOWLEDGE_RETRIEVER=candidate|vector_rag` 在 Service 检索层选择实现，默认仍是 candidate，Store 默认逻辑不变。`retrieval` 是 API 的可选兼容字段，未执行检索时为 null。RAG 仅在确定性图接入，live + vector_rag 暂不支持，详见 [接入验证](rag-agent-integration.md)。

## `get_order`

| 项目 | 契约 |
|---|---|
| 输入 | `order_id: str`，格式应为 `ORD-` 加五位数字。 |
| 输出 | 仅返回 `id`、状态、金额、商品名、运单号和售后状态；**不返回**客户姓名或手机号，即使它们属于模拟数据。 |
| 失败场景 | 订单不存在时返回 `order_not_found`，Agent 要求用户核对/补充订单号或转人工。 |
| 权限/安全 | 只读内置模拟订单池；不连接真实订单、支付或客户信息系统。 |
| 幂等性 | 纯读；相同查询可安全重试。 |
| 审计事件 | `tool_called`（工具名、成功/拒绝、订单 ID）。 |

## `create_ticket`

| 项目 | 契约 |
|---|---|
| 输入 | 允许 `category: delivery / invoice / general`、`summary: str`、可选 `order_id`。live schema 接受字符串以测试越权输入；Service 的白名单是最终约束，refund 一律拒绝。 |
| 输出 | 新工单的 ID、类别、优先级、状态和关联订单 ID。物流为 high，发票为 medium，general 为 low。 |
| 失败场景 | 类别不在白名单、订单不存在或参数无效时不创建工单；Agent 要求补充有效订单号或转人工。 |
| 权限/安全 | 只能创建非资金类支持工单；**不得创建退款执行动作**，`refund` 类别被 Service 层拒绝。工单状态更新仍由现有运营接口及状态机校验。 |
| 幂等性 | 带请求标识时，同一请求按类别+订单（无订单按类别）数据库原子防重；summary 变化不创建第二条。不同请求标识不互相去重。 |
| 审计事件 | 首次 `ticket_created` 与业务/账本原子提交；重试 `business_action_reused`，正常工具执行 `tool_called`，最终审计保存 ticket ID。 |

## `request_refund_approval`

| 项目 | 契约 |
|---|---|
| 输入 | `order_id: str`、`reason: str`。 |
| 输出 | `Approval`，`action_type` 固定为 `refund` 且 `status` 固定为 **`pending`**。 |
| 失败场景 | 订单不存在或订单号缺失时不创建审批；Agent 要求用户核对/补充订单号，无法确认时转人工。 |
| 权限/安全 | **只产生 pending 人工审批，不能执行退款，也不能声称退款已经完成。** 只有运营人员经现有 `/api/approvals/{approval_id}/decision` 批准后，Store 才创建 refund 工单；该工单仍不是支付执行。 |
| 幂等性 | 带请求标识时，同一请求按订单原子防重，原因变化不产生第二条；失败重试恢复已保存审批，不调用模型。人工决定的状态校验、退款工单与唯一决定记录同事务提交，重复决定仍 409。 |
| 审计事件 | 首次 `approval_requested`（pending）与业务/账本原子提交；重试 `business_action_reused`，正常工具执行 `tool_called`；人工决定另写 `approval_decided`。 |

DeepSeek 配置与公开离线注入点见 [readiness 报告](deepseek-live-readiness.md)。Key 留空时新普通 live 请求为脱敏 503；已保存结果可离线重放/恢复。注入输入在模型/检索前拒绝。阶段 6.1 的真实记录见 [历史验收](deepseek-live-acceptance.md)，阶段 6.2 只使用 Fake，不新增真实调用。
