<script setup>
import { ref, computed, onUnmounted, h, onMounted } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NTag, NDataTable, NEmpty, NSpin, NAlert,
  NRadioGroup, NRadioButton, NPagination, NModal, NTooltip, useMessage, NProgress, NSelect,
  NPopover,
} from 'naive-ui'
import { api, on } from '../lib/bridge'

const message = useMessage()

// ---------- 状态 ----------
const query = ref('')
const category = ref(null)
const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 120
const thumbBase = ref(null)
const preview = ref(null)           // {path, url, index}
const scanning = ref(null)          // {stage, i, n, detail}
const lastEvent = ref(null)
const exporting = ref(false)
const sortBy = ref('mtime')
const sortOrder = ref('desc')
const sortOptions = [
  { label: '修改时间', value: 'mtime' },
  { label: '名称', value: 'name' },
  { label: '大小', value: 'size' },
  { label: '类型', value: 'ext' },
]
const orderOptions = [
  { label: '降序', value: 'desc' },
  { label: '升序', value: 'asc' },
]
// ---------- 标签 ----------
const tagOptions = ref([])      // {label, value:id}
const tagFilter = ref([])       // 筛选选中的标签 id
const tagSel = ref({})          // path -> [tag_ids]（行内编辑暂存）

async function loadTags() {
  try {
    const r = await api('list_tags')
    tagOptions.value = (r.tags || []).map((t) => ({ label: t.name, value: t.id }))
  } catch (e) { /* 忽略 */ }
}

async function saveTags(r) {
  const ids = tagSel.value[r.path] || []
  const ok = await api('set_file_tags', r.path, ids)
  if (ok && ok.ok !== false) {
    const names = tagOptions.value.filter((o) => ids.includes(o.value)).map((o) => o.label)
    r.tags = names.length ? names.join('|') : null
    message.success(names.length ? '标签已保存' : '已清除标签')
    loadTags()
  } else {
    message.error('保存失败')
  }
}

async function createTag(label) {
  const r = await api('add_tag', label)
  await loadTags()
  return r.id
}

// ---------- 批量操作（重命名 / 删除到回收站 / 复制路径） ----------
const checkedRows = ref([])
const renameModal = ref(false)
const renameMode = ref('seq')
const renamePrefix = ref('文件')
const renameStart = ref(1)
const renameFind = ref('')
const renameReplace = ref('')
const renameRows = ref([])      // [{src, new_name}]
const renameLoading = ref(false)

const baseName = (p) => p.split(/[\\/]/).pop()

function genRenameRows() {
  const sel = checkedRows.value
  if (renameMode.value === 'seq') {
    renameRows.value = sel.map((it, i) => ({
      src: it.path,
      new_name: renamePrefix.value + (renameStart.value + i) + '.' + it.ext,
    }))
  } else {
    renameRows.value = sel.map((it) => ({
      src: it.path,
      new_name: it.name.replaceAll(renameFind.value || '', renameReplace.value),
    }))
  }
}

async function aiSuggest() {
  if (!checkedRows.value.length) return
  renameLoading.value = true
  try {
    const r = await api('ai_rename', JSON.parse(JSON.stringify(checkedRows.value)))
    if (r.suggestions.length) {
      renameRows.value = r.suggestions
      message.success(`AI 已生成 ${r.suggestions.length} 个建议名，可手动修改`)
    } else {
      message.warning('AI 未返回结果（模型未就绪或输出无法解析）')
    }
  } catch (e) { message.error(String(e)) }
  renameLoading.value = false
}

function openRename() {
  renameMode.value = 'seq'
  genRenameRows()
  renameModal.value = true
}

async function doRename() {
  const rows = renameRows.value
    .filter((r) => r.new_name && r.new_name.trim() && r.new_name.trim() !== baseName(r.src))
    .map((r) => ({ src: r.src, new_name: r.new_name.trim() }))
  if (!rows.length) return message.warning('没有需要重命名的文件')
  const r = await api('rename_files', JSON.parse(JSON.stringify(rows)))
  const ok = r.results.filter((x) => x.ok).length
  message.success(`重命名完成：${ok}/${r.results.length} 个`)
  renameModal.value = false
  checkedRows.value = []
  refresh()
}

