# 本地演示认证与 API 权限

阶段 7 不引入账号注册、密码数据库、第三方身份服务或生产认证。显式运行 `python scripts/create_demo_accounts.py`，为 operator、supervisor、auditor 各生成一个密码学随机 bearer 凭据。服务器只读 `data/demo-auth.json` 中的 SHA-256 摘要与角色；原凭据只保存在被忽略的本地 `data/demo-credentials.json`，不打印、不提交。Linux 文件权限 0600；Windows 继承目录 ACL，需要用户保护本地目录。

客户端通过 `Authorization: Bearer <本地凭据>` 登录。页面只在内存保存凭据，不写 sessionStorage/localStorage，不放 URL。`GET /api/auth/me` 返回认证身份，不返回凭据。没有固定默认密码。角色不是请求参数，X-Role、decided_by、assignee 不能提升权限；审批决定人、处理负责人由服务器认证身份写入。旧 decided_by/assignee 字段保留接收兼容，但不再信任其值。

| API | 匿名 | operator 运营 | supervisor 主管 | auditor 审计 |
|---|---|---|---|---|
| GET /、/health、/docs、/openapi.json、/api/knowledge | 允许 | 允许 | 允许 | 允许 |
| POST /api/conversations、/{id}/messages | 允许模拟提问 | 允许 | 允许 | 允许 |
| GET /api/auth/me | 401 | 自身 | 自身 | 自身 |
| GET /api/orders、tickets、approvals、dashboard | 401 | 只读 | 只读 | 只读 |
| PATCH /api/tickets/{id} | 401 | 允许 | 允许 | 403 |
| POST /api/approvals/{id}/decision | 401 | 403 | 允许 | 403 |
| GET /api/conversations/{id}/audit | 401 | 403 | 允许 | 允许 |

上表 messages 完整路径为 `/api/conversations/{id}/messages`。用户提问可创建未处理工单和 pending 审批，但不能决定审批或推进工单状态。公开知识、模拟订单工具回复不包含真实个人信息。审计员允许像匿名用户一样发起模拟提问，但不能执行管理动作。

无/错误凭据 401；有效身份越权 403；有凭据但认证配置缺失/损坏 503，不降级为无认证。身份校验在业务方法调用前执行，拒绝请求不修改业务表。审计事实使用认证身份，凭据不进入 API 输出、业务审计或请求 URL。没有新增管理/账号 API；账号配置由本机文件管理。

Idempotency-Key 范围仍是会话+键，管理身份切换不改变同一用户提交的动作账本；回归测试验证重放仍只有一条审批，重复人工决定仍 409、只有一个 refund 工单。权限不会绕过 Service 校验或数据库事务。

## 明确局限

- 仅本地演示：HTTP 仅绑定 loopback；没有 TLS、用户级会话隔离、速率限制、登录失败锁定、细粒度个人账号、令牌过期、完整身份审计与撤销接口。
- bearer 是持有者凭证。替换服务端摘要文件可撤销旧凭据，但没有自动轮换。文件可写权限意味着能改角色，须保护文件系统；不承诺抵抗本机管理员。
- 三个共享演示身份不是生产员工账号。公开消息入口只能用于模拟数据，不能把真实订单/个人信息接进来。
- Docker/本地数据目录备份同时包含业务、防重账本与认证摘要，原凭据单独妥善保管。
- 直接内部调用 Service/Store 属于受信任代码，不是 HTTP 权限边界。未来生产化须另做身份、租户、审计与外部系统安全设计。
