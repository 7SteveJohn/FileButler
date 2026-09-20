<script setup>
import { ref, computed, onUnmounted, h, onMounted } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NTag, NDataTable, NEmpty, NSpin, NAlert,
  NRadioGroup, NRadioButton, NPagination, NModal, NTooltip, useMessage, NProgress, NSelect,
  NPopover, NIcon, NDropdown, NDatePicker,
} from 'naive-ui'
import { SearchOutline } from '@vicons/ionicons5'
import { api, on } from '../lib/bridge'
import { CATEGORY_HUES } from '../lib/theme'

const message = useMessage()

// ---------- 状态 ----------
const query = ref('')
const category = ref(null)
const loading = ref(false)
const items = ref([])
const total = ref(0)
const totalCapped = ref(false)   // FTS 命中超过 5000：计数封顶，显示 5000+
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
    tagList.value = r.tags || []
    tagOptions.value = (r.tags || []).map((t) => ({ label: t.name, value: t.id }))
  } catch (e) { /* 忽略 */ }
}

// ---------- 标签管理（重命名/删除，后端 API 现成） ----------
const showTagMgr = ref(false)
const tagList = ref([])         // {id, name, c}

async function deleteTag(t) {
  if (!window.confirm(`删除标签「${t.name}」？\n将从所有文件上移除（文件本身不受影响）。`)) return
  await api('delete_tag', t.id)
  tagList.value = tagList.value.filter((x) => x.id !== t.id)
  tagOptions.value = tagOptions.value.filter((o) => o.value !== t.id)
  message.success('标签已删除')
}