async function trashSelected() {
  const paths = checkedRows.value.map((it) => it.path)
  const ok = window.confirm(`将 ${paths.length} 个文件/文件夹移入系统回收站（可随时恢复）？`)
  if (!ok) return
  const r = await api('trash_files', JSON.parse(JSON.stringify(paths)))
  const okN = r.results.filter((x) => x.ok).length
  message.success(`已移入回收站 ${okN}/${paths.length}`)
  checkedRows.value = []
  refresh()
}

async function copyPaths() {
  const text = checkedRows.value.map((it) => it.path).join('\n')
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制路径')
  } catch (e) { message.error('复制失败') }
}

let searchTimer = null
let offs = []
offs.push(on('lib_scan_start', () => { scanning.value = { stage: 'scan', i: 0, n: 0, detail: '' } }))
offs.push(on('lib_scan_progress', (p) => { scanning.value = p }))
offs.push(on('lib_scan_done', (p) => {
  scanning.value = null
  if (p.result && p.result.added != null) {
    message.success(`文件库已更新：新增 ${p.result.added} · 更新 ${p.result.updated} · 清理 ${p.result.removed}（共 ${p.stats.files} 个文件）`)
  }
  refresh()
}))
offs.push(on('lib_scan_error', (e) => { scanning.value = null; message.error(String(e)) }))
offs.push(on('lib_file_event', (e) => { lastEvent.value = e }))
// 窗口从托盘恢复：隐藏期间事件被后端跳过，重新拉一次数据自愈
offs.push(on('lib_resync', () => { scanning.value = null; lastEvent.value = null; refresh() }))
onUnmounted(() => offs.forEach((f) => f()))

// ---------- 搜索 ----------
const searchHistory = ref([])
const dirTreeData = ref([])
const dirExpanded = ref(false)
const dirTreePattern = ref('')   // 目录树搜索（filterable）
const currentDir = ref(null)   // 当前目录过滤（null = 全部）
// 默认只展开 5 个盘根（避免万级节点卡 DOM）
const defaultExpandedDirs = computed(() => dirTreeData.value.map(n => n.path))

async function loadSearchHistory() {
  try { searchHistory.value = (await api('search_history')).history } catch (e) { /* 忽略 */ }
}
async function loadDirTree() {
  try { dirTreeData.value = (await api('dir_tree')).tree } catch (e) { /* 忽略 */ }
}
function saveHistory() {
  const q = query.value.trim()
  if (q) api('save_search_history', q)
}
const historyOptions = computed(() =>
  searchHistory.value.map((h) => ({ label: h, value: h })))
function pickHistory(v) {
  query.value = v
  refresh()
  searchContent()
}

// 目录树 → 点击过滤（path: 语法，searchContent 会对 path: 前缀做内容搜索，无需内容匹配时跳过）
function pickDir(dir) {
  currentDir.value = dir || null
  query.value = dir ? `path:${dir}` : ''
  page.value = 1
  refresh()
  if (dir) searchContent()
}
function treeDataFor(tree) {
  return (tree || []).map((n) => ({
    label: `${n.name}（${n.files}）`,
    key: n.path,
    children: treeDataFor(n.children),
  }))
}

function onQueryInput() {
  if (!query.value.trim()) {
    currentDir.value = null
    contentResults.value = null
  }
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    refresh()
    searchContent()   // 融合搜索：文件名 + 内容 + 图片语义 同时出
  }, 250)
}

