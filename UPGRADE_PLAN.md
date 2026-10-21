# FileButler 知识库/检索技术升级方案

> 制定日期：2026-10-01。基于 v1.2.0 代码现状（HANDOFF.md 第三次接手后）与
> 2026-10 的开源生态调研（RAGFlow ~80k★ / AnythingLLM ~60k★ / Khoj 37.6k★ / sqlite-vec / Docling / MinerU）。
> 原则：**借成熟库不造轮子，但严格控制依赖体积**；每阶段独立可回退；不改 UI 主流程。
>
> **✅ P0~P3 已于 2026-10-02 执行完毕（v1.3.0），结果见文末「§10 执行结果」。**
> 重要变更：P2 经 spike 实证后改向——sqlite-vec NO-GO，改为 float32 精度修正。

---

## 0. 现状盘点（代码事实）

| 模块 | 现状 | 位置 |
|---|---|---|
| 检索链路 | **纯向量 top-8**，无关键词融合，无 rerank；embed 不可用才降级关键词 | `knowledge/rag.py:35-82` |
| 向量存储 | 全量载入内存，float16 分块点积（暴力检索） | `db.py:837-902`（VectorIndex） |
| chunk 索引 | chunks 表只有 vec BLOB，**没有 FTS**；全盘正文另有独立 FTS（contentindex，与 KB 互补） | `db.py:151-158` |
| PDF 解析 | 主通道 **pypdf** 纯文本抽取；扫描件走 Windows OCR（~0.3s/页）→ VL 模型兜底 | `knowledge/parsers.py:36-65` |
| PyMuPDF | 已在依赖中，**但只用于 OCR 逐页渲染**，主解析通道没在用 | `requirements.txt`、`parsers.py:50` |
| 切块 | 按段落聚合 800 字符/重叠 120，无标题感知，块不带文件名/标题上下文 | `knowledge/chunker.py` |
| 问答上下文 | 每块截 600 字符直接拼 prompt | `rag.py:114-117` |
| 运行环境 | venv Python **3.14.7**，SQLite 3.50.4；`enable_load_extension` **已实测可用**（2026-10-01，sqlite-vec 的前提成立） | 本机验证 |

调研结论对照：成熟项目（RAGFlow/Khoj/AnythingLLM）的三件标配我们缺两件——**混合检索**与 **rerank**；
向量侧 sqlite-vec 是 SQLite 生态的官方答案；文档解析侧 Docling/MinerU 因体积（torch 级依赖，
安装包现为 ~48MB→131MB onedir）不适合进主包（见 §6 不做清单）。

## 1. 方案总览

| 阶段 | 内容 | 新依赖 | 工作量 | 用户可感知 |
|---|---|---|---|---|
| P0 | 基线评测集 + sqlite-vec spike | - | 0.5 天 | - |
| P1 | 混合检索（chunk FTS + 向量 RRF 融合） | 无 | 1 天 | ⭐⭐⭐ 最直接 |
| P2 | sqlite-vec 替换内存向量索引 | sqlite-vec（纯 C 扩展，~几百 KB） | 1~1.5 天 | ⭐（规模地基） |
| P3 | 解析与切块升级（PDF 换 PyMuPDF + 标题感知切块 + 上下文扩容） | 无（全用现有依赖） | 1~1.5 天 | ⭐⭐⭐ |
| P4 | 本地 rerank（可选增强，拍板后做） | onnxruntime | 1~2 天 | ⭐⭐ |

推荐顺序 **P0 → P1 → P2 → P3 → P4**。P1/P3 是用户最能感知的收益且零新依赖；
P2 是规模地基（去全量常驻内存、支撑百万块、为 int8 量化铺路），且 spike 已在 P0 验证可行性。

---

## 2. P0：基线评测 + 可行性 spike（0.5 天）

目的：没有基线，后面每一步"变好了没有"都是感觉。

### 2.1 检索质量基线
- 用自己真实知识库文档建 **20~30 题固定评测集**（问题 → 应命中的文件路径/块关键词），
  存 `tests/eval_qa.json`，脚本输出 hit@8、首个正确块排名、平均检索耗时。
