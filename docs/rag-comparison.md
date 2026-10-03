# 阶段 5.0：受控语义 RAG 检索对照

本报告依据本机实际生成的 [`rag_comparison.json`](../evals/results/rag_comparison.json)（2026-09-22），不是模拟结果。这是**检索层 RAG 对照**，没有生成回答、没有接入 Agent 或 live 模式，也没有替换默认 candidate。未使用 Reranker、HyDE、混合召回、LangChain 检索器、向量数据库服务或网页语料抓取。

## 模型、索引和可复现边界

- 实际模型：`BAAI/bge-small-zh-v1.5`，revision `7999e1d3359715c523056ef9478215996d62a620`；模型卡声明 MIT，512 维。核对了[该 revision 模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/7999e1d3359715c523056ef9478215996d62a620/README.md)、配置及本地下载文件。
- 遵循模型卡的 Transformers 用法：CLS pooling，文档和查询都 L2 归一化；仅查询加前缀 `为这个句子生成表示以用于检索相关文章：`，文档不加。前缀不是用盲测结果选择的。CPU、FP32、2 线程、eval/inference 模式、最长 512 token；超长内容截断是当前实现的限制。
- `trust_remote_code=False`，使用 safetensors。显式构建脚本可以下载固定 revision；查询只加载本地快照，不下载、不重建。另一个模型名会明确拒绝，必须先审核配置，而不是静默套用当前维度/前缀。
- 从原始 84 个 KnowledgeDocument 组合标题、章节、标签、正文，构建真实 `faiss.IndexFlatIP`。所有向量归一化，内积等价余弦相似度；Top-3，分数低于阈值不返回；同分按文档 ID 排序。
- 本机索引：`E:\agent\ops-pilot\.rag-index\documents.faiss`，清单同目录 `manifest.json`。清单记录模型配置/revision、维度、语料与注册表哈希、完整文档哈希/ID、构建时间、权重和索引哈希、开发集校准全过程。模型、语料、文档顺序或维度不一致时拒绝旧索引；索引和模型权重校验也必须通过。
- 返回的是原始 KnowledgeDocument，不构造新引用；来源 URL、章节、发布日期、抓取日、scope 等全部保留。默认 Store、关键词规则、84 块、既有题集/冻结清单没有修改。普通测试无需安装 RAG 依赖或下载模型。
- 运行前后校验 blind/external 冻结清单，并比较题集、语料、注册表、检索代码、开发集、索引文件及清单的 SHA-256；结果另存模型配置 SHA-256。旧结果文件复跑后内容哈希不变。新的结果文件不会覆盖任何旧结果路径。

环境：Windows 11 x64，Python 3.13.2，Intel64 Family 6 Model 183 CPU；torch 2.14.0、transformers 4.57.6、huggingface-hub 0.36.2、faiss-cpu 1.15.1、numpy 2.4.4。依赖组保留兼容版本范围，严格复跑应使用这里记录的实际版本；不同硬件/库版本的浮点末位和延迟可能不同。

## 开发集阈值校准

只使用原 `retrieval_dev_cases.jsonl` 的 12 正例（四类各 3）和本阶段新增 `rag_dev_negative_cases.jsonl` 的 8 负例（编程、医疗、学习、系统设置、金融、创作、天气、泛化不明）。未改动旧开发题。没有使用 blind/external 来调整阈值、前缀或模型参数。

首次运行冻结集评测前，构建脚本已完成如下固定规则：遍历 0.00–1.00、步长 0.01，最大化 `(来源 Recall@3 + 章节 Recall@3 + 负例拒识率) / 3`；并列先选拒识率更高者，再选更高阈值。实际选择 **0.49**（分数等于阈值保留）。开发集来源命中 12/12、章节 11/12、负例拒识 7/8，目标值 0.93056。完整网格和原始开发分数保存在索引清单及对照 JSON 中。此阈值只是该语料/模型的经验阈值，不是概率或通用安全阈值。

冻结集评测不执行校准。真实重复运行测试得到相同指标、文档 ID 和分数，没有据此调参。

