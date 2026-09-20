<script setup>
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NText, NTag, NDataTable, NSelect, NSwitch,
  NAlert, NProgress, NInputGroup, useMessage, NModal, NForm, NFormItem, NDescriptions, NDescriptionsItem,
  NPopconfirm, NList, NListItem,
} from 'naive-ui'
import { api, on } from '../lib/bridge'
import { THEMES, AUTO_KEY, loadThemeKey, saveThemeKey } from '../lib/theme'

const message = useMessage()

// ---------- 外观/主题 ----------
const themeKey = ref(loadThemeKey())
const themeList = Object.values(THEMES)

function pickTheme(k) {
  themeKey.value = k
  saveThemeKey(k)
  window.dispatchEvent(new CustomEvent('fb-theme', { detail: k }))
}
// ---------- Ollama ----------
const status = ref(null)
const host = ref('')
const chatModel = ref('')
const chatModelOptions = ref([])
const pullState = ref(null)   // {model, pct, status, done}
const recommend = ref(null)

async function refresh() {
  status.value = await api('get_status')
  host.value = status.value.ollama.host
  chatModel.value = status.value.chat_model
  const opts = []
  for (const m of status.value.ollama.models) {
    if (!m.startsWith('bge')) opts.push({ label: m, value: m })
  }
  chatModelOptions.value = opts
}

async function saveOllama() {
  await api('save_ollama_settings', host.value, chatModel.value)
  message.success('已保存')
  refresh()
  window.dispatchEvent(new CustomEvent('fb-refresh'))
}

const MODEL_SIZES = {
  'qwen3:4b': '~3 GB',
  'qwen3:8b': '~6 GB',
  'qwen3:14b': '~10 GB',
  'qwen3:32b': '~22 GB',
  'bge-m3': '~1.2 GB',
  'qwen2.5vl:7b': '~6 GB',
}
// 本地对话模型候选（按推荐度排序，推荐项自动打标）
const localModelCards = ['qwen3:8b', 'qwen3:14b', 'qwen3:32b', 'qwen3:4b']

async function pull(model) {
  pullState.value = { model, pct: 0, status: '准备中…' }
  await api('pull_model', model)
}

let offs = []
offs.push(on('pull_start', (p) => { pullState.value = { model: p.model, pct: 0, status: '开始下载' } }))
offs.push(on('pull_progress', (p) => { pullState.value = p }))
offs.push(on('pull_done', (p) => {
  message.success(`模型 ${p.model} 下载完成`)
  pullState.value = null
  refresh()
  // 若下载的是推荐模型，自动设为当前对话模型
  if (recommend.value?.local?.model === p.model) {
    chatModel.value = p.model
    saveOllama()
  }
  window.dispatchEvent(new CustomEvent('fb-refresh'))
}))
offs.push(on('pull_error', (e) => { message.error(String(e)); pullState.value = null }))
onUnmounted(() => offs.forEach((f) => f()))

async function installOllama() {
  await api('open_url', 'https://ollama.com/download/windows')
  message.info('已打开 Ollama 官网，下载安装后它会自动在后台运行，然后回到这里检测')
}

// ---------- 规则 ----------
const rules = ref({ categories: [], rules: [] })
const showAdd = ref(false)
const newRule = ref({ pattern: '', category: '文档', sub: '' })
const filterExt = ref('')

async function loadRules() {
  rules.value = await api('get_rules')
}

const filteredRules = ref([])
function applyFilter() {
  const q = filterExt.value.trim().toLowerCase()
  const src = rules.value.rules
  let rows = q ? src.filter((r) => r.pattern.includes(q)) : src
  // 只展示前 200 条避免卡顿
  filteredRules.value = rows.slice(0, 200)
}

const ruleColumns = [
  { title: '扩展名', key: 'pattern', width: 110, render: (r) => '.' + r.pattern },
  { title: '类别', key: 'category', width: 110 },
  { title: '子目录', key: 'sub', width: 130, render: (r) => r.sub || '-' },
  { title: '来源', key: 'custom', width: 160, render: (r) => r.custom
      ? h(NSpace, { size: 'small', align: 'center' }, { default: () => [
          h(NTag, { size: 'small', type: 'info', bordered: false }, { default: () => '自定义' }),
          h(NButton, { size: 'tiny', type: 'error', ghost: true, onClick: () => delRule(r.pattern) },
            { default: () => '删除' }),
        ] })
      : h(NTag, { size: 'small', bordered: false }, { default: () => '内置' }) },
]

async function addRule() {
  if (!newRule.value.pattern.trim()) return message.warning('请输入扩展名，如 dat')
  await api('add_rule', newRule.value.pattern.trim(), newRule.value.category, newRule.value.sub)
  message.success('规则已添加')
  showAdd.value = false
  newRule.value = { pattern: '', category: '文档', sub: '' }
  loadRules()
}

async function delRule(pattern) {
  await api('delete_rule', pattern)
  loadRules()
}

// ---------- 数据目录管理 ----------
const dataDir = ref(null)
const switching = ref(false)
const switchStatus = ref(null)
const pendingDir = ref(null)
const moveMode = ref('copy')   // copy=保留原目录作为备份；move=重启后删除原目录

let dirOffs = []
dirOffs.push(on('data_switch_status', (m) => { switchStatus.value = m }))
onUnmounted(() => dirOffs.forEach((f) => f()))

async function loadDataDir() {
  try { dataDir.value = await api('data_dir_status') } catch (e) { /* 忽略 */ }
}

// ---------- 数据库维护 ----------
const dbMaint = ref({ db_size: 0, wal_size: 0, backups: 0, vacuum_running: false })
const fmtMB = (n) => ((n || 0) / 1e6).toFixed(1) + ' MB'

async function loadDbMaint() {
  try { dbMaint.value = await api('db_maintenance_info') } catch (e) { /* 忽略 */ }
}

