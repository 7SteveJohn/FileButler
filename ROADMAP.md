# FileButler 功能规划（整合版）

> 整合来源：既有功能清单（体验类/功能类/工程类）、搁置记录（A/B/C）、与代码现状逐一核实的结果。
> 核实日期：2026-08-17。标注「已就绪」的项为后端代码已存在、仅缺前端接线或默认关闭。

---

## v1.1.2（2026-08-23）

| 功能/优化 | 说明 | 状态 |
|---|---|---|
| **Windows 内置 OCR（零模型）** | `backend/knowledge/winocr.py`：图片文字搜索与扫描件 PDF（原 #13）不再依赖 qwen2.5vl 视觉模型；视觉模型在场时仍优先走模型（描述+OCR+向量），缺失时降级系统 OCR；CJK 空格归一化保证关键词可搜 | ✅ |
| **性能：启动与后台占用** | 24h 内跳过全盘重扫；SQLite 线程本地连接复用；扫描线程 BELOW_NORMAL；scandir 扫描（1.9x）；space/dir_tree 缓存+流式；托盘隐藏期不向前端推 JS（显示时 lib_resync 自愈）；Ollama 状态后台预热；修复 weekly_rescan 线程因 pywebview 6.x 无 `window.closing` 启动即崩的存量 bug | ✅ |
| **数据库维护** | 备份改官方 `sqlite3.Connection.backup()`（活库/WAL 安全，替代 checkpoint+复制）；未变更自动跳过（退出不再每天复制大库）；设置页新增「数据库维护」卡片（库大小/压缩 VACUUM/备份数）；FTS 一致性重建移后台线程（防启动卡死数分钟）；同秒备份文件名冲突修复 | ✅ |
| 索引质量 | 扫描跳过目录联接 junction/符号链接（不误录 `C:\Users\All Users` 等重解析点） | ✅ |

---

## 0. 核实结论（重要：清单中有 5 项其实已实现）

| 清单条目 | 代码现状 | 结论 |
|---|---|---|
| 定时兜底重扫「没有每周自动核对」 | `main.py` 的 `weekly_rescan` 线程已存在：每小时检查一次，距上次全量扫描超 7 天自动重扫 | ✅ **已实现**，无需开发 |
| 全局热键「没有」 | `main.py` 已有 pynput 全局热键线程 + `hotkey_enabled`/`hotkey_combo` 设置项（默认 Alt+Space） | 🟡 **已就绪**，但默认关闭、设置页无 UI |
| 周报「只能看不能导出」 | `api.py` 的 `weekly_report_html` / `save_weekly_report` 已实现（导出 HTML 到数据目录 reports/） | 🟡 **已就绪**，前端无按钮 |
| 监控目录自定义排除「加不了」 | `exclude_rules` 表 + `scanner.py:39`/`fileindex.py:196` 扫描时读取排除 + `add_exclude`/`remove_exclude` API 齐全 | 🟡 **已就绪**，设置页无 UI |
| 搜索排序「固定按修改时间」 | `browse_files` 已支持 `sort_by=mtime/name/size/ext` + `asc/desc` | 🟡 **已就绪**，文件页无排序控件 |

**归纳**：这 5 项的缺口集中在「前端 UI 接线 + 默认开启」，每项都是小时级工作量，是性价比最高的第一波。

---

## 1. 待开发清单（按优先级）

### P0 · 半小时~半天级（纯前端接线 / 小改动）—— ✅ 已于 2026-08-17 全部完成

| # | 功能 | 需要做的 | 工作量 |
|---|---|---|---|
| 1 | **全局热键 UI** | Settings.vue 加热键开关 + 组合键输入框；main.py 增加 Win→cmd 键名规范化 | ✅ 完成 |
| 2 | **周报导出按钮** | Dashboard 周报卡片加「保存 HTML」，调用 `save_weekly_report` | ✅ 完成 |
| 3 | **监控排除规则 UI** | Settings.vue 加「排除目录」管理区（增删列表） | ✅ 完成 |
| 4 | **搜索排序控件** | Files.vue 加排序下拉（名称/大小/类型/时间 × 升/降序），走 `browse_files` 现有参数 | ✅ 完成 |
| 5 | **灯箱翻图** | Files.vue 大图预览支持 ←/→ 切换上一张/下一张（本页图片内循环 + 计数） | ✅ 完成 |

> 验证：`test_batch2` 32 项全过、`test_fileindex` 15 项全过、`npm run build` 通过、产物含全部新功能字符串。

### P1 · 1~3 天级（真实功能开发）—— ✅ 已于 2026-08-17 全部完成

| # | 功能 | 需要做的 | 工作量 |
|---|---|---|---|
| 6 | **Office 预览** | 新增 `backend/office_preview.py`：docx（python-docx 段落+表格）/ xlsx（openpyxl 工作表表格）/ pptx（python-pptx 形状文本）→ 全转义 HTML；`preview_file` 返回 `type=html`，前端新增渲染与表格样式；`.doc/.xls/.ppt` 老格式维持文本兜底 | ✅ 完成 |
| 7 | **相似图片检测** | 新增 `backend/core/simimg.py`：dHash 感知哈希（Pillow）+ 双桶分桶 + 汉明距离 ≤10 并查集分组，低信息量纯色图自动过滤；Organizer 新增「相似图片」页签，复用 dedupe_cleanup 移入待清理 | ✅ 完成 |
| 8 | **文件标签系统** | 新增 `tags`/`file_tags` 表 + list/add/delete_tag、list/set_file_tags API；`fileindex.search` 支持 `tag_ids` 过滤 + 行内 tags 列（GROUP_CONCAT）；Files 页行内打标（NPopover + 可创建新标签）+ 顶部按标签筛选 | ✅ 完成 |

