# 阶段 5.1：受控 RAG 接入 Agent 验证

## 范围和数据流

本阶段仅将向量检索接入现有单 Agent 的证据链路，默认仍为 candidate。Store 默认检索、关键词规则、84 个知识块、业务动作方法、审批和工单状态机均未修改。**RAG 已接入检索，但没有使用 LLM 生成带引用答案**；当前仍是确定性意图路由和制度摘录/固定业务回复。

FastAPI → OpsPilotService → LangGraph guardrail → retrieve_evidence → Service.retrieve_knowledge → candidate Store / 本地 VectorRetriever。成功且有证据时继续原 classify/action/finalize_audit；vector 空结果直接进入 finalize_audit，要求补充信息或转人工；vector 异常向上返回 503，不到达任何业务动作节点。

guardrail 拦截走 finalize_audit，不调用任一检索器，也不加载模型或索引。检索结果转为原 Citation：文档 ID、章节、来源 URL、发布日期和抓取日完整保留。已修正 Service 的 live 拦截后补检索路径，避免 guardrail 结束后又查询知识。

## 开关与失败/回退

`KNOWLEDGE_RETRIEVER=candidate|vector_rag`，未设置为 candidate。candidate 不导入 Embedding 模型栈，不依赖本地模型/索引。vector_rag 在通过 guardrail 后惰性导入 RAG 模块，并使用现有 RAG_MODEL_CACHE、RAG_INDEX_PATH、RAG_EMBEDDING_MODEL；配置见 README 和 `.env.example`。启动/health 不加载模型，也不是 RAG 就绪检查。

请求只使用本地缓存，不下载、不重建。缺依赖、缺模型、索引不存在、清单/语料/维度失效、模型加载或查询异常均 fail closed：HTTP 503，提示「RAG 检索不可用」，只保存固定错误码和脱敏说明。无异常堆栈、内部磁盘路径或密钥；不静默切回关键词。可能已经记录用户消息和失败审计，但不会创建退款审批、工单或成功回复。

空结果不是配置故障，返回 200，`retrieval.empty=true`，citations/approvals/tickets 均空，回复「当前知识库没有足够依据。请补充信息或转人工处理；本次未创建工单或退款审批」。即使输入含订单号和退款/物流意图也跳过业务动作。candidate 的原行为不变。

人工回退：在启动服务的终端设置 `$env:KNOWLEDGE_RETRIEVER = "candidate"` 后重启服务。不修改数据库或 Store 默认实现，不要求删除索引。非法模式值返回配置 503。当前不支持 `AGENT_MODE=live + vector_rag`：在调用 LLM/工具前明确返回 503；该组合的注入请求仍只执行确定性 guardrail。此限制避免未经验证的工具循环在失败前产生业务副作用。

为保持实现简单，每次向量请求重新实例化并校验本地索引/模型，不长期持有共享检索器或共享请求 trace。索引清单在检索前后比较 SHA-256，运行中变化则拒绝本次结果。索引更新应停止服务后显式构建。当前方式有加载代价，也不是高并发部署方案。

## 可观测性与隐私

新增 `RetrievalTrace`，API 兼容字段 `retrieval`；未执行检索时为 null。成功审计 `retrieval_completed` 与 API 使用同一白名单结构：

| 字段 | 内容 |
|---|---|
| retriever | candidate / vector_rag |
| document_ids | 本次命中文档 ID，顺序与 citations 一致 |
| scores | 向量余弦分数，与 ID 按位置对应；candidate 为空 |
| threshold | 索引清单中已有开发集阈值（本机 0.49）；candidate 为 null |
| model、revision | 已校验模型名称和固定版本；candidate 为 null |
| index_manifest_sha256 | 清单摘要，不返回整个清单及其内部路径 |
| elapsed_ms | 本次检索耗时，向量模式包含初始化/校验/模型加载，不等同于 5.0 热查询延迟 |
| empty | 是否未命中 |

失败为 `retrieval_failed`，含 retriever、固定 code（rag_unavailable / invalid_retriever / unsupported_live_retriever）、脱敏 message、business_actions_executed=false，并保留 `tool_called` blocked。非法模式未实际选定检索器，retriever 为 null。新增检索载荷不包含用户问题原文、订单/客户字段、环境变量值、系统提示词、路径或原始异常。

网页只从 trace 显示“关键词检索 / 向量 RAG · 命中 N 篇 · X ms”，不显示模型磁盘位置。无 trace 显示“本次未执行检索”。既有 citations 和业务字段未删除；失败仍由原错误提示入口展示。

## 验证结果与限制