async function doVacuum() {
  const r = await api('vacuum_db')
  if (r.ok) {
    dbMaint.value.vacuum_running = true
    message.info('压缩已开始，完成后将提示（大库可能需要一两分钟）')
  } else {
    message.error(r.error || '启动失败')
  }
}
offs.push(on('db_vacuum_done', (r) => {
  dbMaint.value.vacuum_running = false
  if (r.ok) {
    const saved = ((r.before - r.after) / 1e6).toFixed(1)
    if (r.after >= r.before && r.note) {
      message.warning(`未回收空间：${r.note}`)
    } else {
      message.success(`压缩完成：${fmtMB(r.before)} → ${fmtMB(r.after)}（回收 ${saved} MB）`)
    }
  } else {
    message.error('压缩失败：' + (r.error || '未知'))
  }
  loadDbMaint()
  loadDataDir()
}))

async function pickNewDataDir() {
  const p = await api('pick_folder')
  if (!p) return
  pendingDir.value = p
}

async function doSwitch(overwrite = false) {
  if (!pendingDir.value) return
  switching.value = true
  switchStatus.value = '准备搬迁…'
  const r = await api('switch_data_dir', pendingDir.value, overwrite, moveMode.value)
  switching.value = false
  if (r.ok) {
    message.success(moveMode.value === 'move'
      ? '搬迁完成，重启后生效（旧目录将在重启后自动删除）'
      : '搬迁完成，重启应用后生效（旧目录保留为备份）')
    restartPending.value = true
    loadDataDir()
  } else if (r.error && r.error.includes('覆盖')) {
    // 目标已有数据库 → 弹确认
    pendingOverwrite.value = true
  } else {
    message.error('搬迁失败：' + r.error)
  }
}

const pendingOverwrite = ref(false)
const restartPending = ref(false)

async function confirmOverwrite() {
  pendingOverwrite.value = false
  await doSwitch(true)
}

async function doResetDir() {
  const r = await api('reset_data_dir')
  if (r.ok) {
    message.success('已恢复默认位置，重启应用后生效')
    restartPending.value = true
    loadDataDir()
  } else {
    message.error('恢复失败：' + (r.error || '未知错误'))
  }
}

async function restartNow() {
  await api('restart_app')
}

// ---------- 模型接入（本地 Ollama / 云端 API） ----------
const provider = ref(null)
const provMode = ref('ollama')
const provBase = ref('')
const provKey = ref('')
const provChatModel = ref('')
const provEmbedSource = ref('follow')
const provEmbedModel = ref('')
const provPresets = ref([])
const provTesting = ref(false)
const provTestResult = ref(null)
const provSaving = ref(false)

const embedSourceOptions = [
  { label: '跟随接入方式', value: 'follow' },
  { label: '本地 bge-m3（免费）', value: 'ollama' },
  { label: '云端向量模型', value: 'api' },
]

async function loadProvider() {
  try {
    provider.value = await api('llm_provider_status')
    const s = provider.value.summary
    provMode.value = s.mode
    provBase.value = s.api_base
    provKey.value = ''   // 密钥不回显，留空表示不修改
    provChatModel.value = s.api_chat_model
    provEmbedSource.value = s.embed_source
    provEmbedModel.value = s.api_embed_model
    provPresets.value = provider.value.presets
  } catch (e) { /* 忽略 */ }
}

async function saveProvider() {
  provSaving.value = true
  const r = await api('save_llm_provider', provMode.value, provBase.value,
    provKey.value, provChatModel.value, provEmbedSource.value, provEmbedModel.value)
  provSaving.value = false
  if (r.ok) {
    message.success('接入配置已保存')
    loadProvider(); refresh()
    window.dispatchEvent(new CustomEvent('fb-refresh'))
  } else {
    message.error(r.error || '保存失败')
  }
}

async function testProvider() {
  provTesting.value = true
  provTestResult.value = null
  provTestResult.value = await api('test_llm_provider')
  provTesting.value = false
}

function pickPreset(p) {
  provBase.value = p.value
  // 自动填充该服务商的推荐模型名
  if (p.models && p.models.length) {
    provChatModel.value = p.models[0].name
    message.info(`已填入推荐模型：${p.models[0].name}（可改）`)
  }
}

// ---------- 备份管理 ----------
const backups = ref([])
const backingUp = ref(false)

async function loadBackups() {
  try { backups.value = (await api('list_backups')).backups } catch (e) { /* 忽略 */ }
}

async function backupNow() {
  backingUp.value = true
  const r = await api('backup_now')
  backingUp.value = false
  if (r.ok) { message.success('备份完成：' + r.path.split('\\').pop()); loadBackups() }
  else message.error('备份失败：' + r.error)
}

async function restoreBk(b) {
  const ok = window.confirm(`用备份 ${b.file} 覆盖当前数据库？\n当前数据会先另存一份。恢复后需重启应用。`)
  if (!ok) return
  const r = await api('restore_backup', b.path)
  if (r.ok) message.success('已恢复，请重启应用生效')
  else message.error('恢复失败：' + r.error)
}

// ---------- 模型管理（含删除权） ----------
const modelsDetail = ref([])
const deleting = ref(false)
const imgSem = ref(null)          // 图片语义状态
const imgDescProgress = ref(null) // 描述生成进度
// 系统内置 OCR（get_status.winocr）：零模型的图中文字搜索能力
const winOcrOk = computed(() => !!status.value?.winocr?.available)
const winOcrLangs = computed(() => (status.value?.winocr?.languages || []).join(' / ') || '-')

async function loadModels() {
  try {
    modelsDetail.value = (await api('list_models_detail')).models
  } catch (e) { /* 忽略 */ }
}

async function deleteModel(m) {
  deleting.value = true
  const r = await api('delete_model', m)
  deleting.value = false
  if (r.ok) {
    message.success(`已删除 ${m}`)
    loadModels(); refresh()
    window.dispatchEvent(new CustomEvent('fb-refresh'))
  } else {
    message.error(`删除失败：${r.error}`)
  }
}