- 记录改造前数字，每阶段结束复跑。

### 2.2 sqlite-vec spike（不进主代码）
- venv 里 `pip install sqlite-vec`，验证：3.14 加载 dll、vec0 建表（1024 维）插入 10 万向量、
  KNN 查询耗时与正确性（与现有 VectorIndex 结果对拍）。
- vec0 用默认 L2 距离 + **写入前归一化**（与现在 rag 语义等价；若实测版本支持
  `distance_metric=cosine` 则直接用）。
- PyInstaller 试打包：spec 加 `--collect-all sqlite_vec`（或 add-binary 收 dll），
  独立 `--distpath dist_verify --workpath build_verify` 起包验证扩展可用（沿用 §5 打包规矩）。
- **产出 GO/NO-GO**：NO-GO 则 P2 取消，其余阶段不受影响。

## 3. P1：混合检索 RRF（1 天，零新依赖）——性价比最高

**问题**：向量检索对"专有名词/编号/精确短语"弱（bge-m3 对短查询尤其如此），
而 FTS 对语义改写弱——成熟项目全部是两路融合。

做法：
1. 新增 `chunk_fts`（fts5 trigram + 中文，**独立表 + 显式 INSERT/DELETE/UPDATE 触发器**，
   跟 `file_fts`/`doc_content_fts` 同一套成熟形态；**不要用 external content + rebuild**——
   ROADMAP 里记过 SQLite 3.50.4 + trigram + WAL 下 rebuild 后触发器写坏库的教训）。
2. 旧库迁移：一次性从 chunks.text 全量灌入（chunks 规模小，秒级），记 `chunk_fts_built` 设置位。
3. `rag.search()` / `rag.ask()`：向量 top-20 + FTS top-20 → **RRF 融合**（k=60）→ 取 top-8
   进 prompt。两路都空才走现有 keyword 降级；返回 `mode="hybrid"`。
4. 顺手项（可选）：`global_search` 的内容/向量两路现在只是"FTS 排向量前"的拼接，
   可同样换 RRF，一处小改。

验收：评测集 hit@8 不低于基线（预期提升主要在编号/术语类问题）；`test_rag` 回归全绿；
新增 `tests/test_hybrid.py`（融合排序、空向量、空 FTS、触发器一致性）。

## 4. P2：sqlite-vec 替换内存向量（1~1.5 天）

**问题**：VectorIndex 全量常驻内存 + 每次写后 invalidate 重载全表；块数上 10 万后
内存（~200MB 级）和重载都难受。sqlite-vec 是纯 C 无依赖扩展、Windows 可用、
磁盘存储 + 可选 int8 量化，Python 3.14 加载已验证。

做法：
1. `CREATE VIRTUAL TABLE chunk_vec USING vec0(...)` 与 `img_vec` 两张 vec0 表
   （float32，1024 维，bge-m3）；写入侧归一化。
2. 迁移脚本：从 `chunks.vec` / `img_index.vec` 现有 BLOB 重建（含 §4.9 存档向量——
   **确认 `%APPDATA%\FileButler\archive\` 里那 1,925 行向量一并恢复，别再丢一次**）。
3. `VectorIndex` 保留为**降级路径**：扩展加载失败（异常环境/精简包）自动回落现有内存方案，
   与现有 OCR/关键词降级风格一致。
4. `invalidate()` 语义简化为增量写（vec0 支持单行 DELETE+INSERT），写放大不再触发全表重载。
5. 打包：spec 收 dll；记录包体增量（预期 < 1MB）；dist_verify 起包跑
   向量插入/检索冒烟；版本号 +1。

验收：迁移后检索结果与旧实现对拍一致（同 query top-8 重合率）；全测试基线
（228 断言）+ 新迁移用例全绿；冷启动不再因向量全量载入变慢。

## 5. P3：解析与切块升级（1~1.5 天，零新依赖）

1. **PDF 主通道 pypdf → PyMuPDF**：fitz 已在依赖里（现在只干 OCR 渲染），
   `page.get_text()` 的阅读顺序、表格文本、双栏处理显著好于 pypdf——这是调研里
   Docling/MinerU 要解决的 80% 问题里零成本可拿到的部分。pypdf 保留为 fallback。
2. **标题感知切块**：md 的 `#`/docx 的 heading/pptx 的页标题作为切块边界；
   每块前置「文件名 + 所在标题」一行（成熟 RAG 的标准免费增益，跨块指代明显变准）。
