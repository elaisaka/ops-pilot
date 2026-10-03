# 阶段 6.2：跨请求幂等与失败恢复验收

验收日期：2026-10-03。结论：**通过本地受控验收；带稳定请求标识的重复审批/工单已由数据库层阻止。** 本阶段真实模型调用为 0，只使用 Fake Model 和模拟订单。没有安装或修改全局 Python 包。

## 最小实现

数据流：网页持久保存提交标识 → FastAPI `Idempotency-Key` → Service `handle_submission` → SQLite 请求领取 → 原 LangGraph / ToolNode → 原 Service/Store 业务校验 → 原子动作账本；完成结果持久保存。已完成请求直接重放；已有动作但未完成的请求只恢复本地结果，不再调用模型。

为保留冻结检索实现，新增 `RequestStore` 包装现有 Store，未修改 `app/store.py` 或 `app/retrieval.py`。事务视图只延迟既有 Store 方法的 commit，由外层统一提交业务记录、唯一动作账本和创建审计。SQLite `BEGIN IMMEDIATE` 在短数据库事务内串行化检查/写入，不跨模型调用持锁。

| 新表 | 数据库约束与用途 |
|---|---|
| message_requests | 会话+标识摘要唯一；内容摘要冲突拒绝；running/failed/completed、执行者令牌、租约、证据与响应检查点 |
| request_actions | 请求+动作范围主键；结果类型+ID 唯一；退款按订单、工单按类别+订单防重 |
| approval_decisions | 审批 ID 主键、工单 ID 唯一；人工决定与 refund 工单及审计同事务 |

只新增表，不删除或重建原有业务数据。迁移测试预先创建旧工单、审批、审计，再初始化新服务，确认原记录保留。人工重复批准仍返回 409，仅首次批准产生一个 refund 工单，绝不执行资金退款。

## 故障语义与安全边界

模型在工单/审批落库后异常：首轮返回 503 `partial=true`，明确已有业务记录；同键重试返回 `recovered`，复用同一业务 ID 与真实检索引用，没有额外模型调用。恢复消息明确此前处理未完成，不补办模型计划中其他动作。已人工处理的审批重放时显示当前状态，不伪称仍 pending。

同键异内容 409 并写 request_conflict；并发同键 409 in-progress 或在首轮完成后重放。执行权租约为 300 秒：崩溃后等待到期接管，旧执行者令牌/租约在后续写入时校验，不能落库。动作账本插入故障测试验证业务记录与创建审计一起回滚。

标识由客户端控制，模型 schema 不增加标识参数。网页 sessionStorage 保存失败/刷新待确认提交，成功才清除；运营面板刷新失败不会把已完成提交误判为需重发。日志、API 和审计不输出凭据或原始模型异常。默认 langgraph + candidate、live + vector_rag 503、注入先拦截等原边界保持。

完整有效范围、永久保留策略、响应字段和错误码见 [消息接口契约](message-api-contract.md)。未传标识、换键、换会话、关闭标签页丢失标识、删除账本、独立数据库副本之间及外部支付系统均不在保证内；没有认证、后台恢复、租约心跳或自动继续未完成计划。账本随数据库永久保留，暂无容量清理机制。

## 离线验证结果

使用项目 `.venv\Scripts\python.exe`，进程环境清空模型凭据、默认 langgraph + candidate；FastAPI 全局实例使用临时 DATABASE_PATH，测试后关闭，避免读写演示业务库。普通测试禁用外部网络。没有执行真实联调脚本。

| 命令 | 结果 |
|---|---|
| python -m pip check | No broken requirements found，退出 0 |
| python -m pytest -q | 147 passed, 2 skipped，12.24 秒，退出 0；两项真实 Embedding 显式选跑测试跳过 |
| python evals/run_live_agent_mock_smoke.py | mock/offline，未调用 DeepSeek API；16/16，退出 0 |
| python evals/run_local.py | 4/4，退出 0 |
| evals.run_rag_comparison.verify_inputs() | 冻结输入一致，退出 0 |

新增 17 项测试（含参数化）：三类业务顺序重试、三类业务动作后模型异常、同键不同内容与不同合法键/会话、无键旧兼容、并发 HTTP、真实双进程并发、进程重启恢复、强制退出后租约接管、旧执行者隔离与唯一约束、事务故障回滚、旧数据迁移与人工并发审批、审批后重放/live-vector 拒绝，以及实际网页 JavaScript 的失败/刷新复用、新提交新键和面板失败处理。断言真实 SQLite 记录数、业务 ID、pending 状态、审计次数与模型调用次数，不只检查 HTTP 状态。

检索代码、84 个知识块和冻结评测输入未改动；没有复跑或覆盖旧检索结果。业务 smoke 的 latest.json 和 mock smoke 的 live_agent_mock_smoke.json 由原脚本正常刷新，仍是离线业务证据，不属于冻结检索结果。

## 修改文件

- 新增：app/request_store.py、tests/test_idempotency.py、tests/test_submission_frontend.py、docs/message-api-contract.md、本报告。
- 修改：app/main.py、app/models.py、app/service.py、app/fake_deepseek.py、app/static/index.html、docs/tool-contracts.md、README.md、docs/agent-development-plan.md。
- 运行输出：evals/results/latest.json、evals/results/live_agent_mock_smoke.json。

结论限定为：使用同一 SQLite 数据库、同一会话和稳定有效标识，经公开消息入口提交时，同请求同动作范围的重复审批/工单由数据库唯一约束与原子事务阻止；该结论不是外部业务系统“全局 exactly-once”的承诺。
