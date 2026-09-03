# FileButler · 本地智能文件管家

完全本地运行的文件整理助手 + 知识库问答。**不需要任何 API Key，不花一分钱**——AI 能力由本机 [Ollama](https://ollama.com) 免费开源模型提供。

## 功能

- **自动文件编目（Everything 式，只读）**：启动自动扫描桌面/文档/下载/图片等用户目录，后台实时监控增删改，新文件几秒内可搜
- **搜索语法**：`ext:pdf`、`size:>100mb`、`dm:本周`、`path:桌面`、`cat:图片`，可与关键词混用；长词走 FTS5 全文索引
- **内置预览**：文本/代码直接看，Markdown 渲染，PDF 窗口内打开，图片大图预览（Ctrl+F 聚焦搜索，双击打开文件）
- **自动知识库**：监控目录的新文档自动向量化入库（可在设置关闭），无需手动索引即可内容搜索与问答
- **问答历史**：对话自动保存，左侧栏回看/继续/删除历史会话
- **图片语义搜索 + OCR（可选）**：视觉模型（qwen2.5vl）生成描述并转录图中文字，搜「日落」找照片、搜发票号找截图
- **智能文件整理**：手动触发，规则 + 本地大模型批量分类 → 预览确认 → 执行移动 → 全程可撤销（**绝不会自动移动文件**）；整理方案可存为模板
- **重复文件检测与安全清理**：三级哈希找重 → 按规则（最新/最早/路径最短）保留 → 移入「待清理」文件夹（不删除，可撤销）
- **每周文件报告**：本周新增/体积/类别分布/大文件排行，首页自动展示
- **数据库备份**：退出时每日自动备份保留 5 份，设置页可手动备份/恢复
- **数据目录可迁移**：设置页一键搬迁数据库与缓存到任意位置（指针文件方案，含完整性校验）
- **模型管理**：查看已安装模型与占用空间，一键下载/删除（删除前确认）
- **开机自启 + 托盘常驻 + 深色模式**：关窗最小化到托盘，侧栏一键切换明暗主题
- **隐私**：数据全部存本机，不访问任何云端服务

## 快速开始

### 方式一：直接运行打包版

1. 双击 `dist\FileButler\FileButler.exe`
2. 前往「设置」页确认 Ollama 状态（本机已装 Ollama 会自动检测）
3. 若缺模型，在设置页一键下载：对话模型（qwen2.5 系列）+ 向量模型 bge-m3（约 1.2 GB）

### 方式二：开发模式

```bash
# 后端（Python 3.10+）
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 前端（Node 18+）
cd frontend && npm install

# 终端 1：Vite 开发服务器
cd frontend && npm run dev

# 终端 2：桌面窗口（连接开发服务器，支持热更新）
.venv\Scripts\python main.py --dev
```

### 重新打包

```bash
cd frontend && npm run build && cd ..
.venv\Scripts\python -m PyInstaller --noconfirm --onedir --windowed --name FileButler ^
  --add-data "frontend/dist;frontend/dist" --collect-all webview main.py
```

## Ollama 模型建议

设置页会自动探测本机内存与显卡（显存），推荐最合适的 Qwen3 模型并给出下载入口：

| 配置 | 推荐模型 | 大小 |
|---|---|---|
| 24GB+ 显存（RTX 3090/4090 等） | qwen3:32b | ~22 GB |
| 12~16GB 显存（RTX 4070/4060Ti 等） | qwen3:14b | ~10 GB |
| 8GB 显存 / 16GB+ 内存（无独显） | qwen3:8b | ~6 GB |
| 8~16GB 内存（低配/纯 CPU） | qwen3:4b | ~3 GB |

向量模型固定用 `bge-m3`（必装，~1.2 GB）。图片语义搜索需另装视觉模型 `qwen2.5vl:7b`（~6 GB，可选）。

命令行手动拉取：`ollama pull qwen3:8b` / `ollama pull bge-m3`

## 云端 API 接入（可选）

不想部署本地模型的用户可在 **设置 → 模型接入方式** 切换到「云端 API（OpenAI 兼容）」：

- 内置常用服务商快捷填入：DeepSeek、智谱 GLM、Kimi、通义千问、硅基流动、OpenRouter、OpenAI，也支持任何 OpenAI 兼容端点（vLLM / LM Studio 等）
- **省钱混搭**：对话走云端（质量好），向量化留在本地 bge-m3（完全免费）——两者可独立配置
- 密钥仅保存在本机 SQLite，除所填服务商外不经过任何第三方；界面上不回显
- 保存前可「测试连接」验证密钥与模型名

## 安全设计

- 所有文件移动前必须经过「预览 → 勾选确认」，绝不静默移动
- 每一步操作写入日志（时间、原路径、新路径），按批次一键撤销
- 目标已存在的文件自动改名 `(1)`、`(2)`，绝不覆盖
- Ollama 未就绪时自动降级：规则整理 + 关键词搜索照常可用

## 测试

```bash
.venv\Scripts\python tests\test_core.py         # 整理引擎（不需要 Ollama）
.venv\Scripts\python tests\test_fileindex.py    # 自动扫描/监控/缩略图（不需要 Ollama）
.venv\Scripts\python tests\test_batch2.py       # 语法搜索/FTS/清理/预览/QA历史/周报/备份/模板
.venv\Scripts\python tests\test_datadir.py      # 数据目录切换
.venv\Scripts\python tests\test_upgrade.py      # 批量索引/自动知识库/自启（需 Ollama）
.venv\Scripts\python tests\test_ai_classify.py  # AI 分类实测
.venv\Scripts\python tests\test_rag.py          # 知识库端到端
```

## 目录结构

```
main.py               入口（托盘常驻；--dev 连接 Vite 开发服务器）
backend/
  api.py              pywebview JS 桥接 API + 自动化装配
  ollama_client.py    Ollama 客户端（批量 embed / 模型下载删除 / 图片理解）
  db.py               SQLite + 向量检索（文档块 + 图片描述两套索引）
  fileindex.py        文件总索引（自动编目 + 文件名搜索）
  watcher.py          watchdog 实时监控（防抖 + 自动知识库/图片队列）
  thumbserver.py      缩略图/预览服务（仅 127.0.0.1，白名单校验）
  core/               扫描 / 规则 / 批量分类 / 计划 / 执行撤销 / 去重
  knowledge/          解析 / 切块 / 索引 / RAG / 图片语义
frontend/             Vue 3 + Naive UI（Vite 构建为单文件内嵌）
tests/                自动化测试
```