async function renameTag(t) {
  const name = window.prompt('新的标签名：', t.name)
  if (!name || !name.trim() || name.trim() === t.name) return
  await api('rename_tag', t.id, name.trim())
  loadTags()
  message.success('标签已重命名')
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

// ---------- 保存的智能筛选 ----------
const savedSearches = ref([])
const showSaveSearch = ref(false)
const saveSearchName = ref('')

async function loadSavedSearches() {
  try { savedSearches.value = (await api('list_saved_searches')).items } catch (e) { /* 忽略 */ }
}

async function saveCurrentSearch() {
  const q = query.value.trim()
  if (!q) return message.warning('先在搜索框输入筛选条件（支持 size:>100mb 等语法）')
  const name = saveSearchName.value.trim()
  if (!name) return message.warning('请给筛选起个名字')
  const r = await api('save_search', name, q)
  if (r.ok) {
    message.success('筛选已保存')
    showSaveSearch.value = false
    saveSearchName.value = ''
    loadSavedSearches()
  } else {
    message.error(r.error || '保存失败')
  }
}

function applySaved(s) {
  query.value = s.query
  currentDir.value = null
  refresh()
  searchContent()
}

async function removeSaved(s) {
  await api('delete_saved_search', s.name)
  loadSavedSearches()
}

// ---------- 收藏夹（后端 API 现成，这里接线） ----------
const favSet = ref(new Set())
const favList = ref([])

async function loadFavs() {
  try {
    favList.value = (await api('list_favorites')).items
    favSet.value = new Set(favList.value.map((x) => x.path))
  } catch (e) { /* 忽略 */ }
}

async function toggleFav(row) {
  if (favSet.value.has(row.path)) {
    await api('remove_favorite', row.path)
    favSet.value = new Set([...favSet.value].filter((p) => p !== row.path))
    favList.value = favList.value.filter((x) => x.path !== row.path)
    message.info('已取消收藏')
  } else {
    await api('add_favorite', row.path)
    favSet.value = new Set([...favSet.value, row.path])
    favList.value = [...favList.value, { path: row.path, note: '' }]
    message.success('已收藏')
  }
}

function openFav(p) { api('open_path', p) }

async function editFavNote(f) {
  const v = window.prompt('备注（留空清除）：', f.note || '')
  if (v === null) return
  const note = v.trim()
  if (note === (f.note || '')) return
  const r = await api('set_favorite_note', f.path, note)
  if (r && r.ok === false) { message.error(r.error || '保存失败'); return }
  f.note = note
  message.success(note ? '备注已保存' : '已清除备注')
}

async function removeFav(p) {
  await api('remove_favorite', p)
  favList.value = favList.value.filter((x) => x.path !== p)
  favSet.value = new Set([...favSet.value].filter((x) => x !== p))
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

// checkedRows 绑定的是 row-key（路径字符串），不是行对象——
// 需要行对象（name/ext/category）时从这里取回
function checkedRowObjs() {
  return checkedRows.value.map((p) =>
    items.value.find((x) => x.path === p) || { path: p, name: baseName(p) })
}

// 网格视图的勾选也走同一个 checkedRows（路径数组），两种视图选择状态互通
const isChecked = (p) => checkedRows.value.includes(p)
function toggleCheck(p) {
  checkedRows.value = isChecked(p)
    ? checkedRows.value.filter((x) => x !== p)
    : [...checkedRows.value, p]
}

async function aiSuggest() {
  if (!checkedRows.value.length) return
  renameLoading.value = true
  try {
    const r = await api('ai_rename', JSON.parse(JSON.stringify(checkedRowObjs())))
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

// 单文件重命名（右键/F2）：传入路径字符串，预填原名
function startSingleRename(path) {
  renameMode.value = 'single'
  renameRows.value = [{ src: path, new_name: baseName(path) }]
  renameModal.value = true
}

function genRenameRows() {
  if (renameMode.value === 'single') return  // 单文件：保持预填行，用户在输入框里改
  const sel = checkedRowObjs()
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

async function trashSelected(paths = null) {
  const fromChecked = !paths
  paths = paths || [...checkedRows.value]  // checkedRows 存的就是路径
  if (!paths.length) return
  const ok = window.confirm(`将 ${paths.length} 个文件/文件夹移入系统回收站（可随时恢复）？`)
  if (!ok) return
  const r = await api('trash_files', JSON.parse(JSON.stringify(paths)))
  const okN = r.results.filter((x) => x.ok).length
  message.success(`已移入回收站 ${okN}/${paths.length}`)
  if (fromChecked) checkedRows.value = []
  refresh()
}

// ---------- 移动到 / 复制到（paths 省略时作用于勾选行；右键菜单会显式传入） ----------
const transferring = ref(false)
async function transferFiles(mode, paths = null) {
  const fromChecked = !paths
  paths = paths || [...checkedRows.value]  // checkedRows 存的就是路径
  if (!paths.length) return
  const dest = await api('pick_folder')
  if (!dest) return
  transferring.value = true
  try {
    const r = await api('transfer_files', JSON.parse(JSON.stringify(paths)), dest, mode)
    const ok = r.results.filter((x) => x.ok).length
    const fail = r.results.length - ok
    if (fail === 0) {
      const tip = mode === 'move' ? '（可在「文件整理 → 操作历史」一键撤销）' : ''
      message.success(`${mode === 'move' ? '已移动' : '已复制'} ${ok} 个文件${tip}`)
    } else {
      message.warning(`${mode === 'move' ? '移动' : '复制'} ${ok} 个成功 / ${fail} 个失败`)
    }
    if (fromChecked) checkedRows.value = []
    refresh()
  } catch (e) { message.error(String(e)) }
  transferring.value = false
}

async function copyPaths() {
  const text = [...checkedRows.value].join('\n')  // checkedRows 存的就是路径
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制路径')
  } catch (e) { message.error('复制失败') }
}

// ---------- 右键菜单（右键行在勾选集合内 = 作用于全部勾选，否则作用于该行） ----------
const menuShow = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const menuRow = ref(null)

// 系统 OCR 可用性（null = 未探测）。不阻塞列表渲染：探测结果回来后再刷新菜单项状态
const ocrSt = ref(null)
function loadOcrStatus() {
  api('ocr_status').then((s) => { ocrSt.value = s }).catch(() => {})
}

const menuOptions = computed(() => {
  const row = menuRow.value
  if (!row) return []
  const many = checkedRows.value.includes(row.path) && checkedRows.value.length > 1
  const n = `（${checkedRows.value.length}）`
  const tools = []
  if (isImg(row)) {
    const off = ocrSt.value && !ocrSt.value.available
    tools.push(off
      ? {
          label: 'OCR 取字（不可用）',
          key: 'ocr',
          disabled: true,
          title: `${ocrSt.value.reason}${ocrSt.value.hint ? '。' + ocrSt.value.hint : ''}`
        }
      : { label: 'OCR 取字', key: 'ocr' })
  }
  tools.push({ label: '哈希校验 (SHA-256)', key: 'hash' })
  return [
    ...(many ? [] : [
      { label: '打开', key: 'open' },
      { label: '快速预览', key: 'preview' },
      { label: '在所在位置显示', key: 'location' },
      { type: 'divider', key: 'd1' },
    ]),
    { label: favSet.value.has(row.path) ? '取消收藏' : '收藏', key: 'fav' },
    { label: '复制路径' + (many ? n : ''), key: 'copypath' },
    { label: '移动到…' + (many ? n : ''), key: 'move' },
    { label: '复制到…' + (many ? n : ''), key: 'copy' },
    { type: 'divider', key: 'd2' },
    ...(many ? [] : [{ label: '重命名', key: 'rename' },
      { label: '工具', key: 'tools', children: tools }]),
    { label: '删除到回收站' + (many ? n : ''), key: 'trash' },
  ]
})

function openMenu(e, row) {
  e.preventDefault()
  menuRow.value = row
  menuX.value = e.clientX
  menuY.value = e.clientY
  menuShow.value = true
}

function menuPaths() {
  const row = menuRow.value
  if (row && checkedRows.value.includes(row.path) && checkedRows.value.length > 1) {
    return checkedRows.value.map((x) => x.path)
  }
  return [row.path]
}

async function onMenuSelect(key) {
  menuShow.value = false
  const row = menuRow.value
  if (!row) return
  const paths = menuPaths()
  if (key === 'open') openItem(row)
  else if (key === 'preview') isImg(row) ? openPreview(row) : previewFile(row)
  else if (key === 'location') api('open_location', row.path)
  else if (key === 'fav') toggleFav(row)
  else if (key === 'copypath') {
    try {
      await navigator.clipboard.writeText(paths.join('\n'))
      message.success('已复制路径')
    } catch (e) { message.error('复制失败') }
  }
  else if (key === 'move') transferFiles('move', paths)
  else if (key === 'copy') transferFiles('copy', paths)
  else if (key === 'rename') startSingleRename(row.path)
  else if (key === 'ocr' || key === 'hash') openTool(key, row.path)
  else if (key === 'trash') trashSelected(paths)
}

// ---------- 工具结果弹窗（OCR / 哈希） ----------
const toolModal = ref(null)   // {title, loading, text}

function openTool(kind, path) {
  toolModal.value = {
    title: kind === 'ocr' ? 'OCR 取字' : '哈希校验 (SHA-256)',
    loading: true, text: '',
  }
  const call = kind === 'ocr' ? api('ocr_extract', path) : api('file_hash', path, 'sha256')
  call.then((r) => {
    toolModal.value = {
      ...toolModal.value, loading: false,
      text: r.ok ? (kind === 'ocr' ? (r.text || '（未识别到文字）')
        : `${r.algo.toUpperCase()}: ${r.hash}`) : ('失败：' + r.error),
    }
  }).catch((e) => {
    toolModal.value = { ...toolModal.value, loading: false, text: '失败：' + String(e) }
  })
}

async function copyToolText() {
  try {
    await navigator.clipboard.writeText(toolModal.value.text)
    message.success('已复制')
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

// 树默认折叠，没必要在进页面时就做一次全表聚合（68 万行实测 613ms）。
// 每次展开都调：后端有 120s 缓存 + 过期后台重算，命中时几乎瞬时，
// 又能拿到重扫之后的新结构（只在为空时取会导致重扫后树一直不刷新）。
function toggleDirTree() {
  dirExpanded.value = !dirExpanded.value
  if (dirExpanded.value) loadDirTree()
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
  // 120ms：Everything 式「输入即结果」——防抖过长会打断打字节奏
  searchTimer = setTimeout(() => {
    refresh()
  }, 120)
}

// 回车即走：Enter 打开第一项结果；Shift+Enter 搜内容（RAG 较慢，不混在打字流里）
function onSearchEnter(e) {
  saveHistory()
  if (e.shiftKey) {
    searchContent()
    return
  }
  if (!checkedRows.value.length && items.value.length) {
    api('open_path', items.value[0].path)
  }
}

// ---------- 视图模式（列表 / 网格 / 时间线，偏好存后端） ----------
const viewMode = ref('list')
function setViewMode(m) {
  viewMode.value = m
  if (m === 'timeline') {
    // 时间线按天分组，必须按修改时间倒序才有意义
    sortBy.value = 'mtime'
    sortOrder.value = 'desc'
    page.value = 1
    refresh()
  }
  api('set_ui_state', 'view_mode', m).catch(() => {})
}

// ---------- 时间线（区间走后端 mtime 过滤；注意这是修改时间，不是创建时间） ----------
const timeRange = ref('all')          // all | today | week | month | custom
const customRange = ref(null)         // [ms, ms]，n-date-picker daterange
const timeChips = [
  { key: 'all', label: '不限' },
  { key: 'today', label: '今天' },
  { key: 'week', label: '本周' },
  { key: 'month', label: '本月' },
  { key: 'custom', label: '自定义' },
]

function _startOfDay(d) {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}

// 后端区间是左闭右开（mtime>=from AND mtime<to），to=null 表示不设上界
const mtimeRange = computed(() => {
  const now = new Date()
  if (timeRange.value === 'today') return { from: +_startOfDay(now) / 1000, to: null }
  if (timeRange.value === 'week') {
    const d = _startOfDay(now)
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7))   // 周一为一周起点
    return { from: +d / 1000, to: null }
  }
  if (timeRange.value === 'month') {
    const d = _startOfDay(now)
    d.setDate(1)
    return { from: +d / 1000, to: null }
  }
  if (timeRange.value === 'custom') {
    const v = customRange.value
    if (!Array.isArray(v) || v.length !== 2 || !v[0] || !v[1]) return { from: null, to: null }
    return {
      from: +_startOfDay(v[0]) / 1000,
      to: +_startOfDay(new Date(v[1]).getTime() + 86400000) / 1000,  // 结束日整天算入内
    }
  }
  return { from: null, to: null }
})

function pickTimeRange(k) {
  timeRange.value = k
  if (k === 'custom' && !customRange.value) {
    const end = _startOfDay(new Date())
    customRange.value = [+new Date(+end - 6 * 86400000), +end]   // 首次给「近 7 天」
  }
  page.value = 1
  refresh()
}

const WEEK_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
function fmtClock(mtime) {
  const d = new Date((mtime || 0) * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function onCustomRange(v) {
  customRange.value = v
  page.value = 1
  refresh()
}

function dayLabel(d) {
  const s = _startOfDay(d)
  const diff = Math.round((+(_startOfDay(new Date())) - +s) / 86400000)
  const date = `${s.getMonth() + 1} 月 ${s.getDate()} 日`
  const w = WEEK_CN[s.getDay()]
  if (diff === 0) return `今天 · ${date} ${w}`
  if (diff === 1) return `昨天 · ${date} ${w}`
  if (diff > 1 && diff <= 7) return `${diff} 天前 · ${date} ${w}`
  return `${s.getFullYear()} 年 ${date} ${w}`
}

// 按本地日期分组（时间线视图用）；items 已由后端按 mtime 倒序返回
const groupedByDay = computed(() => {
  const out = []
  const map = new Map()
  for (const it of items.value) {
    const d = new Date((it.mtime || 0) * 1000)
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
    if (!map.has(key)) {
      const g = { key, label: dayLabel(d), items: [] }
      map.set(key, g)
      out.push(g)
    }
    map.get(key).items.push(it)
  }
  return out
})

// ---------- 键盘速查 ----------
const showKeys = ref(false)

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
      // 只要缩略图服务地址——get_status 里 Ollama 探测 + 83 万行统计太重，
      // 不能作为列表页每次进入的前置依赖
      const tb = await api('thumb_base')
      thumbBase.value = tb.base
    }
    const r = await api('browse_files', query.value || null, category.value, null,
      (page.value - 1) * pageSize, pageSize, sortBy.value, sortOrder.value,
      tagFilter.value.length ? tagFilter.value : null,
      mtimeRange.value.from, mtimeRange.value.to)
    items.value = r.items
    total.value = r.total
    totalCapped.value = !!r.total_capped
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
    e.stopPropagation()
    searchInput.value?.focus()
    try { searchInput.value?.select?.() } catch (e2) { /* 部分版本无 select */ }
    return
  }
  if (inInput) return
  // Space：拦掉默认行为（滚动 / 复选框在 keyup 上的激活），
  // 实际开关逻辑在 onKeyUp（浏览器对空格的激活行为发生在 keyup）
  if (e.key === ' ' &&
      (preview.value || filePreview.value || checkedRows.value.length)) {
    e.preventDefault()
    e.stopPropagation()
    return
  }
  // 灯箱：Esc 关闭，←/→ 翻页
  if (preview.value) {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      preview.value = null
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      e.stopPropagation()
      stepPreview(-1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      e.stopPropagation()
      stepPreview(1)
    }
    return
  }
  // 文件预览弹窗：Esc 关闭
  if (filePreview.value) {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      filePreview.value = null
      summaryText.value = null
    }
    return
  }
  if (renameModal.value) return  // 重命名弹窗打开时不响应列表快捷键
  if (e.key === '?') {
    e.preventDefault()
    e.stopPropagation()
    showKeys.value = true
    return
  }
  if (!checkedRows.value.length) return  // 未选中时不劫持任何按键
  // 以下快捷键在捕获阶段处理后阻断传播：鼠标点完勾选框后焦点停在
  // 复选框上，Enter 会被它当成「切换勾选」，必须拦在前面
  if (e.key === 'Enter') {
    e.preventDefault()
    e.stopPropagation()
    api('open_path', checkedRows.value[0])  // checkedRows 存路径
  } else if (e.key === 'F2') {
    e.preventDefault()
    e.stopPropagation()
    checkedRows.value.length === 1 ? startSingleRename(checkedRows.value[0]) : openRename()
  } else if (e.key === 'Delete') {
    e.preventDefault()
    e.stopPropagation()
    trashSelected()
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
    e.preventDefault()
    e.stopPropagation()
    checkedRows.value = items.value.map((r) => r.path)  // 绑的是 row-key（路径），不是行对象
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c') {
    e.preventDefault()
    e.stopPropagation()
    copyPaths()
  }
}

// Space 的开/关都在 keyup 处理：keydown 阶段复选框尚未消费空格，
// 此时切状态会被 keyup 的默认激活行为立刻翻回去
function onKeyUp(e) {
  if (e.key !== ' ') return
  const tag = e.target?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target?.isContentEditable) return
  if (preview.value || filePreview.value) {
    e.preventDefault()
    e.stopPropagation()
    preview.value = null
    filePreview.value = null
    summaryText.value = null
    return
  }
  if (renameModal.value || !checkedRows.value.length) return
  e.preventDefault()
  e.stopPropagation()
  const p = checkedRows.value[0]                     // 路径字符串
  const row = items.value.find((x) => x.path === p) || { path: p }
  isImg(row) ? openPreview(row) : previewFile(row)
}
onMounted(() => {
  // 捕获阶段：赶在聚焦的复选框等控件消费按键之前处理列表快捷键
  window.addEventListener('keydown', onKeydown, true)
  window.addEventListener('keyup', onKeyUp, true)
  window.addEventListener('fb-toast', onToast)
  loadTags()
  loadSearchHistory()
  loadFavs()
  loadSavedSearches()
  loadOcrStatus()
  api('get_ui_state').then(({ state }) => {
    const m = state && state.view_mode
    if (m === 'grid' || m === 'timeline') viewMode.value = m
  }).catch(() => {})
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown, true)
  window.removeEventListener('keyup', onKeyUp, true)
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

// 双击打开；右键呼出菜单
const rowProps = (row) => ({
  onDblclick: () => openItem(row),
  onContextmenu: (e) => openMenu(e, row),
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
function openPreviewByPath(p) {
  const idx = imgItems.value.findIndex(x => x.path === p)
  preview.value = { path: p, url: `${thumbBase.value}/preview?p=${encodeURIComponent(p)}`, index: Math.max(0, idx) }
}

// 类别 → 徽章色相（共享设计令牌，见 lib/theme.js）
const _HUES = CATEGORY_HUES

// 列表列
const columns = [
  { title: ' ', key: 'thumb', width: 56,
    render: (r) => isImg(r)
      ? h('div', { class: 'fb-row-thumb' },
          h('img', { src: thumbUrl(r), loading: 'lazy', alt: r.name }))
      : h('div', {
          class: 'fb-ext-badge',
          style: { '--h': String(_HUES[r.category] || 220) },
          title: '.' + (r.ext || ''),
        }, (r.ext || 'file').slice(0, 4).toUpperCase()) },
  { title: '', key: 'fav', width: 40,
    render: (r) => h('span', {
      style: 'cursor:pointer;font-size:15px;color:' +
        (favSet.value.has(r.path) ? '#f0a020' : 'rgba(127,127,127,.35)'),
      title: favSet.value.has(r.path) ? '取消收藏' : '收藏',
      onClick: (e) => { e.stopPropagation(); toggleFav(r) },
      onDblclick: (e) => e.stopPropagation(),
    }, favSet.value.has(r.path) ? '★' : '☆') },
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
            :style="{ flex: 1 }" clearable round
            placeholder="搜索文件… Enter 打开第一项 · Shift+Enter 搜内容 · 支持 ext:pdf size:>100mb dm:week（Ctrl+F 聚焦）"
            @select="pickHistory" @update:value="onQueryInput"
            @keydown.enter="onSearchEnter">
            <template #prefix>
              <n-icon :component="SearchOutline" size="15" style="opacity:.45" />
            </template>
          </n-auto-complete>
          <n-select v-model:value="sortBy" :options="sortOptions" size="small" style="width:112px"
            @update:value="() => { page = 1; refresh() }" />
          <n-select v-model:value="sortOrder" :options="orderOptions" size="small" style="width:88px"
            @update:value="() => { page = 1; refresh() }" />
          <n-select v-model:value="tagFilter" :options="tagOptions" multiple size="small"
            style="width:150px" placeholder="按标签筛选" clearable
            @update:value="() => { page = 1; refresh() }" />
          <n-popover trigger="click" placement="bottom" :width="300" v-model:show="showTagMgr">
            <template #trigger>
              <n-button size="small" secondary>🏷</n-button>
            </template>
            <div v-if="tagList.length" style="max-height:300px;overflow:auto">
              <div v-for="t in tagList" :key="t.id"
                style="display:flex;align-items:center;gap:8px;padding:6px 4px;border-bottom:1px dashed rgba(127,127,127,.2)">
                <span style="flex:1;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                  {{ t.name }}<n-text depth="3" style="font-size:11px">（{{ t.c }}）</n-text>
                </span>
                <n-button size="tiny" quaternary @click="renameTag(t)">改名</n-button>
                <n-button size="tiny" quaternary type="error" @click="deleteTag(t)">删除</n-button>
              </div>
            </div>
            <n-empty v-else size="small" description="暂无标签" style="padding:14px 0" />
          </n-popover>
          <n-popover trigger="click" placement="bottom-end" :width="460">
            <template #trigger>
              <n-button size="small" secondary>
                ★ 收藏{{ favList.length ? ' ' + favList.length : '' }}
              </n-button>
            </template>
            <div v-if="favList.length" style="max-height:320px;overflow:auto">
              <div v-for="f in favList" :key="f.path"
                style="display:flex;align-items:center;gap:8px;padding:6px 4px;border-bottom:1px dashed rgba(127,127,127,.2)">
                <div style="flex:1;min-width:0">
                  <div class="mono" style="cursor:pointer;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                    :title="'打开 ' + f.path" @click="openFav(f.path)">{{ f.path }}</div>
                  <div v-if="f.note" style="font-size:11.5px;opacity:.72;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                    :title="f.note">{{ f.note }}</div>
                </div>
                <n-button size="tiny" quaternary @click="editFavNote(f)">备注</n-button>
                <n-button size="tiny" quaternary @click="removeFav(f.path)">移除</n-button>
              </div>
            </div>
            <n-empty v-else size="small" description="暂无收藏——在文件列表点 ☆ 收藏常用文件" style="padding:18px 0" />
          </n-popover>
          <n-button size="small" secondary :loading="exporting" @click="exportCsv">
            导出 CSV
          </n-button>
          <n-radio-group :value="viewMode" size="small" @update:value="setViewMode">
            <n-radio-button value="list">列表</n-radio-button>
            <n-radio-button value="grid">网格</n-radio-button>
            <n-radio-button value="timeline">时间线</n-radio-button>
          </n-radio-group>
        </n-space>

        <!-- 扫描进度 -->
        <n-alert v-if="scanning" type="info" :bordered="false" class="fb-scanalert">
          <n-progress :percentage="scanning.n ? Math.round(scanning.i / scanning.n * 100) : 0"
            indicator-placement="inside" processing />
          <n-text class="mono" style="font-size:12px">
            {{ { scan: '扫描目录', index: '写入索引', clean: '清理失效记录', done: '完成' }[scanning.stage] }}
            {{ scanning.i }}/{{ scanning.n }} {{ scanning.detail }}
          </n-text>
        </n-alert>

        <!-- 保存的智能筛选 -->
        <div class="fb-saved" v-if="savedSearches.length || query.trim()">
          <n-tag v-for="s in savedSearches" :key="s.name" size="small" round closable
            :bordered="false" :title="s.query" @click="applySaved(s)" @close="removeSaved(s)">
            {{ s.name }}
          </n-tag>
          <n-popover trigger="click" :width="300" v-model:show="showSaveSearch">
            <template #trigger>
              <n-tag size="small" round :bordered="false" style="cursor:pointer">＋ 保存当前筛选</n-tag>
            </template>
            <n-space vertical size="small">
              <n-input v-model:value="saveSearchName" size="small" placeholder="筛选名称，如：本周改的 PDF"
                @keyup.enter="saveCurrentSearch" />
              <n-button size="tiny" type="primary" block @click="saveCurrentSearch">
                保存当前搜索
              </n-button>
            </n-space>
          </n-popover>
        </div>

        <!-- 类别筛选 -->
        <n-space class="fb-chips">
          <n-tag v-for="c in CATEGORIES" :key="c" size="medium" round
            :type="category === c ? 'primary' : 'default'" style="cursor:pointer"
            @click="pickCategory(c)">{{ c }}</n-tag>
          <n-tag size="medium" round :type="!category && !currentDir ? 'primary' : 'default'"
            style="cursor:pointer" @click="pickCategory(null); pickDir(null)">全部</n-tag>
          <n-text depth="3" style="align-self:center">
            共 {{ total }}{{ totalCapped ? '+' : '' }} 个文件{{ category ? ' · ' + category : '' }}{{ currentDir ? ' · ' + currentDir : '' }}
            <template v-if="lastEvent"> · 最近动态：{{ lastEvent.kind === 'added' ? '新增' : '移除' }} {{ lastEvent.path.split(/[\\/]/).pop() }}</template>
          </n-text>
        </n-space>

        <!-- 目录树（可折叠） -->
        <div class="fb-dirtree-wrap">
          <n-button size="tiny" quaternary @click="toggleDirTree">
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

        <!-- 批量操作栏（列表/网格多选后出现）。此前写作 view === 'list'，
             但 view 并非本组件的变量（永远 undefined），批量栏从未显示过 -->
        <n-space v-if="checkedRows.length" align="center" class="fb-batchbar">
          <n-text>已选 <b>{{ checkedRows.length }}</b> 个</n-text>
          <n-button size="small" type="primary" ghost :loading="transferring" @click="transferFiles('move')">移动到…</n-button>
          <n-button size="small" type="primary" ghost :loading="transferring" @click="transferFiles('copy')">复制到…</n-button>
          <n-button size="small" type="primary" ghost @click="openRename">重命名</n-button>
          <n-button size="small" type="error" ghost @click="trashSelected">删除到回收站</n-button>
          <n-button size="small" @click="copyPaths">复制路径</n-button>
          <n-button size="small" quaternary @click="checkedRows = []">清空选择</n-button>
          <n-text depth="3" style="font-size:11.5px">快捷键：Ctrl+A 全选 · Space 预览 · F2 重命名 · Delete 删除 · Enter 打开 · Ctrl+K 全局搜索</n-text>
        </n-space>

        <!-- 时间线：按修改时间区间筛选，结果按天分组 -->
        <div v-if="viewMode === 'timeline'">
          <n-space align="center" size="small" style="margin-bottom:10px">
            <n-tag v-for="c in timeChips" :key="c.key" size="small" round
              :type="timeRange === c.key ? 'primary' : 'default'" style="cursor:pointer"
              @click="pickTimeRange(c.key)">{{ c.label }}</n-tag>
            <n-date-picker v-if="timeRange === 'custom'" :value="customRange" type="daterange"
              size="small" :clearable="false" style="width:262px" @update:value="onCustomRange" />
            <n-text depth="3" style="font-size:11.5px">按「修改时间」统计，不是创建时间</n-text>
          </n-space>
          <n-spin :show="loading">
            <div class="fb-timeline" style="min-height:180px">
              <div v-for="g in groupedByDay" :key="g.key" class="fb-tlday">
                <div class="fb-tlday-head">
                  <span class="fb-tldot"></span>
                  <b>{{ g.label }}</b>
                  <n-text depth="3" style="font-size:12px">{{ g.items.length }} 个</n-text>
                </div>
                <div v-for="r in g.items" :key="r.path" class="fb-tlrow"
                  :class="{ sel: isChecked(r.path) }" :title="r.path"
                  @click="(e) => { if (e.detail === 1) isImg(r) ? openPreview(r) : previewFile(r) }"
                  @dblclick="openItem(r)">
                  <div class="fb-grid-check" :class="{ on: isChecked(r.path) }" title="选择"
                    @click.stop="toggleCheck(r.path)">{{ isChecked(r.path) ? '✓' : '' }}</div>
                  <span class="fb-tltime mono">{{ fmtClock(r.mtime) }}</span>
                  <img v-if="isImg(r)" :src="thumbUrl(r)" class="fb-tlthumb" loading="lazy" alt="" />
                  <span v-else class="fb-ext-badge fb-tlbadge"
                    :style="{ '--h': String(_HUES[r.category] || 220) }">
                    {{ (r.ext || 'file').slice(0, 3).toUpperCase() }}
                  </span>
                  <span class="fb-tlname">{{ r.name }}</span>
                  <span class="mono fb-tlsize">{{ fmtSize(r.size) }}</span>
                </div>
              </div>
              <n-empty v-if="!items.length" description="该时间段内没有文件" style="padding:40px 0" />
            </div>
          </n-spin>
        </div>

        <!-- 网格视图：图片/普通浏览两相宜，悬停出现勾选框可批量操作 -->
        <n-spin v-else-if="viewMode === 'grid'" :show="loading">
          <div class="thumb-grid" style="min-height:220px">
          <div v-for="r in items" :key="r.path" class="thumb-card" :class="{ sel: isChecked(r.path) }"
            :title="r.path"
            @click="(e) => { if (e.detail === 1) isImg(r) ? openPreview(r) : previewFile(r) }"
            @dblclick="openItem(r)">
            <div class="fb-grid-check" :class="{ on: isChecked(r.path) }" title="选择"
              @click.stop="toggleCheck(r.path)">{{ isChecked(r.path) ? '✓' : '' }}</div>
            <img v-if="isImg(r)" :src="thumbUrl(r)" loading="lazy" alt="" />
            <div v-else class="fb-ext-badge fb-grid-badge"
              :style="{ '--h': String(_HUES[r.category] || 220) }">
              {{ (r.ext || 'file').slice(0, 4).toUpperCase() }}
            </div>
            <div class="thumb-name" style="display:flex;justify-content:space-between;gap:6px">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ r.name }}</span>
              <span class="mono" style="opacity:.55;flex-shrink:0">{{ fmtSize(r.size) }}</span>
            </div>
          </div>
          <n-empty v-if="!items.length" description="没有匹配的文件" style="grid-column:1/-1;padding:40px 0" />
          </div>
        </n-spin>

        <!-- 统一列表（第一列缩略图，图片显示缩略图、其他显示类型图标） -->
        <n-data-table v-else :columns="batchColumns" :data="items" :loading="loading"
          :max-height="520" size="small" :row-key="(r) => r.path" :row-props="rowProps"
          v-model:checked-row-keys="checkedRows" />

        <n-space v-if="total > pageSize" justify="center">
          <n-pagination v-model:page="page" :page-count="Math.ceil(total / pageSize)"
            @update:page="refresh" />
        </n-space>

        <!-- 内容搜索结果（文档 + 图片语义，融合展示） -->
        <n-card v-if="contentResults && (contentResults.results.length || contentResults.images.length)"
          size="small" title="内容匹配（文档正文 + 知识库 + 图片语义）">
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
          <n-empty :description="contentResults.fulltext_reason
            || '没有正文命中——正文级搜索需先在 设置 → 全盘内容索引 建立索引；也可把文件夹加入「知识库」做语义搜索'" />
        </n-card>
      </n-space>
    </n-card>

    <!-- 键盘速查 -->
    <n-modal v-model:show="showKeys" preset="card" title="键盘快捷键" style="width:440px">
        <div class="fb-keys">
        <div class="fb-key-row"><span class="mono">Ctrl+K</span><span>全局快速唤起（搜文件 / 跳页面 / 切主题）</span></div>
        <div class="fb-key-row"><span class="mono">Ctrl+F</span><span>聚焦搜索框</span></div>
        <div class="fb-key-row"><span class="mono">Enter</span><span>搜索后打开第一项结果</span></div>
        <div class="fb-key-row"><span class="mono">Shift+Enter</span><span>搜索文件内容（知识库/语义）</span></div>
        <div class="fb-key-row"><span class="mono">Ctrl+A</span><span>全选本页文件</span></div>
        <div class="fb-key-row"><span class="mono">Space</span><span>快速预览选中文件 / 再按关闭</span></div>
        <div class="fb-key-row"><span class="mono">Enter</span><span>打开选中的文件</span></div>
        <div class="fb-key-row"><span class="mono">F2</span><span>重命名（单个就地改 / 多个进批量）</span></div>
        <div class="fb-key-row"><span class="mono">Delete</span><span>删除到回收站</span></div>
        <div class="fb-key-row"><span class="mono">Ctrl+C</span><span>复制路径</span></div>
        <div class="fb-key-row"><span class="mono">← →</span><span>灯箱内翻页</span></div>
        <div class="fb-key-row"><span class="mono">Esc</span><span>关闭预览 / 弹窗</span></div>
      </div>
    </n-modal>

    <!-- 工具结果弹窗（OCR 取字 / 哈希校验） -->
    <n-modal :show="!!toolModal" preset="card" :title="toolModal?.title" style="width:560px"
      @update:show="(v) => { if (!v) toolModal = null }">
      <n-spin :show="!!toolModal?.loading">
        <pre class="mono" style="white-space:pre-wrap;word-break:break-all;min-height:80px;max-height:50vh;overflow:auto;margin:0">{{ toolModal?.text || '处理中…' }}</pre>
      </n-spin>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" type="primary" :disabled="toolModal?.loading || !toolModal?.text" @click="copyToolText">复制</n-button>
          <n-button size="small" @click="toolModal = null">关闭</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 右键菜单 -->
    <n-dropdown trigger="manual" :show="menuShow" :x="menuX" :y="menuY" placement="bottom-start"
      :options="menuOptions" @select="onMenuSelect" @clickoutside="menuShow = false" />

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
          <n-radio-button v-if="renameMode === 'single'" value="single">单个</n-radio-button>
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

        <n-space v-else-if="renameMode === 'ai'" align="center">
          <n-text depth="3">用本地模型按内容/名称理解批量起名</n-text>
          <n-button size="small" type="primary" ghost :loading="renameLoading" @click="aiSuggest">
            AI 一键起名
          </n-button>
        </n-space>
        <n-text v-else-if="renameMode === 'single'" depth="3" style="font-size:12px">
          在下方表格里直接修改新文件名，确认后执行
        </n-text>

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
        <video v-else-if="filePreview?.type === 'video'" :src="filePreview.url"
          controls autoplay style="width:100%;max-height:68vh;border-radius:8px;background:#000"></video>
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