3. **上下文扩容**：`ask()` 每块 600 → 1200 字符，且 top-8 先按文件聚合再排序拼接，
   避免同文件 8 块占满上下文。
4. **重索引策略**：解析/切块变了但 kb_files 按 mtime+size 记账不会重跑——
   加 `parse_ver` 版本标记，不匹配视为需重索引；设置页知识库卡片提供「按新解析重建」入口
   （后台线程、可停止，同 contentindex 的形态）。

验收：同一批含表格/双栏 PDF 的评测题命中率提升；重索引后台可停；全测试基线回归。

## 6. P4（可选，拍板后）：本地 rerank（1~2 天）

- onnxruntime + `bge-reranker-v2-m3` int8 ONNX（~600MB，**下载不进包**，沿用设置页
  模型管理 UX：按需下载/删除/占用显示）。流程：hybrid top-50 → rerank → top-8。
- 包体代价：onnxruntime 约 +25MB；CPU 对 50 对打分约 1~3s → 默认关，设置页开关。
- 无模型时自动纯 RRF（P1 成果兜底）。
- 注意 Ollama 常驻（keep_alive 30m）与 rerank 串行的时序：rerank 在 embed 之后、chat 之前，
  不影响模型常驻。

## 7. 不做清单（明确拒绝，省得反复纠结）

| 不做 | 理由 |
|---|---|
| LangChain / LlamaIndex 全家桶 | 重依赖、抽象层厚，与本项目"直接、可测"风格冲突；要的只是 RRF/向量库这两个轮子 |
| Docling / MinerU 进主包 | torch 级依赖 + 模型文件，安装包从 48MB 级爆到 GB 级；扫描件已有 Windows OCR（0.3s/页）兜底 |
| MarkItDown | 与现有解析重叠，其 PDF 后端（pdfminer）反而弱于 PyMuPDF |
| 云端 rerank API | 破坏"数据不出机"承诺 |
| 从零训练/微调个人模型 | 已论证：个人数据微调对"事实问答"收益差，RAG 是正解（项目本身就是答案） |

## 8. 风险与已知约束（实施时必读）

- **Mimosa 钩子**：SQL 一律常量字符串 + `?` 参数（PRAGMA 除外，内联字面量）；测试里
  动态路径用 `tempfile.mkstemp(dir=...)`，别写 f-string 拼接（HANDOFF §4.0/4.1）。
- **FTS 触发器**：`chunk_fts` 必须走"独立表 + 显式触发器 + 逐行一致性重建"形态，
  external content + rebuild 在本机 SQLite 3.50.4 实测会坏库（ROADMAP 🔧 节）。
- **测试卫生**：每套测试独立一次性 APPDATA（名字含 `fb_captest`），watchdog stub；
  套件必须自足 `init_db()`。