async function exportCsv() {
  exporting.value = true
  try {
    const r = await api('export_csv', query.value || null, category.value, sortBy.value, sortOrder.value)
    if (!r.count) { message.warning('当前筛选没有可导出的文件'); return }
    const blob = new Blob([r.csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `文件清单-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
    message.success(`已导出 ${r.count} 条`)
  } catch (e) { message.error('导出失败：' + e) }
  exporting.value = false
}

async function refresh() {
  loading.value = true
  try {
    if (!thumbBase.value) {
      const st = await api('get_status')
      thumbBase.value = st.thumb_base
    }
    const r = await api('browse_files', query.value || null, category.value, null,
      (page.value - 1) * pageSize, pageSize, sortBy.value, sortOrder.value,
      tagFilter.value.length ? tagFilter.value : null)
    items.value = r.items
    total.value = r.total
  } catch (e) {
    // SQLite 错误（IntegrityError 等）通常是临时状态，只 console 不打扰用户
    console.error('refresh failed:', e)
    message.warning('列表加载失败，请稍后重试')
  }
  loading.value = false
}

function pickCategory(c) {
  category.value = category.value === c ? null : c
  page.value = 1
  refresh()
}

const CATEGORIES = ['文档', '图片', '视频', '音频', '压缩包', '安装包', '代码', '数据', '字体', '其他']

const isImg = (it) => ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'ico', 'svg', 'tiff', 'heic'].includes(it.ext)

const thumbUrl = (it) => `${thumbBase.value}/thumb?p=${encodeURIComponent(it.path)}`
const previewUrl = (it) => `${thumbBase.value}/preview?p=${encodeURIComponent(it.path)}`

function openItem(it) { api('open_path', it.path) }

// 灯箱翻页：只在本页图片列表中循环（←/→ 或按钮）
const imgItems = computed(() => items.value.filter(isImg))
const _extIcon = (ext) => ({
  pdf: '📕', doc: '📘', docx: '📘',
  xls: '📗', xlsx: '📗', csv: '📗',
  ppt: '📙', pptx: '📙',
  txt: '📄', md: '📄',
  zip: '🗜', rar: '🗜', '7z': '🗜',
  mp3: '🎵', wav: '🎵', flac: '🎵',
  mp4: '🎬', mkv: '🎬', mov: '🎬',
  exe: '⚙', msi: '⚙',
}[ext?.toLowerCase()] || '📄')
function openPreview(it) {
  const idx = imgItems.value.findIndex(x => x.path === it.path)
  preview.value = { path: it.path, url: previewUrl(it), index: Math.max(0, idx) }
}
function stepPreview(delta) {
  const list = imgItems.value
  if (!preview.value || !list.length) return
  const cur = preview.value.index ?? 0
  const ni = (cur + delta + list.length) % list.length
  const it = list[ni]
  preview.value = { path: it.path, url: previewUrl(it), index: ni }
}

// ---------- 内置预览（文本/Markdown/PDF/图片） ----------
const filePreview = ref(null)   // {type, content?, url?, name?}
const previewLoading = ref(false)
const summarizing = ref(false)
const summaryText = ref(null)   // {ok, summary?, error?}

// AI 总结仅对"可文本提取"格式开放（与 backend/knowledge/parsers.py SUPPORTED 对齐）。
// png/jpg/mp4 等二进制/媒体文件不显示该按钮，避免误导用户。
const SUMMARY_EXTS = new Set(['pdf', 'docx', 'pptx', 'xlsx', 'txt'])
const canSummarize = computed(() => {
  const p = filePreview.value?.path
  if (!p) return false
  const ext = (p.split('.').pop() || '').toLowerCase()
  return SUMMARY_EXTS.has(ext)
})

async function summarizeFile() {
  const p = filePreview?.value?.path
  if (!p) return
  summarizing.value = true
  summaryText.value = null
  try {
    summaryText.value = await api('summarize_file', p)
  } catch (e) {
    summaryText.value = { ok: false, error: String(e) }
  }
  summarizing.value = false
}

async function copySummary() {
  if (!summaryText.value?.summary) return
  try {
    await navigator.clipboard.writeText(summaryText.value.summary)
    message.success('已复制到剪贴板')
  } catch (e) {
    message.error('复制失败：' + String(e))
  }
}

async function previewFile(it) {
  previewLoading.value = true
  try {
    const r = await api('preview_file', it.path)
    if (r.type === 'image' || r.type === 'pdf') {
      r.url = `http://127.0.0.1:${thumbBase.value.split(':').pop()}/preview?p=${encodeURIComponent(it.path)}`
    }
    r.path = it.path
    filePreview.value = r
  } catch (e) { message.error(String(e)) }
  previewLoading.value = false
}

function renderMd(src) {
  // 轻量 Markdown 渲染（无外部依赖）：标题/加粗/代码/链接/列表
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  let html = esc(src)
  html = html.replace(/```([\s\S]*?)```/g, (_, code) => `<pre class="md-code">${code}</pre>`)
  html = html.replace(/^###### (.*)$/gm, '<h6>$1</h6>').replace(/^##### (.*)$/gm, '<h5>$1</h5>')
      .replace(/^#### (.*)$/gm, '<h4>$1</h4>').replace(/^### (.*)$/gm, '<h3>$1</h3>')
      .replace(/^## (.*)$/gm, '<h2>$1</h2>').replace(/^# (.*)$/gm, '<h1>$1</h1>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\*(.+?)\*/g, '<i>$1</i>')
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/^[-*] (.*)$/gm, '• $1')
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
  return html
}

// ---------- 快捷键 ----------
const searchInput = ref(null)
function onKeydown(e) {
  const tag = e.target?.tagName
  const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || e.target?.isContentEditable
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault()
    searchInput.value?.focus()
  }
  if (e.key === 'Escape' && preview.value) {
    preview.value = null
  }
  if (preview.value && !inInput && e.key === 'ArrowLeft') {
    e.preventDefault()
    stepPreview(-1)
  }
  if (preview.value && !inInput && e.key === 'ArrowRight') {
    e.preventDefault()
    stepPreview(1)
  }
}
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('fb-toast', onToast)
  loadTags()
  loadSearchHistory()
  loadDirTree()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('fb-toast', onToast)
})