新增离线 API/mock 测试覆盖：默认不导入模型栈、两种模式 guardrail 优先、向量调用与 trace/audit 一致、完整 citation、pending 审批及人工批准后才建退款工单、空结果停止动作、显式回退、live/vector 拒绝、live 拦截后不补检索，以及前端 JavaScript 语法/模式文案/路径不泄露。

缺索引、过期清单、缺依赖、缺缓存、模型加载错误、搜索错误，分别对退款/物流/发票请求验证脱敏 503 和零业务副作用。普通 pytest 不下载模型，既有显式真实模型集成测试仍默认跳过。

真实 smoke 运行 `OpsPilotService.handle_message → LangGraph → BGE/FAISS`，临时数据库不触碰用户工单：

| 场景 | 断言结果 |
|---|---|
| 平台交易信息保存年限 | 命中 OFFICIAL-ECOM-021；trace 与 citations/审计一致，无业务副作用 |
| 模拟订单物流延误 | 保留来源引用，创建一张 delivery 工单，不产生审批 |
| 模拟订单退款 | 仅一条 pending 人工审批，没有退款工单或资金动作 |
| 上海明天天气 | 空结果，提示补充信息或转人工，无引用/工单/审批 |

四项通过只证明接入与安全控制，不是广泛答案质量保证。开发期间额外试查“购物平台能不能随便删除买家的评价？”时，实际返回 RETURN-023/007/003 而不是预期评价条款，显示有真实引用也可能答非所问。本轮没有据此改模型、阈值、语料或检索逻辑；正式 smoke 法规项另用交易信息保存查询并增加明确目标文档断言。该诊断不属于冻结评测集，也不作为改善指标。

最终 smoke 在 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 下仍为 4/4，通过真实本地缓存完成。该次四项检索耗时分别约 5874 / 145 / 138 / 133 ms（首项含模型栈首次导入），不能拿 5.0 的约 10 ms 热查询数值当作 Agent 请求延迟。

阶段 5.0 的模型/阈值未变，重新构建及冻结集复跑通过。旧 `rag_comparison.json` 不改写，复跑输出改到 `rag_comparison_replay.json`；其他旧结果内容 SHA-256 也保持不变。真实 smoke 输出 `agent_rag_smoke.json`。

本轮命令验收：

| 命令 | 结果 |
|---|---|
| `python -m pytest -q` | 89 passed、2 skipped（显式真实模型集成测试）；1 条已有 multipart 弃用警告 |
| `python evals/run_local.py` | 4/4 passed，退出码 0 |
| `python evals/run_retrieval_eval.py` | 退出码 0，原始基线指标不变 |
| `python evals/run_keyword_comparison.py` | 退出码 0，指标不变 |
| `python evals/run_blind_retrieval_eval.py` | 退出码 0，冻结校验通过 |
| `python evals/run_external_retrieval_eval.py` | 退出码 0，冻结校验通过 |
| `python scripts/validate_knowledge.py` | 84 块通过，退出码 0 |
| `pip install -e ".[rag]"` | 成功，退出码 0（当前命令临时补 Python Scripts PATH） |
| `python scripts/build_vector_index.py` | 84 个真实向量重建成功，512 维，阈值仍 0.49，退出码 0 |
| `python evals/run_rag_comparison.py` | vector_rag 环境下复跑成功，退出码 0；独立 replay 文件，来源/章节/拒识指标不变 |
| `python evals/run_agent_rag_smoke.py` | 真实 4/4，退出码 0 |

六种 RAG 失败各对退款/物流/发票进行测试，共 18 组 503/无业务副作用断言通过。7 个既有结果文件内容哈希保持不变；Store、关键词代码、语料、旧题及冻结清单验证通过。没有未解决的安装或真实索引阻塞。

## 默认模式和下一阶段

默认保留 candidate：无依赖/下载成本、现有行为稳定；vector 有首次加载延迟、额外内存与每请求验证开销，也仍有跨域误召回和错误条款。同步模型工作还会占用当前 API 执行线程，不宣称高并发 SLA。检索分数不代表规则适用性。

建议可以准备隔离、另行授权的真实 LLM Agent 联调，而非直接启用生产或现有 live/vector 组合。本轮未调用付费 LLM。联调前应增加证据失败时工具循环的不可绕过约束、答案忠实性评测与提示注入测试，并限制模型调用成本和业务权限。

只有在独立真实分布问题上持续达到来源/章节准确性与拒识标准、引用支持回答的质量验收、端到端权限/无副作用回归通过，并满足冷启动/并发/内存预算及显式回退演练后，才考虑将 vector_rag 设为默认；不能仅凭四项 smoke 或两套小型检索集作此决定。
