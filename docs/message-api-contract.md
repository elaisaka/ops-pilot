# 消息提交与幂等契约（阶段 6.2）

`POST /api/conversations/{conversation_id}/messages`，原 JSON `{"message":"…"}` 不变。客户端可发送 `Idempotency-Key`，格式为 1–128 个 ASCII 字母、数字或 `._:-`。模型工具没有该参数，不能选择或覆盖标识。

## 有效范围与保留

- 范围是同一 SQLite 数据库中的 `(conversation_id, Idempotency-Key)`；消息按接口验证后的完整文本进行 SHA-256 比对，空白差异也视为不同内容。数据库只存标识摘要，不存原标识。
- 同键同内容复用；同键不同内容拒绝；不同键或不同会话视为合法新提交，不按订单全局去重。
- 不传标识保持旧行为，响应头 `Idempotency-Status: unprotected`，**不提供跨请求防重**。直接调用旧 Service 方法同样不提供该保证；集成方应调用 `handle_submission`。
- 请求账本随数据库永久保留，当前没有自动清理或 TTL。300 秒执行租约不是幂等有效期。删除数据库或自行清理账本会丢失保护；备份恢复须同时保留业务记录和账本。
- 网页首次发送前生成 UUID 并保存会话、原消息和标识到 sessionStorage；失败、响应丢失、同标签页刷新后复用，禁止在待确认状态修改原文。成功确认或明确“另发新请求”才清除；关闭标签页可能丢失客户端标识。后者不会撤销已落库记录。

## 响应与恢复

| 情况 | HTTP / 语义 |
|---|---|
| 首次成功 | 200，`Idempotency-Status: completed` |
| 已完成重试 | 200，`replayed`；不调用模型，审批/工单刷新为数据库当前状态 |
| 动作已提交但后续失败 | 503，`detail.code=request_failed`、`partial=true`；不宣称回滚或退款执行 |
| 上述失败使用同键重试 | 200，`recovered`、`request.partial_recovery=true`；仅返回已保存动作与检索证据，不继续模型未完成的计划，需继续处理时转人工 |
| 动作提交前失败 | 503、`partial=false`；同键可重新运行图 |
| 同键不同消息 | 409，`idempotency_conflict`，不复用、不执行图 |
| 同键正在运行 | 409，`request_in_progress`，`Retry-After: 5`；没有自动重试 |
| 执行者租约失效 | 409，`request_ownership_lost`；旧执行者禁止继续落库 |
| 无效标识 | 400，`invalid_idempotency_key` |

带标识的错误 `detail` 是 `{code,message,request_id,partial}`；成功增加可选 `request: {id,outcome,partial_recovery}`。未带标识的旧错误格式保持不变。错误不暴露原始异常、路径或凭据。

进程崩溃可能使请求暂时停留 running：300 秒租约到期后，同键重试接管；有动作则本地恢复，无动作才重跑。当前没有租约心跳或后台恢复。服务仍显式拒绝 live + vector_rag（包括已完成请求的重放），注入拦截先于模型与检索。

## 数据库与审计

请求唯一键、动作账本主键 `(request_id, action_key)` 与短 `BEGIN IMMEDIATE` 事务共同防重。动作范围：退款按订单；工单按类别与订单，无订单按类别。同一次提交中的同类同订单动作最多一个；不同类别/订单可分别产生动作。业务写入、账本与首次创建审计同事务提交，不存在“业务成功而防重账本未提交”的窗口。

新增审计包括 `request_started`、`request_retried`、`request_in_progress`、`request_conflict`、`request_failed`（partial）、`request_completed`、`request_recovered`、`request_replayed`、`business_action_reused`；首次业务事件仍为 `ticket_created` / `approval_requested`，带 request_id。引用恢复自先前真实检索检查点，不采用模型文本。

人工审批仍要求 pending，决定、refund 工单、唯一 approval_decisions 记录和 approval_decided 审计原子提交。重复决定返回既有 409，不重复生成退款工单；工单不是资金退款。

阶段 7 增加管理接口角色认证，见 [权限表](demo-security.md)；幂等标识不是授权凭证。模拟提问入口仍公开，不提供会话级隐私隔离。只保证同一数据库内的本地业务动作，不保证独立数据库副本、外部支付/消息发送、客户端丢失标识后的新请求或任意 SQL 绕过 Service 的操作。