let imgOffs = []
imgOffs.push(on('img_desc_start', (p) => { imgDescProgress.value = { i: 0, n: p.pending } }))
imgOffs.push(on('img_desc_progress', (p) => { imgDescProgress.value = p }))
imgOffs.push(on('img_desc_done', (r) => {
  imgDescProgress.value = null
  message.success(`图片语义描述完成：新增 ${r.described || 0}，跳过 ${r.skipped || 0}`)
  loadImgSem()
}))
imgOffs.push(on('img_desc_error', (e) => { imgDescProgress.value = null; message.error(String(e)) }))
onUnmounted(() => imgOffs.forEach((f) => f()))

// ---------- 全盘内容索引（正文进 FTS，手动启动 + 预算受控） ----------
const ci = ref(null)           // {status: {...}, plan: {...}|null}
const ciBusy = ref(false)
const ciRunning = computed(() => !!ci.value?.status?.running)

async function loadContentIndex() {
  try { ci.value = await api('content_index_status') } catch (e) { /* 忽略 */ }
}

async function ciStart() {
  ciBusy.value = true
  await api('content_index_start')
  await loadContentIndex()
  ciBusy.value = false
}

async function ciStop() {
  await api('content_index_stop')
  message.info('已请求停止，当前文件处理完即退出')
}

let ciOffs = []
ciOffs.push(on('content_index_progress', (p) => {
  if (!ci.value) ci.value = { status: {}, plan: null }
  ci.value.status = { ...ci.value.status, ...p, running: true }
}))
ciOffs.push(on('content_index_done', (s) => {
  if (s.budget_hit) message.warning('已达预算上限，可在设置里放宽篇数或体积上限')
  else message.success(`内容索引结束：成功 ${s.ok || 0} 篇，失败 ${s.failed || 0} 篇`)
  loadContentIndex()
}))
onUnmounted(() => ciOffs.forEach((f) => f()))
loadContentIndex()

// ---------- 功能开关 ----------
const autoKb = ref(true)
const autostart = ref(false)

async function loadToggles() {
  const st = await api('get_status')
  autoKb.value = st.auto_kb
  const as = await api('autostart_status')
  autostart.value = as.enabled
}

async function toggleAutoKb(v) {
  autoKb.value = v
  await api('set_auto_kb', v)
  message.success(v ? '自动知识库已开启：监控目录的新文档将自动索引' : '自动知识库已关闭')
}

async function toggleAutostart(v) {
  const r = await api('set_autostart', v)
  if (r.ok) {
    autostart.value = v
    message.success(v ? '将在开机时自动启动并最小化' : '已取消开机自启')
  } else {
    message.error('设置失败：' + r.error)
  }
}

async function loadImgSem() {
  try { imgSem.value = await api('img_semantic_status') } catch (e) { /* 忽略 */ }
}

async function toggleImgSem(v) {
  const r = await api('set_img_semantic', v)
  if (!v) {
    message.info('图片语义搜索已关闭')
    loadImgSem()
    return
  }
  if (r.model_ready) {
    message.success('已开启，开始为图片生成语义描述（后台进行）')
  } else {
    message.warning(`已开启，但视觉模型 ${r.vl_model} 未安装——下方下载后自动开始`)
  }
  loadImgSem()
}

async function pullVl() {
  await api('pull_model', 'qwen2.5vl:7b')
  pullState.value = { model: 'qwen2.5vl:7b', pct: 0, status: '排队下载' }
}

// ---------- 自动扫描监控目录 ----------
const watchRoots = ref([])
const rescanning = ref(false)

const rootColumns = [
  { title: '目录', key: 'path', ellipsis: { tooltip: true }, render: (r) => r.path },
  { title: '标签', key: 'label', width: 100 },
  { title: '已编目', key: 'n_files', width: 90 },
  { title: '操作', key: 'ops', width: 160, render: (r) => h(NSpace, { size: 'small' }, { default: () => [
      h(NButton, { size: 'tiny', onClick: () => api('open_path', r.path) }, { default: () => '打开' }),
      h(NButton, { size: 'tiny', type: 'error', ghost: true, onClick: () => removeWatchRoot(r.id) },
        { default: () => '移除' }),
    ] }) },
]

async function loadWatchRoots() {
  try {
    watchRoots.value = (await api('watch_roots')).roots
  } catch (e) { /* 忽略 */ }
}

async function addWatchRoot() {
  const p = await api('pick_folder')
  if (!p) return
  const r = await api('add_watch_root', p)
  if (r.error) return message.error(r.error)
  message.success('已添加，开始扫描')
  loadWatchRoots()
}

async function removeWatchRoot(id) {
  await api('remove_watch_root', id)
  message.success('已移除（索引记录一并清除）')
  loadWatchRoots()
}

async function rescanNow() {
  rescanning.value = true
  await api('rescan_library')
  message.info('后台扫描已启动，进度见「文件」页')
  setTimeout(() => { rescanning.value = false; loadWatchRoots() }, 3000)
}

// ---------- 其他设置 ----------
const imagesByYear = ref(true)

async function loadMisc() {
  imagesByYear.value = (await api('get_status')).images_by_year === '1'
}
async function toggleImagesByYear(v) {
  imagesByYear.value = v
  await api('set_images_by_year', v)
  message.success(v ? '图片将按年份归档' : '图片不再按年份归档')
}

// ---------- 全局热键 ----------
const hotkey = ref({ enabled: false, hotkey: 'Alt+Space' })

async function loadHotkey() {
  try { hotkey.value = await api('hotkey_status') } catch (e) { /* 忽略 */ }
}

async function toggleHotkey(v) {
  const r = await api('set_hotkey', v, hotkey.value.hotkey)
  if (r.ok) {
    hotkey.value.enabled = v
    message.success(v ? `全局热键已开启：任意程序下按 ${hotkey.value.hotkey} 呼出` : '全局热键已关闭')
  } else {
    message.error('设置失败：' + (r.error || '未知错误'))
  }
}

async function saveHotkeyCombo() {
  if (!hotkey.value.hotkey.trim()) return message.warning('请输入组合键，如 Alt+Space')
  const r = await api('set_hotkey', hotkey.value.enabled, hotkey.value.hotkey.trim())
  if (r.ok) message.success(`热键已应用：${hotkey.value.hotkey.trim()}`)
  else message.error('设置失败：' + (r.error || '未知错误'))
}