// 后端 SendTo/右键 触发的 toast 通知（App.vue 顶层不能 useMessage，转发到这里）
function onToast(e) {
  const d = e?.detail
  if (!d) return
  if (d.type === 'success') message.success(d.text)
  else if (d.type === 'error') message.error(d.text)
  else message.info(d.text)
}

// 双击打开
const rowProps = (row) => ({
  onDblclick: () => openItem(row),
})

// 内容搜索（知识库文档 + 图片语义描述）
const contentResults = ref(null)
const contentLoading = ref(false)
async function searchContent() {
  if (!query.value.trim()) {
    contentResults.value = null
    return
  }
  contentLoading.value = true
  try {
    const r = await api('global_search', query.value)
    contentResults.value = r.content
  } catch (e) { message.error(String(e)) }
  contentLoading.value = false
}
function openContentFile(p) { api('open_path', p) }
function isContentImg(r) {
  return r.type === 'image' || /\.(jpe?g|png|gif|bmp|webp)$/i.test(r.file_path || '')
}
const contentThumb = (r) => thumbBase.value ? `${thumbBase.value}/thumb?p=${encodeURIComponent(r.file_path)}` : ''
const fileIconUrl = (ext) => thumbBase.value ? `${thumbBase.value}/icon?ext=${encodeURIComponent(ext || 'bin')}` : ''
function openPreviewByPath(p) {
  const idx = imgItems.value.findIndex(x => x.path === p)
  preview.value = { path: p, url: `${thumbBase.value}/preview?p=${encodeURIComponent(p)}`, index: Math.max(0, idx) }
}

