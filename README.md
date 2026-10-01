<div align="center">
  <img src="icon/picture.png" width="130" alt="FileButler">
  <h1>FileButler · 本地智能文件管家</h1>
  <p><b>把杂乱的硬盘，变成能搜索、能提问的私人资料库。</b></p>
  <p>
    <a href="https://github.com/7SteveJohn/FileButler/releases/latest"><img src="https://img.shields.io/github/v/release/7SteveJohn/FileButler?color=4f8cff&label=release" alt="release"></a>
    <img src="https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6?logo=windows&logoColor=white" alt="platform">
    <img src="https://img.shields.io/badge/data-100%25%20local-2ea44f" alt="local">
    <img src="https://img.shields.io/badge/API%20Key-not%20required-orange" alt="no api key">
  </p>
  <p>
    <a href="https://github.com/7SteveJohn/FileButler/releases/latest"><b>↓ 下载安装包</b></a>
    &nbsp;·&nbsp; <a href="#它到底能做什么">能做什么</a>
    &nbsp;·&nbsp; <a href="#常见问题">常见问题</a>
    &nbsp;·&nbsp; <a href="#技术实现">技术实现</a>
  </p>
</div>

---

FileButler 是一个**完全跑在你自己电脑上**的文件管家：替你记住硬盘里有什么、让你用大白话找到文件、把文档变成能问答的知识库，并且在**动你文件之前先给你看一遍**。