// ---------- 排除目录（扫描时跳过） ----------
const excludes = ref([])

async function loadExcludes() {
  try { excludes.value = (await api('list_excludes')).rules } catch (e) { /* 忽略 */ }
}

async function addExclude() {
  const p = await api('pick_folder')
  if (!p) return
  const r = await api('add_exclude', p)
  if (r.error) return message.error(r.error)
  message.success('已添加排除目录，下次扫描生效（可点上方「立即重新扫描」立即应用）')
  loadExcludes()
}

async function removeExclude(id) {
  await api('remove_exclude', id)
  message.success('已移除排除规则')
  loadExcludes()
}

// ---------- 自动整理（可选项） ----------
const autoOrganize = ref({ enabled: false, mode: 'report', root: '' })
const organizeModeOptions = [
  { label: '仅生成整理报告（不移动文件）', value: 'report' },
  { label: '自动移动（可一键撤销）', value: 'move' },
]

async function loadAutoOrganize() {
  try { autoOrganize.value = await api('auto_organize_status') } catch (e) { /* 忽略 */ }
}

async function toggleAutoOrganize(v) {
  const r = await api('set_auto_organize', v, autoOrganize.value.mode, autoOrganize.value.root)
  if (r.ok) {
    autoOrganize.value.enabled = v
    message.success(v ? '自动整理已开启' : '自动整理已关闭')
  } else message.error('设置失败')
}

async function changeOrganizeMode(mode) {
  await api('set_auto_organize', autoOrganize.value.enabled, mode, autoOrganize.value.root)
  autoOrganize.value.mode = mode
  message.success(mode === 'move' ? '新文件将自动移动（每步可撤销）' : '仅出报告，不移动文件')
}

async function pickOrganizeRoot() {
  const p = await api('pick_folder')
  if (p) {
    autoOrganize.value.root = p
    await api('set_auto_organize', autoOrganize.value.enabled, autoOrganize.value.mode, p)
    message.success('自动整理目标目录已设置')
  }
}

// ---------- 关于 ----------
const about = ref(null)
const checkingUpdate = ref(false)
const updateInfo = ref(null)
async function loadAbout() {
  try { about.value = await api('app_version') } catch (e) { /* 忽略 */ }
}
async function checkUpdate() {
  checkingUpdate.value = true
  updateInfo.value = null
  try {
    updateInfo.value = await api('check_update')
    if (!updateInfo.value.outdated && !updateInfo.value.latest) {
      message.info(updateInfo.value.error || '检查失败，请先配置更新源')
    }
  } catch (e) { message.error('检查更新失败：' + e) }
  checkingUpdate.value = false
}

onMounted(() => {
  refresh()
  loadRules().then(applyFilter)
  loadMisc()
  loadWatchRoots()
  loadModels()
  loadToggles()
  loadImgSem()
  loadDataDir()
  loadDbMaint()
  loadBackups()
  loadProvider()
  loadHotkey()
  loadExcludes()
  loadAutoOrganize()
  loadAbout()
  api('recommend_model').then((r) => (recommend.value = r))
})
</script>