// 列表列
const columns = [
  { title: ' ', key: 'thumb', width: 56,
    render: (r) => h('div', { class: 'fb-row-thumb' + (isImg(r) ? '' : ' is-doc') },
      isImg(r)
        ? h('img', { src: thumbUrl(r), loading: 'lazy', alt: r.name })
        : [
            h('span', { class: 'fb-thumb-emoji' }, _extIcon(r.ext)),
            h('img', { src: fileIconUrl(r.ext), loading: 'lazy', alt: r.ext || '',
              onError: (e) => { e.target.style.opacity = '0' } }),
          ])
    },
  { title: '文件名', key: 'name', ellipsis: { tooltip: true },
    render: (r) => h('span', { style: 'cursor:pointer', onClick: () => previewFile(r),
      title: '单击预览 · 双击打开所在位置' }, r.name) },
  { title: '类别', key: 'category', width: 90, render: (r) => h(NTag, { size: 'small', bordered: false }, { default: () => r.category }) },
  { title: '大小', key: 'size', width: 100, render: (r) => fmtSize(r.size) },
  { title: '修改时间', key: 'mtime', width: 160, render: (r) => new Date(r.mtime * 1000).toLocaleString() },
  { title: '标签', key: 'tags', width: 140, render: (r) => h(NPopover, { trigger: 'click', placement: 'left' }, {
      trigger: () => h('span', {
        style: 'cursor:pointer;font-size:12px;color:#5a9cf8;display:inline-block;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap',
        title: '点击编辑标签',
      }, r.tags ? ('🏷 ' + r.tags.split('|').slice(0, 2).join('、')
          + (r.tags.split('|').length > 2 ? '…' : '')) : '＋ 打标签'),
      default: () => h('div', { style: 'width:230px' }, [
        h(NSelect, {
          multiple: true, tag: true, filterable: true,
          value: tagSel.value[r.path] || [], options: tagOptions.value,
          placeholder: '选择或输入新标签，回车创建',
          onCreate: createTag,
          onUpdateValue: (v) => { tagSel.value[r.path] = v },
        }),
        h(NButton, {
          size: 'small', type: 'primary', style: 'margin-top:10px;width:100%',
          onClick: () => saveTags(r),
        }, { default: () => '保存标签' }),
      ]),
    }) },
  { title: '路径', key: 'path', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.path) },
]

const batchColumns = [{ type: 'selection' }, ...columns]

function fmtSize(n) {
  if (n == null) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return n.toFixed(1) + ' ' + units[i]
}

refresh()
</script>