## 冻结集三方结果

blind：24 正例（每来源 6），8 负例；external：24 正例（每来源 6），12 负例。继承阶段 4 口径：Recall 是预期来源命中率；章节指标要求来源和至少一个预期章节关键字同时命中。错误引用率 = 非空但未命中预期来源 / 全部正例；无依据率 = 未命中预期来源 / 全部正例。它们不是生成回答的幻觉率。负例只计拒识，不混入正例 Recall。

| 集合 | 方法 | Recall@1 | Recall@3 | 章节@3 | 错误引用率 | 无依据率 | 负例拒识 |
|---|---|---:|---:|---:|---:|---:|---:|
| blind | baseline | 62.50% | 79.17% | 50.00% | 20.83% | 20.83% | 0/8 (0%) |
| blind | candidate | 79.17% | 91.67% | 75.00% | 0% | 8.33% | 5/8 (62.50%) |
| blind | vector_rag | 91.67% | 95.83% | 87.50% | 0% | 4.17% | 7/8 (87.50%) |
| external | baseline | 70.83% | 87.50% | 62.50% | 12.50% | 12.50% | 0/12 (0%) |
| external | candidate | 83.33% | 91.67% | 79.17% | 8.33% | 8.33% | 5/12 (41.67%) |
| external | vector_rag | 91.67% | 100% | 83.33% | 0% | 0% | 10/12 (83.33%) |

四来源 Recall@3（各项分母均为 6）：

| 集合/方法 | 退款退货 | 电商交易 | 快递物流 | 数电发票 |
|---|---:|---:|---:|---:|
| blind/baseline | 6/6 | 4/6 | 5/6 | 4/6 |
| blind/candidate | 5/6 | 6/6 | 5/6 | 6/6 |
| blind/vector_rag | 6/6 | 5/6 | 6/6 | 6/6 |
| external/baseline | 5/6 | 6/6 | 6/6 | 4/6 |
| external/candidate | 4/6 | 6/6 | 6/6 | 6/6 |
| external/vector_rag | 6/6 | 6/6 | 6/6 | 6/6 |

每条正例的 Top-3 原始文档、分数、来源/章节判断，以及每条负例的返回文档、分数和错误 ID 均在 JSON。关键词分数是对 Store 返回结果的审计重算，不参与召回或排序；vector 分数是真实余弦相似度，三种分数不能横向比较数值大小。

## 典型成功、条款失败和误召回

- 改进：`blind-r1` 用“只肯给余额、不肯按原先付钱渠道退”的口语表达，vector 命中 `OFFICIAL-RETURN-015`；`blind-r4` 问卖家不告知寄回地址，命中 `OFFICIAL-RETURN-011`。candidate 在这两题未命中预期条款。
- 改进：`external-r3` 问下单未确认、收货后才说机器启动就不让退，vector 命中 `OFFICIAL-RETURN-007`；`external-e1` 问店铺证照在哪里看，命中 `OFFICIAL-ECOM-009`。candidate 章节未命中。
- 回退：`blind-e4` 问花钱排在搜索前面的店是否需要标识，candidate 命中第四十条，vector 被 0.49 阈值拒绝，成为 blind 唯一无来源证据的正例。本轮不降低阈值来修此题。
- 条款混淆：`external-r1` 问十五天退货承诺，vector 返回退货相关块，但缺少第五条；`external-c2` 问当场检查快件，缺少第二十六条。这两题 candidate 原本章节命中。external 净增加 1 个章节命中，实际是改善 3 题、退化 2 题，不能忽略退化。
- 其他章节失败：`blind-e3`（销号门槛）、`blind-c6`（擅自拆件）；`external-r4`（可下载退换规则）、`external-r6`（平台对反复拒退的责任）。主题相近不保证检到精确义务条款。
- blind 唯一负例误召回：`negative-04` 问 Python 列表去重，返回 `OFFICIAL-ECOM-017`。
- external 两条负例误召回：`external-n2` 数据库连接异常 → `OFFICIAL-ECOM-017`、`OFFICIAL-COURIER-002`；`external-n7` 关闭手机定位保护隐私 → `OFFICIAL-ECOM-016`、`OFFICIAL-COURIER-004`。语义上“数据/安全/隐私”相近，不能替代服务领域判断。