不需要任何 API Key，不花一分钱——AI 能力由本机 [Ollama](https://ollama.com) 免费开源模型提供。不满足条件时也能用：没装 Ollama，规则整理 + 关键词搜索照常工作。

| 你现在的麻烦 | FileButler 的做法 |
|---|---|
| 找一份三个月前的 PDF，只记得大概写了什么 | 文件名、正文、图片文字一起搜；也可以直接问「那份报价单里写了什么」 |
| 想让 AI 读自己的文档，但不想上传到别人服务器 | 文档在本机解析、切块、向量化，本地模型作答，数据一步不出电脑 |
| 文件夹乱得不敢动，怕一整理就找不到 | 先出整理方案 → 逐条预览勾选 → 执行 → 不满意一键撤销 |
| 重复文件删完才后悔 | **不删除**，移进「待清理」文件夹，随时还原 |
| 装个 AI 又要账号又要充值 | 本地模型零成本，密钥一个都不用填 |

## 30 秒上手

1. 到 [Releases](https://github.com/7SteveJohn/FileButler/releases/latest) 下载 `FileButler-Setup-1.2.1.exe`（约 45 MB），双击安装——**不需要管理员权限**，装在当前用户目录下
2. 首次启动自动扫描桌面 / 文档 / 下载 / 图片等用户目录，几秒后就能搜到东西
3. 想要「问答」和「按意思搜」：装 [Ollama](https://ollama.com)，设置页按显卡推荐一键下载模型（`qwen3` 系列 + 向量模型 `bge-m3`）
4. 已经有 LM Studio 或云端 API？设置页「模型接入方式」直接切，不必装 Ollama

> **系统要求**：Windows 10 / 11 64 位。搜索、预览、整理、去重全部本地完成，不依赖任何外部服务。

## 它到底能做什么

### 🔍 找文件：比「只看文件名」多懂一点

- **自动文件编目（Everything 式，只读）**：启动自动扫描桌面 / 文档 / 下载 / 图片等用户目录，后台实时监控增删改，新文件几秒内可搜
- **搜索语法**：`ext:pdf`、`size:>100mb`、`dm:本周`、`path:桌面`、`cat:图片`，可与关键词混用；长词走 FTS5 全文索引
- **自然语言搜索**：搜「上个月那份报价单」也能命中，走本地向量索引（需 Ollama）
- **图片语义搜索 + OCR**：视觉模型（qwen2.5vl）为图片生成描述并转录图中文字，搜「日落」找照片、搜发票号找截图；没有视觉模型时自动降级到 Windows 内置 OCR
- **内置预览**：文本 / 代码直接看，Markdown 渲染，PDF 窗口内打开，图片大图预览；`Ctrl+F` 聚焦搜索，双击打开文件
- **筛完不用重来**：当前筛选条件可一键保存，下次点开即用；常看的文件加星收藏、打标签、写备注
- **批量操作**：多选后移动到 / 复制到 / 重命名 / 删除（进回收站）、复制路径
- **两个顺手工具**：对图片「OCR 取字」直接抄文字；对任意文件「哈希校验」算 SHA-256

### 💬 问文档：把资料变成会回答的知识库

- **自动知识库**：监控目录里的新文档自动解析、切块、向量化入库（可在设置关闭），不用手动建索引即可内容搜索与问答
- **问答 + 检索**：知识库页既能只搜原文片段（仅搜索），也能让本地模型基于原文作答
- **任意目录建库**：手动添加文件夹即可纳入知识库
- **问答历史**：对话自动保存，左侧栏回看 / 继续 / 删除历史会话

### 🗂️ 整理与清理：动手前先给你看一遍

- **智能文件整理**：手动触发，规则 + 本地大模型批量分类 → 生成整理计划 → 逐条预览勾选 → 执行移动 → **全程可撤销**（也能重做）；整理方案可存为模板
- **重复文件检测与安全清理**：三级哈希找重 → 按规则（最新 / 最早 / 路径最短）保留 → 移入「待清理」文件夹（不删除，可撤销）
- **顺带两个体检**：查找大文件、检测冲突副本
- **绝不静默移动**：每一步操作写入日志（时间 / 原路径 / 新路径），可按批次一键撤销

### 🖥️ 用起来顺手

- **概览页**：本地文件库规模、本周文件动态、空间占用（按类别 + 占用 TOP 目录）一屏看清
- **每周文件报告**：本周新增 / 体积 / 类别分布 / 大文件排行，可导出
- **模型管理**：自动探测内存与显存推荐合适模型，已安装模型与占用空间一目了然，一键下载 / 删除（删除前确认）
- **托盘常驻 + 全局热键**：关窗最小化到托盘，默认 `Alt+Space` 随时唤起；开机自启可选
- **深色模式**：跟随系统或手动切换
- **数据目录可迁移**：设置页一键搬迁数据库与缓存到任意位置（指针文件方案，含完整性校验）
- **数据库备份**：退出时每日自动备份保留 5 份，设置页可手动备份 / 恢复 / 压缩
- **配置导入导出**：换机器搬设置不用重新配
- **检查更新**：从 GitHub Releases 拉取新版本提示
- **监控目录与排除规则**：添加监控目录、排除目录、自定义规则，都在设置页搞定

### 🔒 隐私

- 数据全部存本机 SQLite，不访问任何云端服务
- 云端 API **可选**：密钥仅保存在本机，界面打码不回显，保存前可「测试连接」验证
- 可以「对话走云端、向量化留本地」——省钱混搭，两者独立配置

## 界面一览

| 页面 | 干什么 |
|---|---|
| **概览** | 库规模、本周动态、空间占用排行 |
| **文件** | 搜索 + 预览 + 收藏标签 + 批量操作，日常主战场 |
| **文件整理** | 生成整理计划、重复检测、大文件与冲突副本体检、批次撤销 |
| **知识库** | 选文件夹建库、问答、仅搜索、历史会话 |
| **设置** | 模型接入与下载、监控与排除目录、数据迁移、备份、更新 |

<!-- 截图位（建议补 3~5 张：概览 / 文件搜索结果 / 整理计划预览 / 知识库问答 / 设置页模型管理）
     图片放进 docs/screenshots/ 后，用下面的写法替换本注释即可：
     <p align="center"><img src="docs/screenshots/files.png" width="760" alt="文件页"></p>
-->

## 常见问题

**必须装 Ollama 吗？**
不必须。搜索语法、预览、重复检测、规则整理、Windows 内置 OCR 都不依赖模型；只有「问答、自然语言搜索、AI 分类」需要。

**会把我的文件传上网吗？**
不会。默认全程本地。只有你自己在设置里填了云端 API 地址，才会把**你问的那段文字**发出去，文件本身不上传。

**整理文件会不会把文件弄丢？**
不会静默移动。所有移动先出计划给你逐条勾选；执行后写日志，可按批次撤销；目标重名自动改 `(1)`、`(2)`，绝不覆盖；重复文件只移进「待清理」，不删除。

**8GB 显存的显卡能跑吗？**
能。设置页会按显存自动推荐，8GB 档对应 `qwen3:8b`（约 6 GB）。

**支持 Mac / Linux 吗？**
目前只打包 Windows 10 / 11 64 位。

**怎么提需求、报 bug？**
直接开 [Issue](https://github.com/7SteveJohn/FileButler/issues) 说就行。

## 模型建议

设置页会自动探测本机内存与显卡（显存），推荐最合适的 Qwen3 模型并给出下载入口：

| 配置 | 推荐模型 | 大小 |
|---|---|---|
| 24GB+ 显存（RTX 3090/4090 等） | qwen3:32b | ~22 GB |
| 12~16GB 显存（RTX 4070/4060Ti 等） | qwen3:14b | ~10 GB |
| 8GB 显存 / 16GB+ 内存（无独显） | qwen3:8b | ~6 GB |
| 8~16GB 内存（低配/纯 CPU） | qwen3:4b | ~3 GB |

向量模型固定用 `bge-m3`（必装，~1.2 GB）。图片语义搜索需另装视觉模型 `qwen2.5vl:7b`（~6 GB，可选）。

命令行手动拉取：`ollama pull qwen3:8b` / `ollama pull bge-m3`

## 模型接入方式

**本地 Ollama（默认，免费）**：设置页自动检测。对话与向量模型默认「常驻 30 分钟」，
不会因空闲 5 分钟被卸载而每次重新载入（大模型载入要几十秒）；内存充裕可开
「启动时预载 + 常驻不卸载」，首次问答零等待。

**LM Studio（本机，免费）**：启动 LM Studio 的本地服务后，在「设置 → 模型接入方式」
切到「云端 API（OpenAI 兼容）」，点「检测本机服务」即可一键填入（默认
`http://127.0.0.1:1234/v1`，无需密钥），「拉取模型列表」直接选模型；向量化也走
LM Studio 的 embedding 模型，全程不装 Ollama。

**云端 API（OpenAI 兼容，可选）**：

- 内置常用服务商快捷填入：DeepSeek、智谱 GLM、Kimi、通义千问、硅基流动、OpenRouter、OpenAI，也支持任何 OpenAI 兼容端点（vLLM / LM Studio 等）
- 省钱混搭：对话走云端（质量好），向量化留在本地 `bge-m3`（完全免费）——两者可独立配置
- 密钥仅保存在本机 SQLite，除所填服务商外不经过任何第三方；界面上不回显
- 保存前可「测试连接」验证密钥与模型名

## 技术实现

<details>
<summary>点开看架构、技术栈与目录结构</summary>

界面 Vue 3 + Naive UI，外壳 pywebview（Chromium 内核），后端 Python 3.10+；
存储用 SQLite（FTS5 全文索引 + 文档块向量索引 + 图片描述索引）；
实时监控用 watchdog（带防抖）；预览服务只监听 `127.0.0.1` 并做白名单校验；
打包走 PyInstaller（onedir）+ Inno Setup 安装包。

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

</details>

## 开发与打包

<details>
<summary>点开看</summary>

### 开发模式

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

### 测试

```bash
.venv\Scripts\python tests\test_core.py         # 整理引擎（不需要 Ollama）
.venv\Scripts\python tests\test_fileindex.py    # 自动扫描/监控/缩略图（不需要 Ollama）
.venv\Scripts\python tests\test_batch2.py       # 语法搜索/FTS/清理/预览/QA历史/周报/备份/模板
.venv\Scripts\python tests\test_datadir.py      # 数据目录切换
.venv\Scripts\python tests\test_hardening.py    # 连接回收/恢复备份/watcher兜底/Host校验（需一次性 APPDATA）
.venv\Scripts\python tests\test_upgrade.py      # 批量索引/自动知识库/自启（需 Ollama）
.venv\Scripts\python tests\test_ai_classify.py  # AI 分类实测
.venv\Scripts\python tests\test_rag.py          # 知识库端到端
```

</details>