<template>
  <div class="fb-page">
    <n-card>
      <n-space vertical size="large">
        <!-- 搜索栏 -->
        <n-space :wrap="false" align="center" class="fb-search">
          <n-auto-complete v-model:value="query" :options="historyOptions" size="large"
            :style="{ flex: 1 }" clearable round placeholder="搜索文件名和内容… 支持 ext:pdf size:>100mb dm:本周 path:桌面 cat:图片（Ctrl+F 聚焦）"
            @select="pickHistory" @update:value="onQueryInput"
            @keyup.enter="saveHistory">
            <template #prefix>🔍</template>
          </n-auto-complete>
          <n-select v-model:value="sortBy" :options="sortOptions" size="small" style="width:112px"
            @update:value="() => { page = 1; refresh() }" />
          <n-select v-model:value="sortOrder" :options="orderOptions" size="small" style="width:88px"
            @update:value="() => { page = 1; refresh() }" />
          <n-select v-model:value="tagFilter" :options="tagOptions" multiple size="small"
            style="width:150px" placeholder="按标签筛选" clearable
            @update:value="() => { page = 1; refresh() }" />
          <n-button size="small" secondary :loading="exporting" @click="exportCsv">
            导出 CSV
          </n-button>
        </n-space>

        <!-- 扫描进度 -->
        <n-alert v-if="scanning" type="info" :bordered="false">
          <n-progress :percentage="scanning.n ? Math.round(scanning.i / scanning.n * 100) : 0"
            indicator-placement="inside" processing />
          <n-text class="mono" style="font-size:12px">
            {{ { scan: '扫描目录', index: '写入索引', clean: '清理失效记录', done: '完成' }[scanning.stage] }}
            {{ scanning.i }}/{{ scanning.n }} {{ scanning.detail }}
          </n-text>
        </n-alert>

        <!-- 类别筛选 -->
        <n-space class="fb-chips">
          <n-tag v-for="c in CATEGORIES" :key="c" size="medium" round
            :type="category === c ? 'primary' : 'default'" style="cursor:pointer"
            @click="pickCategory(c)">{{ c }}</n-tag>
          <n-tag size="medium" round :type="!category && !currentDir ? 'primary' : 'default'"
            style="cursor:pointer" @click="pickCategory(null); pickDir(null)">全部</n-tag>
          <n-text depth="3" style="align-self:center">
            共 {{ total }} 个文件{{ category ? ' · ' + category : '' }}{{ currentDir ? ' · ' + currentDir : '' }}
            <template v-if="lastEvent"> · 最近动态：{{ lastEvent.kind === 'added' ? '新增' : '移除' }} {{ lastEvent.path.split(/[\\/]/).pop() }}</template>
          </n-text>
        </n-space>

        <!-- 目录树（可折叠） -->
        <div class="fb-dirtree-wrap">
          <n-button size="tiny" quaternary @click="dirExpanded = !dirExpanded">
            {{ dirExpanded ? '▾ 收起目录树' : '▸ 目录树' }}
            <template v-if="currentDir"> · {{ currentDir }}</template>
          </n-button>
          <!-- 取消 default-expand-all：大库（万级文件）展开整棵会卡。改为只展开第 1 层 + 搜索过滤 -->
          <n-tree v-if="dirExpanded" :data="treeDataFor(dirTreeData)" block-line
            selectable filterable :pattern="dirTreePattern"
            :default-expanded-keys="defaultExpandedDirs"
            :selected-keys="currentDir ? [currentDir] : []"
            style="max-height:260px;overflow:auto;margin-top:6px"
            @update:selected-keys="(k) => k.length && pickDir(k[0])" />
          <n-space v-if="dirExpanded && currentDir" size="small" style="margin-top:4px">
            <n-button size="tiny" quaternary @click="pickDir(null)">↺ 全部（清除目录过滤）</n-button>
          </n-space>
        </div>

        <!-- 批量操作栏（列表视图多选后出现） -->
        <n-space v-if="view === 'list' && checkedRows.length" align="center" class="fb-batchbar">
          <n-text>已选 <b>{{ checkedRows.length }}</b> 个</n-text>
          <n-button size="small" type="primary" ghost @click="openRename">重命名</n-button>
          <n-button size="small" type="error" ghost @click="trashSelected">删除到回收站</n-button>
          <n-button size="small" @click="copyPaths">复制路径</n-button>
          <n-button size="small" quaternary @click="checkedRows = []">清空选择</n-button>
        </n-space>

        <!-- 统一列表（第一列缩略图，图片显示缩略图、其他显示类型图标） -->
        <n-data-table :columns="batchColumns" :data="items" :loading="loading"
          :max-height="520" size="small" :row-key="(r) => r.path" :row-props="rowProps"
          v-model:checked-row-keys="checkedRows" />

        <n-space v-if="total > pageSize" justify="center">
          <n-pagination v-model:page="page" :page-count="Math.ceil(total / pageSize)"
            @update:page="refresh" />
        </n-space>

        <!-- 内容搜索结果（文档 + 图片语义，融合展示） -->
        <n-card v-if="contentResults && (contentResults.results.length || contentResults.images.length)"
          size="small" title="内容匹配（文档 + 图片语义）">
          <template #header-extra>
            <n-tag size="small" :type="contentResults.mode === 'vector' ? 'success' : 'warning'">
              {{ contentResults.mode === 'vector' ? '语义' : '关键词' }}
            </n-tag>
          </template>
          <!-- 图片语义命中 -->
          <div v-if="contentResults.images.length" class="content-imgs">
            <div v-for="(img, i) in contentResults.images" :key="i" class="thumb-card"
              style="width:150px" @click="openPreviewByPath(img.file_path)" :title="img.descr || img.text">
              <img :src="contentThumb(img)" loading="lazy" />
              <div class="thumb-name">{{ (img.file_path.split(/[\\/]/).pop()) }}</div>
            </div>
          </div>
          <div style="max-height:300px;overflow:auto">
            <div v-for="(r, i) in contentResults.results" :key="i"
              style="padding:6px 0;border-bottom:1px dashed #eee">
              <n-text class="mono" style="cursor:pointer;color:#5a9cf8" @click="openContentFile(r.file_path)">
                📄 {{ r.file_path }}
              </n-text>
              <div style="color:#666;font-size:12px;margin-top:2px">{{ (r.text || '').slice(0, 160) }}…</div>
            </div>
          </div>
        </n-card>
        <n-card v-else-if="contentResults && contentLoading === false && query" size="small">
          <n-empty description="知识库中没有匹配内容——把相关文件夹加入「知识库」页并索引后即可内容级搜索" />
        </n-card>
      </n-space>
    </n-card>

    <!-- 大图预览灯箱：点空白处/ESC 关闭，右上角关闭钮，底部悬浮操作条 -->
    <teleport to="body">
      <transition name="fade">
        <div v-if="preview" class="lightbox" @click.self="preview = null" title="点击空白处关闭">
          <img :src="preview.url" class="lightbox-img" alt="" />
          <button class="lightbox-close" @click="preview = null" title="关闭 (Esc)">✕</button>
          <div class="lightbox-bar">
            <button class="lightbox-btn" @click.stop="stepPreview(-1)">‹ 上一张</button>
            <span class="lightbox-name" :title="preview.path">
              {{ preview.path.split(/[\\/]/).pop() }}
              <span v-if="imgItems.length" class="lightbox-count">
                {{ (preview.index ?? 0) + 1 }} / {{ imgItems.length }}
              </span>
            </span>
            <button class="lightbox-btn" @click.stop="stepPreview(1)">下一张 ›</button>
            <button class="lightbox-btn" @click="api('open_path', preview.path)">
              📂 打开
            </button>
            <button class="lightbox-btn primary" @click="preview = null">关 闭</button>
          </div>
        </div>
      </transition>
    </teleport>

    <!-- 批量重命名 -->
    <n-modal v-model:show="renameModal" preset="card" title="批量重命名"
      style="width:720px" content-style="max-height:70vh;overflow:auto">
      <n-space vertical>
        <n-radio-group :value="renameMode" @update:value="(v) => { renameMode = v; genRenameRows() }">
          <n-radio-button value="seq">序列编号</n-radio-button>
          <n-radio-button value="replace">查找替换</n-radio-button>
          <n-radio-button value="ai">AI 建议</n-radio-button>
        </n-radio-group>

        <n-space v-if="renameMode === 'seq'" align="center">
          <n-input v-model:value="renamePrefix" placeholder="前缀，如 旅行照片" style="width:200px" />
          <n-input-number v-model:value="renameStart" :min="0" :max="99999" style="width:110px" />
          <n-button size="small" @click="genRenameRows">生成</n-button>
        </n-space>

        <n-space v-else-if="renameMode === 'replace'" align="center">
          <n-input v-model:value="renameFind" placeholder="查找" style="width:200px" />
          <n-input v-model:value="renameReplace" placeholder="替换为" style="width:200px" />
          <n-button size="small" @click="genRenameRows">生成</n-button>
        </n-space>

        <n-space v-else align="center">
          <n-text depth="3">用本地模型按内容/名称理解批量起名</n-text>
          <n-button size="small" type="primary" ghost :loading="renameLoading" @click="aiSuggest">
            AI 一键起名
          </n-button>
        </n-space>

        <n-data-table v-if="renameRows.length" :columns="[
          { title: '原文件名', key: 'src', ellipsis: { tooltip: true },
            render: (r) => h('span', { class: 'mono' }, baseName(r.src)) },
          { title: '新文件名', key: 'new_name', render: (r) => h('input', {
              class: 'fb-rename-input', value: r.new_name,
              onInput: (e) => { r.new_name = e.target.value },
            }) },
        ]" :data="renameRows" :max-height="380" size="small" :row-key="(r) => r.src" />

        <n-space justify="end">
          <n-button @click="renameModal = false">取消</n-button>
          <n-button type="primary" :disabled="!renameRows.length" @click="doRename">
            执行重命名（{{ renameRows.length }} 个）
          </n-button>
        </n-space>
      </n-space>
    </n-modal>

    <!-- 文件预览（文本/Markdown/PDF） -->
    <n-modal :show="!!filePreview" @update:show="(v) => { if (!v) { filePreview = null; summaryText = null } }"
      preset="card" style="width:860px" :title="filePreview?.name || '预览'"
      content-style="max-height:78vh;overflow:auto">
      <n-spin :show="previewLoading">
        <n-alert v-if="summaryText" :type="summaryText.ok ? 'success' : 'error'"
          style="margin-bottom:12px">
          <template #header>
            <n-space align="center" :wrap="false">
              <span>{{ summaryText.ok ? 'AI 摘要' : '总结失败' }}</span>
              <n-button v-if="summaryText.ok" size="tiny" quaternary @click="copySummary">复制</n-button>
            </n-space>
          </template>
          <span style="white-space:pre-wrap">{{ summaryText.ok ? summaryText.summary : summaryText.error }}</span>
        </n-alert>
        <div v-if="filePreview?.type === 'markdown'" class="md-body"
          v-html="renderMd(filePreview.content)"></div>
        <div v-else-if="filePreview?.type === 'html'" class="fb-office"
          v-html="filePreview.content"></div>
        <pre v-else-if="filePreview?.type === 'text'" class="mono"
          style="white-space:pre-wrap;word-break:break-word;font-size:12.5px;line-height:1.6">{{ filePreview.content }}</pre>
        <iframe v-else-if="filePreview?.type === 'pdf'" :src="filePreview.url"
          style="width:100%;height:70vh;border:0;border-radius:6px"></iframe>
        <div v-else-if="filePreview?.type === 'image'" class="fb-modal-image">
          <img :src="filePreview.url" :alt="filePreview.name" />
        </div>
        <n-empty v-else-if="filePreview?.type === 'unknown'" description="该格式暂不支持内置预览，可点击文件名或使用系统打开" />
      </n-spin>
      <template #footer>
        <n-space>
          <n-button v-if="canSummarize" size="small" type="primary" ghost :loading="summarizing"
            @click="summarizeFile">🤖 AI 总结</n-button>
          <n-button size="small" @click="api('open_path', filePreview?.path)">系统打开</n-button>
          <n-button size="small" @click="api('open_location', filePreview?.path)">打开所在位置</n-button>
          <n-button size="small" @click="filePreview = null">关闭</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.md-body { line-height: 1.7; }