## 延迟和本地资源

以下为最终单独运行的 JSON 实测。用开发题预热后，每题顺序执行 3 次真实检索，统计全部正负例样本（blind 每方法 96，external 108）。P50/P95 使用 nearest-rank；包括单条查询编码/检索，排除离线审计分数重算和首次模型加载。不是压测、并发 SLA，也不是不同硬件的性能保证。

| 集合/方法 | 平均 ms | P50 ms | P95 ms |
|---|---:|---:|---:|
| blind/baseline | 0.96 | 0.97 | 1.30 |
| blind/candidate | 3.80 | 3.60 | 5.11 |
| blind/vector_rag | 10.86 | 10.94 | 13.65 |
| external/baseline | 0.76 | 0.75 | 0.98 |
| external/candidate | 3.69 | 3.63 | 4.87 |
| external/vector_rag | 10.03 | 10.16 | 12.06 |

首次构建（含首次下载）21.59 秒；本次缓存模型/索引加载 5.19 秒。FAISS 文件 172,077 字节（约 168 KiB），模型缓存 96,405,251 字节（约 91.9 MiB）；评测进程末 RSS 约 487.3 MiB、峰值工作集约 514.6 MiB，包含 Python/torch/三种检索，不是模型单独内存。安装依赖的磁盘占用未计入模型缓存。Windows 不支持缓存符号链接时有空间警告，但真实下载/构建成功。

## 决策与局限

满足预先约定门槛：两套来源 Recall@3 不降；章节 Recall@3 分别净增 3 题（12.5 个百分点）和 1 题（4.17 个百分点）；负例拒识均不低于 candidate。因此**建议进入下一阶段受控 RAG 接入 Agent 验证，而不是立即替换默认方案**。本阶段默认入口仍为 candidate，业务审批与审计没有变化。

样本量很小、法规种类仅四类、发票语料只有三块；题目由项目开发过程编写，并非真实线上独立流量，前轮也记录了作者独立性限制。这是达到实验门槛的证据，不是统计显著性或通用泛化证明。关键词与向量均存在跨域误召回；接入前仍需专门验证领域拒识、精确条款、缺证据转人工和可回退性。不能因为总分提高而声称所有类别/条款均改善。

## 命令验收

| 命令 | 本机结果 |
|---|---|
| `python -m pytest -q` | 60 passed，2 skipped（显式真实模型集成测试），1 条已有 multipart 弃用警告 |
| `python evals/run_local.py` | 4/4 passed，退出码 0 |
| `python evals/run_retrieval_eval.py` | 成功，旧基线 75%/87.5%，退出码 0 |
| `python evals/run_keyword_comparison.py` | 成功，退出码 0 |
| `python evals/run_blind_retrieval_eval.py` | 成功，退出码 0 |
| `python evals/run_external_retrieval_eval.py` | 成功，退出码 0 |
| `python scripts/validate_knowledge.py` | 84 块四来源通过，退出码 0 |
| `pip install -e ".[rag]"` | 成功；最初 pip 不在 PATH，先用 `python -m pip` 安装，随后仅为当前命令补 Scripts 路径复跑原命令成功 |
| `python scripts/build_vector_index.py` | 真实模型下载、Embedding、FAISS 构建及开发校准成功，退出码 0 |
| `python evals/run_rag_comparison.py` | 三方对照及冻结前后校验通过，退出码 0 |
| `RUN_RAG_INTEGRATION=1` 后运行 `python -m pytest tests/test_rag_retrieval.py -q` | 15 passed，真实本地模型查询及两次完整对照的指标/分数可复现 |

没有未解决的下载、依赖或索引构建阻塞。所有测试/实验均未调用付费模型或生成式 LLM。