<template>
  <div class="fb-page">
    <n-space vertical size="large">
      <!-- ============ Ollama ============ -->
      <n-card title="本地模型服务 (Ollama)">
        <n-space vertical size="large">
          <n-alert v-if="status && !status.ollama.running" type="error">
            未检测到 Ollama 服务。FileButler 的 AI 能力（智能分类、语义检索、知识库问答）完全依赖本机 Ollama，免费且无需任何 API Key。
            <n-button style="margin-left:12px" type="primary" size="small" @click="installOllama">去下载安装</n-button>
          </n-alert>
          <n-alert v-else-if="status && (!status.ollama.chat_ready || !status.ollama.embed_ready)" type="warning">
            Ollama 已运行，但还缺模型：
            <n-tag v-if="!status.ollama.chat_ready" size="small">对话模型（qwen2.5）</n-tag>
            <n-tag v-if="!status.ollama.embed_ready" size="small">向量模型（bge-m3）</n-tag>
            <span style="margin-left:8px">在下方点击下载即可。</span>
          </n-alert>

          <n-space item-style="display:flex;align-items:center;gap:8px">
            <n-text style="width:80px">服务地址</n-text>
            <n-input v-model:value="host" style="width:320px" class="mono" placeholder="http://127.0.0.1:11434" />
          </n-space>
          <n-space item-style="display:flex;align-items:center;gap:8px">
            <n-text style="width:80px">对话模型</n-text>
            <n-select v-model:value="chatModel" :options="chatModelOptions" style="width:320px"
              placeholder="选择已安装的模型" tag filterable />
            <n-button type="primary" @click="saveOllama">保存</n-button>
          </n-space>

          <n-descriptions v-if="recommend" :column="3" bordered size="small" label-placement="left">
            <n-descriptions-item label="本机内存">{{ recommend.config.ram_gb }} GB</n-descriptions-item>
            <n-descriptions-item label="独立显卡">
              {{ recommend.config.gpu
                ? (recommend.config.gpu_name || recommend.config.vram_gb + ' GB 显存')
                : '未检测到' }}
            </n-descriptions-item>
            <n-descriptions-item label="推荐对话模型">
              <b class="mono">{{ recommend.local.model }}</b>（约 {{ recommend.local.size_gb }} GB）
            </n-descriptions-item>
            <n-descriptions-item label="推荐理由" :span="3">
              {{ recommend.local.reason }} · {{ recommend.local.note }}
            </n-descriptions-item>
          </n-descriptions>

          <n-space>
            <n-card v-for="m in localModelCards" :key="m" size="small" style="width:280px"
              :title="m">
              <template #header-extra>
                <n-tag v-if="recommend && recommend.local.model === m" size="tiny" type="success"
                  :bordered="false">推荐</n-tag>
                <n-tag v-if="status && status.ollama.models.includes(m)" type="success" size="small">已安装</n-tag>
                <n-button v-else size="small" type="primary" ghost @click="pull(m)">下载 {{ MODEL_SIZES[m] }}</n-button>
              </template>
            </n-card>
            <n-card size="small" style="width:280px" title="bge-m3（向量模型，必装）">
              <template #header-extra>
                <n-tag v-if="status && status.ollama.embed_ready" type="success" size="small">已安装</n-tag>
                <n-button v-else size="small" type="primary" ghost @click="pull('bge-m3')">下载 ~1.2 GB</n-button>
              </template>
            </n-card>
          </n-space>

          <template v-if="pullState">
            <n-progress :percentage="Math.min(99, pullState.pct)" indicator-placement="inside" processing />
            <n-text depth="3" class="mono">
              {{ pullState.model }} · {{ pullState.status }}
              {{ pullState.total ? '(' + (pullState.done / 1e9).toFixed(1) + ' / ' + (pullState.total / 1e9).toFixed(1) + ' GB)' : '' }}
            </n-text>
          </template>
        </n-space>
      </n-card>

      <!-- ============ 外观主题 ============ -->
      <n-card title="外观主题">
        <n-space :size="14">
          <!-- 跟随系统（auto）：亮暗各半预览 -->
          <div class="theme-card" :class="{ active: themeKey === AUTO_KEY }" @click="pickTheme(AUTO_KEY)">
            <div class="theme-preview" style="background:#f4f6f4">
              <div class="tp-side" style="background:#1c2333">
                <div class="tp-logo" style="background:linear-gradient(135deg,#34d399,#818cf8)"></div>
                <div class="tp-menu"></div>
                <div class="tp-menu"></div>
              </div>
              <div class="tp-main">
                <div class="tp-hero" style="background:linear-gradient(135deg,#94a3b8,#334155)"></div>
                <div class="tp-line"></div>
              </div>
            </div>
            <div class="theme-card-body">
              <b>跟随系统</b>
              <span>亮暗随 Windows 自动切换</span>
              <n-tag v-if="themeKey === AUTO_KEY" size="tiny" round type="primary"
                style="margin-top:4px; width: fit-content;">当前使用</n-tag>
            </div>
          </div>
          <div v-for="t in themeList" :key="t.key" class="theme-card"
            :class="{ active: themeKey === t.key }" @click="pickTheme(t.key)">
            <div class="theme-preview" :style="{ background: t.naive.common.bodyColor }">
              <div class="tp-side" :style="{ background: t.vars['--fb-sidebar-bg'] }">
                <div class="tp-logo" :style="{ background: t.swatch }"></div>
                <div class="tp-menu"></div>
                <div class="tp-menu"></div>
                <div class="tp-menu"></div>
              </div>
              <div class="tp-main">
                <div class="tp-hero" :style="{ background: t.swatch }"></div>
                <div class="tp-line"></div>
                <div class="tp-line short"></div>
              </div>
            </div>
            <div class="theme-card-body">
              <b>{{ t.name }}</b>
              <span>{{ t.desc }}</span>
              <n-tag v-if="themeKey === t.key" size="tiny" round type="primary"
                style="margin-top:4px; width: fit-content;">当前使用</n-tag>
            </div>
          </div>
        </n-space>
      </n-card>

      <!-- ============ 数据存储位置 ============ -->
      <n-card title="数据存储位置">
        <n-space vertical size="medium" v-if="dataDir">
          <div class="fb-kv">
            <span class="fb-kv-label">当前位置</span>
            <code class="fb-path" :title="dataDir.current">{{ dataDir.current }}</code>
            <n-tag size="small" round :type="dataDir.is_default ? 'default' : 'success'">
              {{ dataDir.is_default ? '默认' : '自定义' }}
            </n-tag>
          </div>
          <n-text depth="3" style="font-size:12.5px">
            数据库 + 缩略图缓存共 {{ (dataDir.total_size / 1e6).toFixed(1) }} MB。
            切换到同步盘/其他盘后，备份时只需拷贝该目录。
          </n-text>
          <n-space>
            <n-button size="small" round @click="api('open_data_dir')">打开数据目录</n-button>
            <n-button size="small" round type="primary" ghost @click="pickNewDataDir">更改位置…</n-button>
            <n-button v-if="!dataDir.is_default" size="small" round @click="doResetDir">恢复默认位置</n-button>
          </n-space>
          <div v-if="pendingDir" class="fb-pending">
            <n-text style="font-size:13px">搬迁到</n-text>
            <code class="fb-path" style="border-style: solid;">{{ pendingDir }}</code>
            <n-button size="small" round type="primary" :loading="switching" @click="doSwitch(false)">开始搬迁</n-button>
            <n-button size="small" round quaternary @click="pendingDir = null">取消</n-button>
            <n-radio-group v-model:value="moveMode" size="small" style="margin-left:8px">
              <n-radio value="copy">复制（原目录保留为备份）</n-radio>
              <n-radio value="move">迁移（重启后删除原目录，不占双份空间）</n-radio>
            </n-radio-group>
          </div>
          <n-alert v-if="restartPending" type="warning" :bordered="false">
            数据目录已切换，重启应用后完全生效。
            <n-button size="small" type="primary" style="margin-left:10px" @click="restartNow">立即重启</n-button>
          </n-alert>
        </n-space>
      </n-card>

      <!-- ============ 数据库维护 ============ -->
      <n-card title="数据库维护">
        <n-space vertical size="medium">
          <div class="fb-kv">
            <span class="fb-kv-label">库大小</span>
            <n-tag size="small" round>{{ fmtMB(dbMaint.db_size) }}</n-tag>
            <n-tag v-if="dbMaint.wal_size > 10 * 1e6" size="small" round type="warning">
              待落盘 {{ fmtMB(dbMaint.wal_size) }}
            </n-tag>
            <n-tag size="small" round depth="2">备份 {{ dbMaint.backups }} 份</n-tag>
          </div>
          <n-text depth="3" style="font-size:12.5px">
            大量删除/重建索引后库文件不会自动缩小，压缩（VACUUM）可回收空间。
            需要约 2 倍库大小的空闲磁盘，期间后台索引可能短暂暂停。
          </n-text>
          <n-space>
            <n-button size="small" round type="primary" ghost :loading="dbMaint.vacuum_running"
              :disabled="dbMaint.vacuum_running" @click="doVacuum">
              {{ dbMaint.vacuum_running ? '压缩中…' : '压缩数据库' }}
            </n-button>
            <n-button size="small" round @click="loadDbMaint">刷新</n-button>
          </n-space>
        </n-space>
      </n-card>

      <!-- ============ 模型接入 ============ -->
      <n-card title="模型接入方式">
        <n-space vertical size="medium">
          <n-radio-group :value="provMode" @update:value="(v) => (provMode = v)">
            <n-radio-button value="ollama">本地 Ollama（免费）</n-radio-button>
            <n-radio-button value="api">云端 API（OpenAI 兼容）</n-radio-button>
          </n-radio-group>

          <template v-if="provMode === 'api'">
            <n-space :size="8" :wrap="true">
              <n-tag v-for="p in provPresets" :key="p.value" size="small" round
                style="cursor:pointer" :type="provBase === p.value ? 'primary' : 'default'"
                @click="pickPreset(p)">{{ p.label }}</n-tag>
            </n-space>
            <div class="fb-kv">
              <span class="fb-kv-label">服务地址</span>
              <n-input v-model:value="provBase" placeholder="https://api.deepseek.com/v1"
                style="flex:1;min-width:280px" class="mono" />
            </div>
            <div class="fb-kv">
              <span class="fb-kv-label">API 密钥</span>
              <n-input v-model:value="provKey" type="password" show-password-on="click"
                placeholder="sk-…（已保存的密钥留空即不修改）" style="flex:1;min-width:280px" class="mono" />
            </div>
            <div class="fb-kv">
              <span class="fb-kv-label">对话模型</span>
              <n-input v-model:value="provChatModel" placeholder="如 deepseek-chat / glm-4.7 / gpt-4o-mini"
                style="flex:1;min-width:280px" class="mono" />
            </div>
            <div class="fb-kv">
              <span class="fb-kv-label">向量化</span>
              <n-select v-model:value="provEmbedSource" :options="embedSourceOptions"
                style="width:220px" />
              <n-input v-if="provEmbedSource === 'api'" v-model:value="provEmbedModel"
                placeholder="向量模型名，如 text-embedding-3-small" style="flex:1;min-width:240px" class="mono" />
            </div>
            <n-text depth="3" style="font-size:12px">
              省钱混搭推荐：对话走云端（质量好），向量化选「本地 bge-m3」（完全免费）。
              密钥仅保存在本机数据库，除你填写的服务商外不经过任何第三方。
            </n-text>
          </template>

          <n-space>
            <n-button type="primary" size="small" round :loading="provSaving" @click="saveProvider">保存</n-button>
            <n-button size="small" round :loading="provTesting" @click="testProvider">测试连接</n-button>
            <n-tag v-if="provider" size="small" round
              :type="provider.summary.chat.ok ? 'success' : 'error'">
              对话：{{ provider.summary.chat.reason }}
            </n-tag>
            <n-tag v-if="provider" size="small" round
              :type="provider.summary.embed.ok ? 'success' : 'warning'">
              向量：{{ provider.summary.embed.reason }}
            </n-tag>
          </n-space>
          <n-alert v-if="provTestResult" :bordered="false"
            :type="provTestResult.ok ? 'success' : 'error'">
            <template v-if="provTestResult.ok">
              ✓ 连通正常 · {{ provTestResult.latency_ms }}ms · 回复「{{ provTestResult.reply }}」
            </template>
            <template v-else>✗ {{ provTestResult.error }}</template>
          </n-alert>
        </n-space>
      </n-card>

      <!-- ============ 备份管理 ============ -->
      <n-card title="数据库备份">
        <n-space vertical size="large">
          <n-space>
            <n-button type="primary" ghost size="small" :loading="backingUp" @click="backupNow">立即备份</n-button>
            <n-text depth="3" style="font-size:12px;align-self:center">
              应用运行期间每日自动备份一次（后台进行，不阻塞退出），保留最近 5 份，存于数据目录 backups\ 下
            </n-text>
          </n-space>
          <n-list v-if="backups.length" bordered size="small">
            <n-list-item v-for="b in backups" :key="b.file">
              <n-space justify="space-between" style="width:100%" align="center">
                <n-space>
                  <n-text class="mono" style="font-size:12.5px">{{ b.file }}</n-text>
                  <n-text depth="3" style="font-size:12px">
                    {{ (b.size / 1e6).toFixed(1) }} MB · {{ new Date(b.mtime * 1000).toLocaleString() }}
                  </n-text>
                </n-space>
                <n-button size="tiny" type="warning" ghost @click="restoreBk(b)">恢复此备份</n-button>
              </n-space>
            </n-list-item>
          </n-list>
          <n-text v-else depth="3">暂无备份</n-text>
        </n-space>
      </n-card>

      <!-- ============ 配置导出 / 导入 ============ -->
      <n-card title="配置导出 / 导入">
        <n-space vertical size="medium">
          <n-space align="center">
            <n-button size="small" round type="primary" ghost @click="exportConfig">导出配置</n-button>
            <n-button size="small" round @click="triggerImport">导入配置</n-button>
            <input ref="importFileEl" type="file" accept=".json,application/json"
              style="display:none" @change="onImportFile" />
          </n-space>
          <n-text depth="3" style="font-size:12px">
            导出内容：自定义分类规则、整理模板、标签、收藏、排除目录、保存的筛选。
            换机或重装后导入即可恢复（合并式导入，不覆盖已有数据，绝不删除）。
          </n-text>
        </n-space>
      </n-card>

      <!-- ============ 模型管理 ============ -->
      <n-card title="已安装模型（可删除释放磁盘）">
        <n-space vertical size="large">
          <n-list bordered v-if="modelsDetail.length">
            <n-list-item v-for="m in modelsDetail" :key="m.name">
              <n-space justify="space-between" align="center" style="width:100%">
                <n-space align="center">
                  <n-text class="mono" strong>{{ m.name }}</n-text>
                  <n-tag size="small" type="success" v-if="m.is_chat">当前对话模型</n-tag>
                  <n-tag size="small" type="info" v-if="m.is_embed">向量模型</n-tag>
                  <n-tag size="small" type="warning" v-if="m.is_vl">视觉模型</n-tag>
                  <n-text depth="3">{{ (m.size / 1e9).toFixed(1) }} GB</n-text>
                </n-space>
                <n-popconfirm @positive-click="deleteModel(m.name)">
                  <template #trigger>
                    <n-button size="small" type="error" ghost :loading="deleting">删除</n-button>
                  </template>
                  确定删除 {{ m.name }}（{{ (m.size / 1e9).toFixed(1) }} GB）？
                  删除后相关 AI 功能将不可用，可随时重新下载。
                  <template v-if="m.is_chat || m.is_embed"><br/><b>这是当前正在使用的模型，删除会影响核心功能！</b></template>
                </n-popconfirm>
              </n-space>
            </n-list-item>
          </n-list>
          <n-text v-else depth="3">Ollama 未运行或尚无模型</n-text>
        </n-space>
      </n-card>

      <!-- ============ 全盘内容索引 ============ -->
      <n-card title="全盘内容索引（正文搜索）">
        <n-space vertical size="small">
          <n-text depth="3" style="font-size:12.5px">
            把已编目文档（PDF / Word / PPT / Excel / 文本 / 代码）的正文抽进本地全文索引，
            之后「搜内容」可直接命中正文里的词。不依赖 AI 模型、不产生向量，
            与上面的「自动知识库索引」互不影响。
          </n-text>
          <n-space size="small" align="center" style="margin-top:4px">
            <n-button size="small" type="primary" ghost :disabled="ciRunning" :loading="ciBusy"
              @click="ciStart">{{ ciRunning ? '索引进行中…' : '开始索引' }}</n-button>
            <n-button size="small" :disabled="!ciRunning" @click="ciStop">停止</n-button>
            <n-button size="small" quaternary @click="loadContentIndex">刷新</n-button>
          </n-space>
          <n-progress v-if="ciRunning && ci?.status?.total" type="line" :percentage="Math.min(99.5, Math.round((ci.status.done || 0) / ci.status.total * 100))"
            indicator-placement="inside" processing />
          <n-text depth="3" style="font-size:12px">
            已索引 {{ ci?.status?.indexed?.ok ?? 0 }} 篇 ·
            正文 {{ fmtMB(ci?.status?.indexed?.bytes ?? 0) }} ·
            失败 {{ ci?.status?.indexed?.failed ?? 0 }} 篇
          </n-text>
          <n-text v-if="ci?.plan" depth="3" style="font-size:12px">
            待处理候选 {{ ci.plan.candidates }} 篇，预算内还会索引 {{ ci.plan.will_index }} 篇 ·
            上限 {{ ci.plan.budget.max_docs }} 篇 / {{ (ci.plan.budget.max_body_bytes / 1e9).toFixed(1) }} GB
          </n-text>
          <n-text depth="3" style="font-size:12px">
            单文件不超过 10 MB、正文最多取前 20 万字符；可随时停止，已完成的进度会保留并增量续跑。
          </n-text>
          <n-text v-if="ci?.status?.budget_hit || (ci?.plan && ci.plan.budget.docs_left === 0)"
            type="warning" style="font-size:12px">
            已达预算上限——放宽上限后再次点「开始索引」即可继续。
          </n-text>
        </n-space>
      </n-card>

      <!-- ============ 自动化开关 ============ -->
      <n-card title="自动化">
        <n-space vertical size="large">
          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="autoKb" @update:value="toggleAutoKb" />
            <div>
              <n-text strong>自动知识库索引</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                监控目录里的新文档（PDF/Word/代码等）自动向量化进知识库，可直接内容搜索与问答
              </n-text>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="!!imgSem?.enabled" @update:value="toggleImgSem" />
            <div>
              <n-text strong>图片语义搜索</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                用视觉模型为图片生成描述，搜「日落」即可找到夕阳照片（需下载视觉模型）
              </n-text>
              <n-text v-if="winOcrOk" depth="3" style="display:block;font-size:12px">
                系统内置 OCR 可用（{{ winOcrLangs }}）：不装任何模型也能搜图中的文字（发票号/截图文字），扫描版 PDF 自动识别
              </n-text>
              <n-text v-else depth="3" style="display:block;font-size:12px">
                系统内置 OCR 不可用：可在 系统设置→时间和语言→语言→选项 中添加「光学字符识别」后使用图中文字搜索
              </n-text>
              <n-space v-if="imgSem" style="margin-top:4px" size="small">
                <n-tag size="small" :type="imgSem.model_ready ? 'success' : 'warning'">
                  {{ imgSem.model_ready ? imgSem.vl_model + ' 已安装' : imgSem.vl_model + ' 未安装' }}
                </n-tag>
                <n-tag v-if="imgSem.described" size="small">已描述 {{ imgSem.described }} 张</n-tag>
                <n-tag v-if="imgSem.pending" size="small">待描述 {{ imgSem.pending }} 张</n-tag>
                <n-button v-if="!imgSem.model_ready" size="tiny" type="primary" ghost @click="pullVl">
                  下载视觉模型（~6 GB）
                </n-button>
                <n-button v-else-if="imgSem.pending" size="tiny" @click="api('describe_backlog')">
                  补描述
                </n-button>
              </n-space>
              <n-progress v-if="imgDescProgress"
                :percentage="imgDescProgress.n ? Math.round(imgDescProgress.i / imgDescProgress.n * 100) : 0"
                style="margin-top:6px" processing>
                正在描述 {{ imgDescProgress.i }}/{{ imgDescProgress.n }} · {{ imgDescProgress.name }}
              </n-progress>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="autostart" @update:value="toggleAutostart" />
            <div>
              <n-text strong>开机自动启动</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                开机后台常驻，文件监控与自动索引不间断；关闭窗口最小化到托盘，托盘菜单可退出
              </n-text>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="autoOrganize.enabled" @update:value="toggleAutoOrganize" />
            <div style="flex:1">
              <n-text strong>自动整理新文件</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                新文件进入监控目录后按规则自动归类；默认「仅出报告」绝不静默移动
              </n-text>
              <n-space v-if="autoOrganize.enabled" style="margin-top:6px" size="small" align="center">
                <n-select :value="autoOrganize.mode" :options="organizeModeOptions" size="small"
                  style="width:230px" @update:value="changeOrganizeMode" />
                <n-input :value="autoOrganize.root" placeholder="选择整理目标目录…" readonly
                  style="width:320px" class="mono">
                  <template #suffix>
                    <n-button size="tiny" @click="pickOrganizeRoot">浏览</n-button>
                  </template>
                </n-input>
              </n-space>
            </div>
          </n-space>
        </n-space>
      </n-card>

      <!-- ============ 自动扫描监控目录 ============ -->
      <n-card title="自动扫描的监控目录（只读索引，不移动文件）">
        <n-space vertical size="large">
          <n-space>
            <n-button type="primary" @click="addWatchRoot">＋ 添加监控目录</n-button>
            <n-button :loading="rescanning" @click="rescanNow">立即重新扫描</n-button>
            <n-text depth="3">默认包含桌面/文档/下载/图片/视频/音乐；后台实时监控变化自动更新索引</n-text>
          </n-space>
          <n-data-table v-if="watchRoots.length" :columns="rootColumns" :data="watchRoots" size="small" />
        </n-space>
      </n-card>

      <!-- ============ 排除目录 ============ -->
      <n-card title="排除目录（扫描时跳过）">
        <n-space vertical size="large">
          <n-space>
            <n-button type="primary" @click="addExclude">＋ 添加排除目录</n-button>
            <n-text depth="3">排除后该目录及其子目录不再编目、不进知识库；移除规则后下次扫描自动恢复</n-text>
          </n-space>
          <n-list v-if="excludes.length" bordered size="small">
            <n-list-item v-for="r in excludes" :key="r.id">
              <n-space justify="space-between" style="width:100%" align="center">
                <n-space>
                  <n-text class="mono" style="font-size:12.5px">{{ r.path }}</n-text>
                  <n-text depth="3" style="font-size:12px">{{ new Date(r.added_at * 1000).toLocaleString() }}</n-text>
                </n-space>
                <n-button size="tiny" type="error" ghost @click="removeExclude(r.id)">移除</n-button>
              </n-space>
            </n-list-item>
          </n-list>
          <n-text v-else depth="3">暂无自定义排除目录（内置会跳过微信/QQ 等缓存目录）</n-text>
        </n-space>
      </n-card>

      <!-- ============ 分类规则 ============ -->
      <n-card title="分类规则（扩展名 → 类别）">
        <n-space vertical size="large">
          <n-space>
            <n-input v-model:value="filterExt" placeholder="按扩展名筛选，如 pdf" style="width:240px" @input="applyFilter" />
            <n-button type="primary" @click="showAdd = true">＋ 自定义规则</n-button>
            <n-text depth="3">共 {{ rules.rules.length }} 条内置规则，自定义规则优先级更高</n-text>
          </n-space>
          <n-data-table :columns="ruleColumns" :data="filteredRules" :max-height="360" size="small"
            :row-key="(r) => r.pattern" virtual-scroll />
        </n-space>
      </n-card>

      <!-- ============ 其他 ============ -->
      <n-card title="整理选项">
        <n-space vertical>
          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="imagesByYear" @update:value="toggleImagesByYear" />
            <n-text>图片按拍摄年份归档（读取 EXIF，读不到的归入「未知年份」）</n-text>
          </n-space>
        </n-space>
      </n-card>

      <!-- ============ 关于 ============ -->
      <n-card title="关于 FileButler">
        <n-space vertical>
          <n-space align="center">
            <n-text strong style="font-size:14px">FileButler · 本地智能文件管家</n-text>
            <n-tag v-if="about" size="small" round type="info">v{{ about.version }}</n-tag>
            <n-button v-if="!checkingUpdate" size="tiny" secondary @click="checkUpdate">
              检查更新
            </n-button>
            <n-button v-else size="tiny" secondary loading>检查中…</n-button>
          </n-space>
          <n-alert v-if="updateInfo" :type="updateInfo.outdated ? 'warning' : 'success'" :bordered="false">
            <template #header>
              {{ updateInfo.outdated ? `发现新版本 v${updateInfo.latest}（当前 v${updateInfo.current}）`
                 : updateInfo.latest ? `已是最新版本 v${updateInfo.current}` : updateInfo.error }}
            </template>
            <template #default>
              <span v-if="updateInfo.outdated && updateInfo.download">
                <n-button size="tiny" type="primary" @click="api('open_url', updateInfo.download)">
                  去下载
                </n-button>
              </span>
              <span v-if="updateInfo.notes" style="margin-left:10px">{{ updateInfo.notes }}</span>
            </template>
          </n-alert>
          <n-text depth="3" style="font-size:12.5px">
            完全本地运行：自动编目 / 语法搜索 / 知识库问答 / 智能整理与去重。
            AI 能力由本机 Ollama（或你配置的云端 API）提供，文件数据零上传。
          </n-text>
        </n-space>
      </n-card>
    </n-space>

    <n-modal v-model:show="showAdd" preset="card" title="添加自定义规则" style="width:420px">
      <n-space vertical>
        <n-input v-model:value="newRule.pattern" placeholder="扩展名，如 dat、npy（不含点）" />
        <n-select v-model:value="newRule.category" :options="rules.categories.map(c => ({ label: c, value: c }))" />
        <n-input v-model:value="newRule.sub" placeholder="子目录（可留空），如 我的资料" />
        <n-button type="primary" @click="addRule">添加</n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="pendingOverwrite" preset="dialog" type="warning"
      title="目标目录已有数据库" positive-text="覆盖目标并搬迁" negative-text="取消"
      @positive-click="confirmOverwrite" @negative-click="pendingOverwrite = false">
      <span class="mono">{{ pendingDir }}</span> 里已存在 filebutler.db。
      覆盖后将使用当前数据替换目标目录里的旧数据，确定继续吗？
    </n-modal>
  </div>
</template>