.md-body :deep(h1) { font-size: 1.5em; margin: 0.6em 0 0.3em; }
.md-body :deep(h2) { font-size: 1.3em; margin: 0.6em 0 0.3em; }
.md-body :deep(h3) { font-size: 1.15em; margin: 0.5em 0 0.3em; }
.md-body :deep(code) { background: rgba(127,127,127,.15); border-radius: 3px; padding: 1px 5px; }
.md-body :deep(.md-code) { background: rgba(127,127,127,.12); border-radius: 6px; padding: 10px 12px; overflow: auto; }
.md-body :deep(a) { color: #5a9cf8; }
.content-imgs {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.lightbox-count {
  opacity: .6;
  font-size: 12px;
  margin-left: 8px;
}
.fb-office { font-size: 13px; line-height: 1.7; padding: 4px 2px; }
.fb-office :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0 14px; font-size: 12.5px; }
.fb-office :deep(td), .fb-office :deep(th) { border: 1px solid rgba(127,127,127,.35); padding: 4px 8px; }
.fb-office :deep(h1) { font-size: 1.35em; margin: .5em 0 .25em; }
.fb-office :deep(h2) { font-size: 1.2em; margin: .5em 0 .25em; }
.fb-office :deep(h3) { font-size: 1.05em; margin: .6em 0 .2em; }
.fb-office :deep(h4) { font-size: 1em; margin: .6em 0 .2em; color: #5a9cf8; }
.fb-office :deep(p) { margin: 4px 0; }
.fb-office :deep(.fb-office-empty) { color: #999; }
.blink { animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }
</style>