- **打包**：不覆盖 `dist/FileButler`，用 `--distpath dist_verify --workpath build_verify`；
  ISCC 在 `%LOCALAPPDATA%\Programs\Inno Setup 6\`；出包先看 `Setup command line:` 确认
  `MSYS_NO_PATHCONV=1` 生效。
- **sqlite-vec 未知项**（P0 spike 覆盖）：pip 包对 cp314-win 的轮子兼容性、vec0 在
  813MB 真实库上的迁移耗时、与 WAL/mmap 共存表现。
- **存档向量**：`%APPDATA%\FileButler\archive\`（1,925 行 chunks 向量）迁移时恢复，勿删。

## 9. 验收口径（整个方案完成时）

1. 评测集 20~30 题：hit@8 与首命中排名均优于基线，专有名词类问题提升明显。
2. 检索链路三级降级完整：hybrid → 向量/关键词单路 → keyword，每级有测试。
3. 内存：知识库向量不再全量常驻；10 万块级语料检索 < 50ms。
4. 包体增量 < 30MB（P4 不装模型不计模型体积）。
5. 228 断言基线 + 新增套件全绿；安装包重出并在干净环境装过一次。

---

## 10. 执行结果（2026-10-02，v1.3.0）

### 10.1 P0 spike：sqlite-vec NO-GO（改写原 P2）

`tests/spikes/spike_sqlite_vec.py`，10 万 x 1024 维 float32、k=8、20 次取均值：

| 指标 | sqlite-vec 0.1.9 | numpy float32 | numpy float16（当时线网路径） |
|---|---|---|---|
| KNN 查询 | **661.7 ms/次** | **25.6 ms/次** | 504.6 ms/次 |
| top-8 重合率 | 100%（vs 两路对拍） | - | - |
| 单行增量写 | 59.7 ms | -（invalidate 全量重载） | - |
| 插入 10 万条 | 84.9 s | - | - |

结论：sqlite-vec 功能完好（WAL/双连接/结果正确性全过），但其 C 实现无 SIMD，
KNN 比本机 numpy BLAS 慢 26 倍——引入即负优化。**意外收获：float16 矩阵乘在
numpy 里没有 BLAS 路径，比 float32 慢约 20 倍**，即原 VectorIndex 在几万块规模
本身就是几百毫秒级。故 P2 改为 float32 精度修正，sqlite-vec 从 venv 卸载
（spike 脚本保留，日后语料到百万级可重新评估量化/ANN 方案）。

### 10.2 基线与最终数字（`tests/eval_retrieval.py`，29 题 12 篇合成语料）

| 通道 | hit@8 | MRR | 延迟 | 说明 |
|---|---|---|---|---|
| keyword（LIKE，旧降级） | 0% | 0.000 | ~0ms | 整句子串匹配，对自然语言问题**完全失效** |
| vector（bge-m3） | 100% | 0.983 | 12.6ms | 改写题 MRR 0.962 是短板 |
| fts（bigram+unicode61，零模型） | 97% | 0.966 | **0.2ms** | 精确实体题 100% |
| **hybrid（RRF 融合）** | **100%** | **1.000** | 13.1ms | 改写/实体两题型 MRR 均 1.000 |

规模延迟（2 万随机向量）：VectorIndex 查询 **103ms（float16）→ 2.9ms（float32，36 倍）**；
全量重载 341~511ms（该路径仍存在，invalidate 后首查含载入）。

### 10.3 落地改动清单

| 阶段 | 文件 | 内容 |
|---|---|---|
| P1 | db.py | chunks.btext 列 + 迁移回填；chunk_fts（unicode61 + CJK 二元切分，独立表+显式触发器）；chunk_btext/chunk_fts_expr/chunk_fts_search；一致性重建 |
| P1 | rag.py | _rrf_fuse + _retrieve 统一降级链 hybrid→vector/fts→keyword；search/ask 接入；mode 新增 "hybrid"/"fts" |
| P1 | indexer.py | 写 chunks 时生成 btext |
| P2 | db.py | VectorIndex float16→float32（矩阵与查询向量均归一化 float32，走 BLAS） |
| P3 | parsers.py | PARSE_VER="2"；PDF 主通道 fitz(sort=True) + pypdf 兜底 + 扫描件 OCR 链不变；docx 标题样式→"# " 行 |
| P3 | chunker.py | # / [第N页] 行硬边界；每块前置「文件名 · 所在标题」 |
| P3 | indexer.py | 增量判断加 parse_ver 比对（旧库下次索引自动全量重解析+重嵌入）；切块传文件名 |
| P3 | rag.py | ask() 上下文 600→1200 字符 + 按文件聚合排序 |
| P3 | db.py | kb_files.parse_ver 列 + 迁移 |
| 测试 | 新增 | tests/test_hybrid.py（6 项）、tests/test_parse_chunk.py（10 项）、tests/eval_retrieval.py（回归评测） |

### 10.4 与计划的偏差

- 计划中「设置页按新解析重建入口」未做：parse_ver 比对使**任意一次索引**（手动或
  watcher 自动）都会把旧解析文件自动重跑，等价覆盖了该需求；若要显式按钮，接
  现有 kb_index API 循环全部 folder 即可。
- P4（本地 rerank）未动，待产品拍板；未新增任何运行时依赖，requirements.txt 无变化。
- 升级注意：旧知识库**第一次索引会全量重解析+重嵌入**（parse_ver 迁移），大库请留出
  Ollama 运行时间；测试基线现为 11 套 253 断言全绿（新增 16 项）。

### 10.5 追加完善（并入未发布的 1.3.0）

| 项 | 文件 | 内容 |
|---|---|---|
| 多轮检索改写 | rag.py | ask() 新增检索改写步：history 非空且非清单意图时，用一次非流式小调用（temperature 0、num_predict 120、think=False）把追问改写成自包含检索词；异常/为空/超 100 字一律回退原 query，三条返回路径均附 rewritten_query；list-intent 判断与生成阶段行为不变 |
| 引用增强 | rag.py | sources 新增 title（按 chunker「文件名 · 所在标题」前缀行解析）与 locator（[第N页]/[工作表 X] → 「第3页」/「工作表 报价」），snippet 去掉前缀行；按答案末尾「来源 [n]」解析过滤出 cited（无引用为空，不伪造） |
| contentindex parse_ver 对齐 | contentindex.py | doc_content_state 加 parse_ver 列 + 旧库迁移；候选谓词 IS NOT 比对、入库写当前版本——解析管线升版后存量正文自动重抽；ensure() 补双检锁，消除升级后并发首调（设置页轮询 + 点开始索引）重复 ALTER 的 duplicate column 竞态 |
| LLM 结构化输出与幂等重试 | ollama_client.py、llm.py、classifier.py | 两客户端 chat() 新增 format 参数（Ollama 写 payload["format"]；OpenAI 包 json_schema response_format，400 不支持时自动去字段重试一次，仅非流式）；Ollama 端 400 走参数降级链 think→format 逐个剥离重试；classifier 批量/单个改传 BATCH_FORMAT/SINGLE_FORMAT（category 枚举 = CATEGORIES），正则 + json.loads 兜底保留；新增 post_retry 幂等重试（连接错误/超时/5xx/429 按 ×3 指数退避 0.5s→1.5s；其余 4xx 与流式不重试） |
| 文件相关度排序 | fileindex.py、Files.vue | search() 新增 sort_by="relevance"：仅文本真正走 FTS MATCH 时按 file_fts.rank（bm25 升序 = 最相关在前，rank 方向已 spike 实测），LIKE 降级/无文本/非法值照旧回退 mtime；前端排序下拉接入「相关度」 |

测试：新增 tests/test_kb_followup.py（46 项）、tests/test_contentver.py（17 项）、
tests/test_llm_robust.py（51 项，假 Ollama/OpenAI 兼容服务打桩，覆盖 format 降级链
与幂等重试）、tests/test_rel_sort.py（11 项）；tests/test_content_index.py 改为导入
backend 前自隔离一次性 APPDATA（fb_captest 防护检查保留）。

回归（统一基线）：13 套通过——test_core 25、test_fileindex 15、test_maint 17、
test_content_index 30、test_contentver 17、test_rel_sort 11、test_parse_chunk 10、
test_winocr 6、test_hybrid、test_kb_followup 46、test_llm_robust 51、test_batch2 35、
test_upgrade 15（test_ai_classify 为观测型、无断言）；5 套因环境未跑——test_datadir /
test_hardening / test_session_followups（APPDATA 一次性目录门禁）、test_llm_provider
（hybrid embed 项需 Ollama 运行，restored 检查依赖宿主库 llm_mode 配置）、test_rag
（需 Ollama + bge-m3）；按各套文档要求提供环境后本机实测均通过（25/41/43/15 项及
RAG 端到端）。

### 10.6 延迟优化：num_ctx 显式化 + 改写参数收紧（并入未发布的 1.3.0）

| 项 | 文件 | 内容 |
|---|---|---|
| num_ctx 显式下发 | ollama_client.py | chat() 给文本请求注入 options.num_ctx，默认 8192：v1.3.0 上下文扩到 1200 字符×8 块 + 多轮历史后 prompt 可达 5~8K token，而 Ollama 按模型默认窗口（2048/4096，随 Modelfile 而异）**静默截断**，扩容等于白做。调用方自带 num_ctx 优先；注入只改请求体副本、不污染调用方 dict；带 images 的视觉请求跳过（低配机显存保护）；设置项 ollama_num_ctx 可覆盖，"0"=不干预，非法值回落默认 |
| 设置页「上下文窗口」 | api.py、Settings.vue | save_ollama_settings 新增 num_ctx 参数（仅收数字，"0" 合法）；get_status 回显 ollama_num_ctx；模型卡片新增下拉（4096/8192 推荐/16384/32768/跟随模型默认）并更新说明文字 |
| 改写调用降配额 | rag.py | _rewrite_query 的 num_predict 120→60：改写产物是一条检索词（几十 token），配额超给只放大跑偏输出，本机 8B 上省约 1s 级体感延迟 |

测试：tests/test_llm_robust.py 新增 [8] 节 7 项（51→58：默认注入/调用方优先/
设置覆盖/图片跳过/"0" 关闭/非法值回落/dict 不被污染）；test_kb_followup 改写
参数断言同步为 60。

回归（Ollama 运行中）：18 套实跑全绿——fast 9 套（core 25 / fileindex 15 /
batch2 35 / content_index 30 / contentver 17 / rel_sort 11 / parse_chunk 10 /
winocr 6 / maint 17）+ llm_robust 58 + kb_followup 46 + followups 43 +
datadir 25 + hardening 41 + **test_rag 端到端（真实 Ollama，验证注入后链路）** +
llm_provider 15 + upgrade 15；npm run build 通过。

### 10.7 延迟收尾与存量清理（并入未发布的 1.3.0）

| 项 | 文件 | 内容 |
|---|---|---|
| global_search 两路 RRF 融合 | rag.py、api.py | 新增 rrf_merge_by_file（文件级多路 RRF，路内同文件只计最好名次、代表行保留先出现者展示形态）；global_search 的「全盘正文 FTS + 知识库块」从「FTS 恒排前」的拼接换成融合（P1 遗留顺手项） |
| 问答生成封顶 | rag.py | ask() 生成加 num_predict 1024：防模型跑飞时无界生成拖死会话（RAG 答案几百字足够，流式下用户早看到前文） |
| 测试卫生存量修复 | test_ai_classify.py、test_llm_provider.py | 两者导入 backend 前自隔离一次性 APPDATA（fb_captest 约定）+ init_db；此前 test_ai_classify 依赖真实库才跑得动、test_llm_provider 直跑会写真实用户库（finally 还原也不该碰） |

验收：eval_retrieval 真评测复跑（真实 bge-m3）——hybrid hit@8 100% / MRR 1.000，
与 §10.2 数字完全复现；2 万向量 float32 查询 3.0ms。回归：test_hybrid（新增
文件级融合 4 项）、test_batch2 35、test_kb_followup 46、test_llm_provider 15
（自隔离后无外部 APPDATA 首跑即绿）、test_rag 端到端、test_ai_classify 观测
3/4 符合（无断言，MISS 为模型判断波动）全过。README 更新至 1.3.0 现状
（安装包版本号、混合检索/多轮改写/相关度排序/来源定位能力描述）。

### 10.8 第二轮追加：拼音 / EXIF / eml / rerank（并入未发布的 1.3.0）

| 项 | 文件 | 内容 |
|---|---|---|
| 拼音搜文件名 | fileindex.py、db.py | pypinyin（新依赖，纯 Python）生成全拼/首字母两列；file_index 加 pinyin/pinyin_ini/photo_time 列（ensure_fts 内常量 ALTER 迁移）；file_fts 重建为四列（name/path/pinyin/pinyin_ini，trigram）+ 触发器带拼音 + _fts_resync 重灌带拼音；检索：≥3 字纯字母词走 FTS 命中拼音列（baogao/xmbg），<3 字走 LIKE 或 pinyin_ini（bg）；存量行启动后台分批回填（UPDATE 经触发器自动同步 FTS），新写入 upsert/scan 直接带值；pypinyin 缺失时拼音列写空、检索自动失效不报错 |
| EXIF 拍摄时间 | fileindex.py | photo_time 列（epoch 秒）：watcher 单文件路径读 EXIF（Pillow 惰性加载只读头），全量扫描不读（十几万张会拖长十几分钟）由回填线程补齐；时间过滤（mtime_from/to/dm:）改 COALESCE——有拍摄时间的照片按拍摄时间，「上个月的照片」不再被拷贝/整理打乱；排序字段语义不变；browse 结果暴露 photo_time |
| eml 邮件解析 | parsers.py、watcher.py | 标准库 email（policy.default）：主题/发件人/日期 + text/plain 正文（各段拼接），纯 HTML 邮件复用剥标签；附件不进正文；SUPPORTED 与 watcher _DOC_EXTS 加 eml |
| HTML 剥标签 | parsers.py | 旧实现整文件按文本读（script/style/标签噪音全进索引）；改为 HTMLParser 取正文，块级标签换行、h1-h3 转 md 标题行（chunker 按章节切块），编码 utf-8→gb18030 逐级尝试；PARSE_VER 2→3，旧 HTML 块自动重解析 |
| quick_search 相关度 | api.py | Ctrl+K 命中从 mtime 改按 file_fts.rank（bm25）排序 |
| rerank 精排（P4 落地） | knowledge/reranker.py（新）、rag.py、api.py、Settings.vue | onnxruntime + tokenizers（新依赖，进包 ~+40MB）；bge-reranker-v2-m3 int8 ONNX 544MB 设置页按需下载（hf-mirror 优先/官方站回退，.part 原子落盘）/删除；rag 集成：融合池扩到 RERANK_POOL=50 → CPU 精排（50 对 1~3s，实测热态 0.32s/15 对）→ top-8，分数经 sigmoid 归一；ask/search 均接入并附 reranked 标记；开关默认关，任何失败（运行时缺失/模型未下载/加载失败/打分异常）回退 RRF 原序；分数 float() 强转防 numpy 标量过桥 JSON 失败；设置页模型卡片含开关/下载进度/删除/状态 |
| 多轮改写真评测 | tests/eval_retrieval.py | 8 组追问（含纯指代对抗题：那负责人的电话多少）+ 120 篇确定性干扰文档（12 篇语料 hit@8 随机基线 67% 无区分度，扩到 132 篇降为 6%）：原词直查 hit@8=100% MRR=0.938，改写后 MRR=1.000，改写成功 8/8——量化「改写消除指代错排」 |

测试：tests/test_rerank.py 新增（24 项：ask/search 重排、失败/关闭/单候选回退、
池扩容断言、单元与真模型冒烟——无模型环境自动 SKIP 自足）；tests/test_pinyin.py
新增（19 项：纯函数/三通道检索/触发器同步/拍摄时间过滤/pypinyin 缺失降级）；
test_parse_chunk 扩到 22 项（HTML 剥标签/gb18030/eml 头与正文/HTML 邮件/PARSE_VER）。

回归：20 套全绿（fast 12 + 特殊 APPDATA 3 + Ollama 依赖 5，含 rerank/pinyin 两套
新增）；py_compile 全仓通过。依赖：requirements.txt 新增 pypinyin>=0.51、
onnxruntime>=1.17、tokenizers>=0.15；spec 收集三者（惰性导入需显式 collect_all）。
安装包：PyInstaller onedir（dist_verify）+ Inno Setup → installer/FileButler-Setup-1.3.0.exe，
包体增量约 +40MB（onnxruntime + tokenizers + pypinyin；模型文件不进包）。