### P2 · 功能增强

| # | 功能 | 需要做的 | 工作量 |
|---|---|---|---|
| 9 | **定时/自动整理** | 新增 `backend/core/autoorganize.py` + watcher `on_organize` 钩子：新文件落库后按规则匹配，report 模式仅出建议（默认）、move 模式自动移动并记批次可撤销；Settings 自动化卡片加开关/模式/目标目录 | ✅ 完成 |
| 10 | **图片语义搜索落地** | 代码已就绪（img_index + 描述/OCR + Settings 下载入口），只需下载 qwen2.5vl:7b（~6GB） | 无需开发 |

### P3 · 工程化

| # | 功能 | 需要做的 | 工作量 |
|---|---|---|---|
| 11 | **安装包 + 版本号** | `backend/__init__.py` 增加 `__version__="1.1.0"`；`app_version` API + 设置页「关于」卡片；窗口标题带版本号；新增 `FileButler.iss`（Inno Setup 安装脚本，需 ISCC 编译出 Setup.exe） | ✅ 脚本完成，未编译 |
| 12 | **国际化** | 全界面 i18n（中文/英文），工作量大，建议后置 | 大 |
| 13 | **扫描版 PDF OCR** | OCR 目前仅覆盖图片；扫描件 PDF 需逐页渲染再走图片 OCR，可借 vl 模型曲线实现 | 大（搁置） |

### 🔧 顺带修复：FTS5 全文索引损坏 bug（must-fix 级别）

排查测试失败时发现：`db._ensure_fts` 原用**外部内容表**（`content='file_index'`）+ `'rebuild'` 命令，在 SQLite 3.50.4 + trigram + WAL 下 rebuild 后触发器任何写操作（删除一行中文名文件）即报 `database disk image is malformed`。已改为**独立 FTS 表 + 显式 DELETE 触发器 + 逐行一致性重建**（旧库自动迁移），FTS 中文子串搜索正常。

### P4 · 文件操作层（日常基本盘）—— ✅ 已于 2026-08-17 完成

| # | 功能 | 实现 | 状态 |
|---|---|---|---|
| 14 | **批量重命名** | `backend/trash.py`（回收站工具）+ `api.rename_files`（冲突自动加序号绝不覆盖、记日志、索引同步）；Files 列表多选 → 重命名弹窗（序列编号 / 查找替换 / **AI 一键起名**，本地模型生成建议名可手动改） | ✅ 完成 |
| 15 | **批量删除到回收站** | `api.trash_files`：SHFileOperationW + FOF_ALLOWUNDO 送系统回收站（可恢复），记日志 + 索引清理；Files 多选 → 「删除到回收站」 | ✅ 完成 |
| 16 | **空间占用分析** | `api.space_analysis`：按类别/父目录聚合（TOP 100 目录）；Dashboard 首页新增「空间占用」卡片（类别占比条 + TOP 目录，点击打开） | ✅ 完成 |
| 17 | **批量复制路径** | Files 多选 → 「复制路径」（换行拼接进剪贴板） | ✅ 完成 |

> 验证：新 API 冒烟 9 项全过（冲突加序号/改名+日志/回收站+日志/AI 降级）、`test_batch2` 32 过、`test_fileindex` 15 过、构建通过。

---

## 2. 建议实施顺序

1. **第一波（约 1 天）**：P0 的 5 项 —— 纯接线，体验提升最明显，风险最低 ✅ **已完成（2026-08-17）**
2. **第二波（2~3 天）**：Office 预览 + 相似图片检测 —— 从「能用」到「好用」的跨越 ✅ **已完成（2026-08-17）**
3. **第三波**：文件标签 + 定时整理（可选项形式） ✅ **已完成（2026-08-17）**
4. **第四波**：打包发布链路（Inno + 版本 + 关于/更新）—— 脚本已就绪，待装 Inno Setup 编译 Setup.exe
5. **第五波（后续可选）**：国际化 / 扫描版 PDF OCR / 应用自更新

---

## 3. 附录：清单来源对照

- 体验类（Office 预览 / 全局热键 / .doc·.xls·.ppt）→ 对应 #6、#1、#6
- 功能类（定时整理 / 相似图片 / 标签收藏 / 周报导出）→ 对应 #9、#7、#8、#2
- 工程类（安装包版本号 / 国际化）→ 对应 #11、#12
- A（图片语义搜索就绪）→ #10
- B（扫描版 PDF OCR / 排除规则 / 定时重扫 / 灯箱翻图 / Office 预览）→ #13、#3、✅已实现、#5、#6
- C（全局热键 / 收藏标签 / 搜索排序 / 周报导出）→ #1、#8、#4、#2
