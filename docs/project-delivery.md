# 阶段 7：本地演示项目交付验收

日期：2026-10-03。结论：**达到本地“简历演示项目完成”标准，不等于生产可用；容器运行与远程 CI 尚未验证。** 默认无 Key、LangGraph + candidate；live + vector_rag 仍返回 503。本阶段真实 DeepSeek 请求 0，没有更改全局 Python，没有创建远程仓库、推送或公开项目。

## 本次交付

- 随机本地演示凭据与服务端摘要鉴权，operator / supervisor / auditor 权限矩阵。401 未登录、403 越权；不信任自报角色、审批人或负责人。公开模拟提问不要求登录。
- 页面登录、身份切换/退出、审计查看；凭据仅内存保存。请求重试的原标识仍用 sessionStorage，授权与数据库防重互不替代。
- SQLite 请求/动作账本不改，角色 API 测试直接验证记录、审计、重复审批和同键重试。默认检索、84 个知识块、冻结输入与历史检索结果不变。
- 非 root Dockerfile、仅 loopback 暴露的 Compose、命名卷持久化、显式账号初始化。构建仅安装核心包，不包含模型、不下载 BGE、不要求模型 Key。
- Windows Python 3.13 已安装环境的依赖约束文件与本地启动说明；自动测试配置和统一离线脚本。依赖约束不是跨平台带哈希锁文件，Linux 独有包仍由平台解析，镜像标签未锁 digest。
- 5 分钟演示、架构图、权限表和可核实简历内容，不夸大线上能力。

## 实际运行结果

运行 `.\.venv\Scripts\python.exe scripts/check_offline.py`，内部命令全部退出 0：

| 检查 | 结果 |
|---|---|
| python -m pip check | No broken requirements found |
| python -m pytest -q | 152 passed, 2 skipped；原 147 项 + 新 5 项权限测试；两项真实 Embedding 集成选跑继续跳过 |
| python evals/run_live_agent_mock_smoke.py | mock/offline，未调用 DeepSeek API；16/16；使用临时随机主管凭据，真实验证 HTTP 授权而非绕过依赖 |
| python evals/run_local.py | 4/4 |
| python scripts/validate_knowledge.py | 84 块，四类来源 25/34/22/3，元数据校验通过 |
| verify_inputs() | blind/external 冻结哈希一致 |

原 API 测试现在使用临时随机主管身份走真实 bearer 校验；原“assignee=alice”断言改为认证身份 demo-supervisor，因为不能再信任客户端自报负责人。新增权限测试显式覆盖匿名、运营、主管、审计及错误凭据/损坏配置，不借测试开关关闭认证。

`.\.venv\Scripts\python.exe scripts/run_demo_smoke.py` 退出 0：实际启动 `python -m uvicorn app.main:app --host 127.0.0.1 --port <临时端口> --no-access-log`，通过真实本地 HTTP 验证知识引用、模拟订单、物流/发票工单、pending、主管决定、越权拒绝、重复决定、同键重试与审计。使用临时 SQLite/账号，结束后关闭服务并清理；没有读取本地用户凭据或调用模型 API。

`docker compose config --quiet` 退出 0。`docker info` 退出 1：Docker Desktop Linux Engine 管道不可用。**未执行镜像构建或容器启动，不声称通过。** 没有为此启动/修改全局 Docker 服务。README 的固定 8000 端口/持久库交互操作与 Compose 需用户按文档执行，本次只实测隔离临时端口的同一 Uvicorn 启动路径。

本次 smoke 输出到 `.local-results/stage7`；历史 `evals/results` 未覆盖。历史 DeepSeek 证据仍仅阶段 6.1 五场景、12 次请求，原报告未改。

## 文件清单

- 新增：app/auth.py；scripts/create_demo_accounts.py、check_offline.py、run_demo_smoke.py；tests/test_auth.py；Dockerfile、compose.yaml、.dockerignore、constraints-demo.txt、.github/workflows/offline.yml；docs/demo-security.md、demo-guide.md、本报告。
- 修改：app/main.py、app/static/index.html；tests/conftest.py、test_api.py、test_idempotency.py；evals/run_live_agent_mock_smoke.py、run_local.py（支持独立输出目录）；README.md、.env.example、.gitignore；docs/message-api-contract.md、tool-contracts.md、agent-development-plan.md。
- 本地生成、不提交：.local-results/stage7 下 smoke 结果。没有新增固定凭据或向仓库写入 Key。

## 未验证与风险

Docker 镜像构建、Linux 容器卷权限/启动、真正新机器从零安装、远程 GitHub Actions 都尚未运行；因此只保证本机验证与已提供可复现步骤，不能将配置存在当作运行成功。依赖安装/镜像构建需要网络，但不需要模型 Key。

本地共享 bearer 无过期/自动轮换、无 TLS/限流/租户隔离；匿名消息只适合模拟数据。没有生产权限系统、真实订单/支付集成或外部事务 exactly-once。客户端丢失幂等标识或使用新键会成为新请求；并发保护仅在同一数据库。容器卷删除会连同业务/身份/账本删除。详见 [安全边界](demo-security.md) 和 [幂等契约](message-api-contract.md)。
