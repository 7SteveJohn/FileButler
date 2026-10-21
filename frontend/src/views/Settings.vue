<script setup>
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NText, NTag, NDataTable, NSelect, NSwitch,
  NAlert, NProgress, NInputGroup, useMessage, NModal, NForm, NFormItem, NDescriptions, NDescriptionsItem,
  NPopconfirm, NList, NListItem, NIcon,
} from 'naive-ui'
import { EyeOutline, EyeOffOutline } from '@vicons/ionicons5'
import { api, on } from '../lib/bridge'
import { THEMES, AUTO_KEY, loadThemeKey, saveThemeKey } from '../lib/theme'
import { useI18n } from 'vue-i18n'
import { LANGS, setLocale, currentLocale } from '../lib/i18n'

const message = useMessage()
const { t } = useI18n()

// ---------- 界面语言 ----------
const lang = ref(currentLocale())
function pickLang(k) {
  setLocale(k)
  lang.value = k
}

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
const keepAlive = ref('30m')  // 模型常驻时长：默认 30m，避免空闲 5 分钟被卸载后重载几十秒
const preload = ref(false)    // 启动时把模型预载进显存/内存
const numCtx = ref('8192')    // 上下文窗口 token：默认 8192，防止长资料被 Ollama 默认窗口静默截断
const keepAliveOptions = computed(() => [
  { label: t('settings.keep5'), value: '5m' },
  { label: t('settings.keep30'), value: '30m' },
  { label: t('settings.keep60'), value: '1h' },
  { label: t('settings.keepForever'), value: '-1' },
])
const numCtxOptions = computed(() => [
  { label: t('settings.ctx4096'), value: '4096' },
  { label: t('settings.ctx8192'), value: '8192' },
  { label: '16384', value: '16384' },
  { label: '32768', value: '32768' },
  { label: t('settings.ctxDefault'), value: '0' },
])

// Ollama 后台拉起就绪后自动重拉：模型下拉/在线状态不再停留在空列表
onMounted(() => { offs.push(on('ollama_ready', refresh)) })

async function refresh() {
  status.value = await api('get_status')
  host.value = status.value.ollama.host
  chatModel.value = status.value.chat_model
  keepAlive.value = status.value.ollama_keep_alive || '30m'
  preload.value = !!status.value.ollama_preload
  numCtx.value = status.value.ollama_num_ctx || '8192'
  const opts = []
  for (const m of status.value.ollama.models) {
    if (!m.startsWith('bge')) opts.push({ label: m, value: m })
  }
  chatModelOptions.value = opts
}

async function saveOllama() {
  await api('save_ollama_settings', host.value, chatModel.value, keepAlive.value, preload.value, numCtx.value)
  message.success(t('settings.m.saved'))
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
  pullState.value = { model, pct: 0, status: t('settings.m.preparing') }
  await api('pull_model', model)
}

let offs = []
offs.push(on('pull_start', (p) => { pullState.value = { model: p.model, pct: 0, status: t('settings.m.starting') } }))
offs.push(on('pull_progress', (p) => { pullState.value = p }))
offs.push(on('pull_done', (p) => {
  message.success(t('settings.m.pullDone', { m: p.model }))
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
  message.info(t('settings.m.ollamaOpened'))
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

const ruleColumns = computed(() => [
  { title: t('settings.colExt'), key: 'pattern', width: 110, render: (r) => '.' + r.pattern },
  { title: t('settings.colCat'), key: 'category', width: 110 },
  { title: t('settings.colSub'), key: 'sub', width: 130, render: (r) => r.sub || '-' },
  { title: t('settings.colSrc'), key: 'custom', width: 160, render: (r) => r.custom
      ? h(NSpace, { size: 'small', align: 'center' }, { default: () => [
          h(NTag, { size: 'small', type: 'info', bordered: false }, { default: () => t('settings.custom') }),
          h(NButton, { size: 'tiny', type: 'error', ghost: true, onClick: () => delRule(r.pattern) },
            { default: () => t('settings.del') }),
        ] })
      : h(NTag, { size: 'small', bordered: false }, { default: () => t('settings.builtin') }) },
])

async function addRule() {
  if (!newRule.value.pattern.trim()) return message.warning(t('settings.m.needExt'))
  await api('add_rule', newRule.value.pattern.trim(), newRule.value.category, newRule.value.sub)
  message.success(t('settings.m.ruleAdded'))
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
// 正文索引刚起步时是 KB 量级，一律按 MB 显示会变成「0.0 MB」，看着像没干活
const fmtBody = (n) => (n >= 1e6 ? fmtMB(n) : ((n || 0) / 1024).toFixed(0) + ' KB')

async function loadDbMaint() {
  try { dbMaint.value = await api('db_maintenance_info') } catch (e) { /* 忽略 */ }
}

async function doVacuum() {
  const r = await api('vacuum_db')
  if (r.ok) {
    dbMaint.value.vacuum_running = true
    message.info(t('settings.m.vacStarted'))
  } else {
    message.error(r.error || t('settings.m.startFail'))
  }
}
offs.push(on('db_vacuum_done', (r) => {
  dbMaint.value.vacuum_running = false
  if (r.ok) {
    const saved = ((r.before - r.after) / 1e6).toFixed(1)
    if (r.after >= r.before && r.note) {
      message.warning(t('settings.m.noSpace', { note: r.note }))
    } else {
      message.success(t('settings.m.vacDone', { b: fmtMB(r.before), a: fmtMB(r.after), s: saved }))
    }
  } else {
    message.error(t('settings.m.vacFail', { e: r.error || '?' }))
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
  switchStatus.value = t('settings.m.moving')
  const r = await api('switch_data_dir', pendingDir.value, overwrite, moveMode.value)
  switching.value = false
  if (r.ok) {
    message.success(moveMode.value === 'move'
      ? t('settings.m.movedDel')
      : t('settings.m.movedKeep'))
    restartPending.value = true
    loadDataDir()
  } else if (r.error && r.error.includes('覆盖')) {
    // 目标已有数据库 → 弹确认
    pendingOverwrite.value = true
  } else {
    message.error(t('settings.m.moveFail', { e: r.error }))
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
    message.success(t('settings.m.resetDone'))
    restartPending.value = true
    loadDataDir()
  } else {
    message.error(t('settings.m.resetFail', { e: r.error || '?' }))
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
const provKeyMask = ref('')   // 已保存密钥的打码回显（后端只回尾 4 位），明文永不下发
const keyEditing = ref(false)   // 本次聚焦后用户真的键入过；期间显示编辑值而非掩码
const keyInputRef = ref(null)   // 聚焦时全选掩码，直接输入即整段替换
const keyRevealed = ref(false)   // 点眼睛查看明文；再点恢复打码
const savedKeyPlain = ref('')    // 明文缓存：首次点眼睛才向后端取
const provChatModel = ref('')
const provEmbedSource = ref('follow')
const provEmbedModel = ref('')
const provPresets = ref([])
const provTesting = ref(false)
const provTestResult = ref(null)
const provSaving = ref(false)
const provModels = ref([])        // 从服务端拉到的模型列表（可下拉选）
const provFetching = ref(false)
const provDetecting = ref(false)

const embedSourceOptions = computed(() => [
  { label: t('settings.embFollow'), value: 'follow' },
  { label: t('settings.embLocal'), value: 'ollama' },
  { label: t('settings.embApi'), value: 'api' },
])

// 密钥框显示值：编辑中 > 查看明文 > 打码掩码。掩码常驻（聚焦不清空），
// 聚焦时全选，直接输入即整段替换
const keyDisplay = computed(() => {
  if (keyEditing.value || provKey.value) return provKey.value
  if (keyRevealed.value) return savedKeyPlain.value
  return provKeyMask.value
})

function onKeyFocus() {
  keyInputRef.value?.select()
}

function onKeyInput(v) {
  // 从掩码里插字会产生含 **** 的垃圾值；真密钥不会连着四个星号，不采纳
  if (v.includes('****')) return
  keyEditing.value = true
  provKey.value = v
}

function onKeyBlur() {
  if (!provKey.value) keyEditing.value = false  // 没输新值就离开，回到掩码显示
}

async function toggleKeyReveal() {
  if (!keyRevealed.value && provKeyMask.value && !savedKeyPlain.value) {
    try {
      const r = await api('reveal_api_key')
      savedKeyPlain.value = r.key || ''
    } catch (e) { savedKeyPlain.value = '' }
  }
  keyRevealed.value = !keyRevealed.value
}

async function loadProvider() {
  try {
    provider.value = await api('llm_provider_status')
    const s = provider.value.summary
    provMode.value = s.mode
    provBase.value = s.api_base
    provKey.value = ''   // 密钥不回显，留空表示不修改
    provKeyMask.value = s.api_key_masked || ''
    keyEditing.value = false
    savedKeyPlain.value = ''   // 重新载入后旧明文缓存作废
    keyRevealed.value = false
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
    message.success(t('settings.m.provSaved'))
    loadProvider(); refresh()
    window.dispatchEvent(new CustomEvent('fb-refresh'))
  } else {
    message.error(r.error || t('settings.saveFail2'))
  }
}

async function testProvider() {
  provTesting.value = true
  provTestResult.value = null
  provTestResult.value = await api('test_llm_provider')
  provTesting.value = false
}

async function fetchProvModels() {
  provFetching.value = true
  try {
    const r = await api('list_api_models', provBase.value, provKey.value)
    if (r.ok && r.models.length) {
      provModels.value = r.models
      message.info(t('settings.m.modelsGot', { n: r.models.length }))
    } else {
      provModels.value = []
      message.warning(r.error || t('settings.m.modelsEmpty'))
    }
  } catch (e) {
    message.error(String(e))
  } finally {
    provFetching.value = false
  }
}

async function detectLocal() {
  provDetecting.value = true
  try {
    const r = await api('detect_local_llm')
    const lm = r.lmstudio
    if (lm?.running) {
      provMode.value = 'api'
      provBase.value = lm.base
      provModels.value = lm.models || []
      if (lm.models?.length && !provChatModel.value) provChatModel.value = lm.models[0]
      message.success(t('settings.m.lmFound', { n: lm.models.length }))
      return
    }
    if (r.ollama?.running) {
      message.info(t('settings.ollamaLocal'))
      return
    }
    message.warning(t('settings.m.lmNotFound'))
  } finally {
    provDetecting.value = false
  }
}

function pickPreset(p) {
  provBase.value = p.value
  // 自动填充该服务商的推荐模型名
  if (p.models && p.models.length) {
    provChatModel.value = p.models[0].name
    message.info(t('settings.m.filledModel', { m: p.models[0].name }))
  } else {
    message.info(t('settings.m.filledBase'))
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
  if (r.ok) { message.success(t('settings.m.backupDone', { f: r.path.split('\\').pop() })); loadBackups() }
  else message.error(t('settings.m.backupFail', { e: r.error }))
}

async function restoreBk(b) {
  const ok = window.confirm(t('settings.m.restoreConfirm', { f: b.file }))
  if (!ok) return
  const r = await api('restore_backup', b.path)
  if (r.ok) message.success(t('settings.m.restored'))
  else message.error(t('settings.m.restoreFail', { e: r.error }))
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
    message.success(t('settings.m.modelDeleted', { m }))
    loadModels(); refresh()
    window.dispatchEvent(new CustomEvent('fb-refresh'))
  } else {
    message.error(t('settings.m.deleteFail', { e: r.error }))
  }
}

let imgOffs = []
imgOffs.push(on('img_desc_start', (p) => { imgDescProgress.value = { i: 0, n: p.pending } }))
imgOffs.push(on('img_desc_progress', (p) => { imgDescProgress.value = p }))
imgOffs.push(on('img_desc_done', (r) => {
  imgDescProgress.value = null
  message.success(t('settings.m.descDone', { a: r.described || 0, s: r.skipped || 0 }))
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
  message.info(t('settings.m.stopReq'))
}

const ciHasIndex = computed(() => !!ci.value?.status?.indexed?.docs)

async function ciReset() {
  if (!window.confirm(t('settings.m.ciClearConfirm'))) return
  const r = await api('content_index_reset')
  if (r && r.ok === false) { message.error(r.error || t('settings.m.clearFail')); return }
  message.success(t('settings.m.ciCleared'))
  loadContentIndex()
}

let ciOffs = []
ciOffs.push(on('content_index_progress', (p) => {
  if (!ci.value) ci.value = { status: {}, plan: null }
  ci.value.status = { ...ci.value.status, ...p, running: true }
}))
ciOffs.push(on('content_index_done', (s) => {
  if (s.budget_hit) message.warning(t('settings.m.budgetHit'))
  else message.success(t('settings.m.ciDone', { ok: s.ok || 0, f: s.failed || 0 }))
  loadContentIndex()
}))
onUnmounted(() => ciOffs.forEach((f) => f()))
loadContentIndex()

// ---------- 功能开关 ----------
const autoKb = ref(true)
const autostart = ref(false)
const autoStartOllama = ref(true)
const stopOllamaOnExit = ref(true)

async function loadToggles() {
  const st = await api('get_status')
  autoKb.value = st.auto_kb
  autoStartOllama.value = st.auto_start_ollama
  stopOllamaOnExit.value = st.stop_ollama_on_exit
  const as = await api('autostart_status')
  autostart.value = as.enabled
}

async function toggleAutoStartOllama(v) {
  autoStartOllama.value = v
  await api('set_auto_start_ollama', v)
  message.success(v ? t('settings.m.asoOn') : t('settings.m.asoOff'))
}

async function toggleStopOllamaOnExit(v) {
  stopOllamaOnExit.value = v
  await api('set_stop_ollama_on_exit', v)
  message.success(v ? t('settings.m.sooOn') : t('settings.m.sooOff'))
}

async function toggleAutoKb(v) {
  autoKb.value = v
  await api('set_auto_kb', v)
  message.success(v ? t('settings.m.autoKbOn') : t('settings.m.autoKbOff'))
}

async function toggleAutostart(v) {
  const r = await api('set_autostart', v)
  if (r.ok) {
    autostart.value = v
    message.success(v ? t('settings.m.autoStartOn') : t('settings.m.autoStartOff'))
  } else {
    message.error(t('settings.m.setFail', { e: r.error }))
  }
}

async function loadImgSem() {
  try { imgSem.value = await api('img_semantic_status') } catch (e) { /* 忽略 */ }
}

async function toggleImgSem(v) {
  const r = await api('set_img_semantic', v)
  if (!v) {
    message.info(t('settings.m.imgSemOff'))
    loadImgSem()
    return
  }
  if (r.model_ready) {
    message.success(t('settings.m.imgSemOn'))
  } else {
    message.warning(t('settings.m.imgSemNoModel', { m: r.vl_model }))
  }
  loadImgSem()
}

async function pullVl() {
  await api('pull_model', 'qwen2.5vl:7b')
  pullState.value = { model: 'qwen2.5vl:7b', pct: 0, status: t('settings.m.queued') }
}

// ---------- 自动扫描监控目录 ----------
const watchRoots = ref([])
const rescanning = ref(false)

const rootColumns = computed(() => [
  { title: t('settings.colDir'), key: 'path', ellipsis: { tooltip: true }, render: (r) => r.path },
  { title: t('settings.colLabel'), key: 'label', width: 100 },
  { title: t('settings.colIndexed'), key: 'n_files', width: 90 },
  { title: t('settings.colOps'), key: 'ops', width: 160, render: (r) => h(NSpace, { size: 'small' }, { default: () => [
      h(NButton, { size: 'tiny', onClick: () => api('open_path', r.path) }, { default: () => t('settings.open') }),
      h(NButton, { size: 'tiny', type: 'error', ghost: true, onClick: () => removeWatchRoot(r.id) },
        { default: () => t('settings.remove') }),
    ] }) },
])

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
  message.success(t('settings.m.watchAdded'))
  loadWatchRoots()
}

async function removeWatchRoot(id) {
  await api('remove_watch_root', id)
  message.success(t('settings.m.watchRemoved'))
  loadWatchRoots()
}

async function rescanNow() {
  rescanning.value = true
  await api('rescan_library')
  message.info(t('settings.m.scanStarted'))
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
  message.success(v ? t('settings.m.yearOn') : t('settings.m.yearOff'))
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
    message.success(v ? t('settings.m.hotkeyOn', { k: hotkey.value.hotkey }) : t('settings.m.hotkeyOff'))
  } else {
    message.error(t('settings.m.setFail', { e: r.error || '?' }))
  }
}

async function saveHotkeyCombo() {
  if (!hotkey.value.hotkey.trim()) return message.warning(t('settings.m.hotkeyNeed'))
  const r = await api('set_hotkey', hotkey.value.enabled, hotkey.value.hotkey.trim())
  if (r.ok) message.success(t('settings.m.hotkeyApplied', { k: hotkey.value.hotkey.trim() }))
  else message.error(t('settings.m.setFail', { e: r.error || '?' }))
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
  message.success(t('settings.m.excludeAdded'))
  loadExcludes()
}

async function removeExclude(id) {
  await api('remove_exclude', id)
  message.success(t('settings.m.excludeRemoved'))
  loadExcludes()
}

// ---------- 自动整理（可选项） ----------
const autoOrganize = ref({ enabled: false, mode: 'report', root: '' })
const organizeModeOptions = computed(() => [
  { label: t('settings.orgReport'), value: 'report' },
  { label: t('settings.orgMove'), value: 'move' },
])

async function loadAutoOrganize() {
  try { autoOrganize.value = await api('auto_organize_status') } catch (e) { /* 忽略 */ }
}

async function toggleAutoOrganize(v) {
  const r = await api('set_auto_organize', v, autoOrganize.value.mode, autoOrganize.value.root)
  if (r.ok) {
    autoOrganize.value.enabled = v
    message.success(v ? t('settings.m.autoOrgOn') : t('settings.m.autoOrgOff'))
  } else message.error(t('settings.m.setFailShort'))
}

async function changeOrganizeMode(mode) {
  await api('set_auto_organize', autoOrganize.value.enabled, mode, autoOrganize.value.root)
  autoOrganize.value.mode = mode
  message.success(mode === 'move' ? t('settings.m.orgModeMove') : t('settings.m.orgModeReport'))
}

async function pickOrganizeRoot() {
  const p = await api('pick_folder')
  if (p) {
    autoOrganize.value.root = p
    await api('set_auto_organize', autoOrganize.value.enabled, autoOrganize.value.mode, p)
    message.success(t('settings.m.orgRootSet'))
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
      message.info(updateInfo.value.error || t('settings.m.checkFail'))
    }
  } catch (e) { message.error(t('settings.m.checkFailE', { e })) }
  checkingUpdate.value = false
}

// 下载 + 静默安装：下载进度走事件，装完由后端拉起安装器并退出本进程
const downloadingUpdate = ref(false)
const updatePct = ref(0)
const updateReady = ref(null)   // { path, version }
const updateOffs = []
updateOffs.push(on('update_progress', (p) => { updatePct.value = p.pct || 0 }))
updateOffs.push(on('update_done', (p) => {
  downloadingUpdate.value = false
  updateReady.value = p
  message.success(p.rollback
    ? t('settings.m.updReadyRb', { v: p.version })
    : t('settings.m.updReady', { v: p.version }))
}))
updateOffs.push(on('update_error', (e) => {
  downloadingUpdate.value = false
  updatePct.value = 0
  message.error(String(e))
}))
onUnmounted(() => updateOffs.forEach((f) => f()))

async function downloadUpdate() {
  downloadingUpdate.value = true
  updatePct.value = 0
  try {
    const r = await api('download_update')
    if (!r.ok) { downloadingUpdate.value = false; message.error(r.error || t('settings.m.dlFailShort')) }
  } catch (e) { downloadingUpdate.value = false; message.error(t('settings.m.dlFail', { e })) }
}

async function installUpdate() {
  if (!updateReady.value) return
  try {
    const r = await api('install_update', updateReady.value.path)
    if (!r.ok) message.error(r.error || t('settings.m.installFail', { e: '' }))
    else message.info(t('settings.m.installing'))
  } catch (e) { message.error(t('settings.m.installFail', { e })) }
}

// 回退：新版装坏了退回上一个正式版（复用同一套下载 + 静默安装）
const rollbackLoading = ref(false)
async function startRollback() {
  rollbackLoading.value = true
  try {
    const target = await api('rollback_target')
    if (!target.ok) { message.warning(target.error || t('settings.m.noRollback')); return }
    if (!window.confirm(t('settings.m.rollbackConfirm', { t: target.target, c: target.current }))) return
    const r = await api('download_rollback')
    if (!r.ok) { message.error(r.error || t('settings.m.dlFailShort')); return }
    downloadingUpdate.value = true
    updatePct.value = 0
  } catch (e) { message.error(t('settings.m.rollbackFail', { e })) }
  finally { rollbackLoading.value = false }
}

// Rerank（检索结果精排，可选增强）：模型 ~544MB 按需下载，CPU 打分，默认关
const rerank = ref(null)
let rerankTimer = null
async function refreshRerank() {
  try {
    rerank.value = await api('rerank_status')
  } catch (e) { /* 忽略 */ }
}
async function toggleRerank(v) {
  rerank.value = await api('set_rerank_enabled', v)
  if (v && !rerank.value.active) message.warning(t('settings.m.rerankNotReady'))
}
async function rerankDownload() {
  rerank.value = await api('rerank_download')
  if (rerankTimer) clearInterval(rerankTimer)
  rerankTimer = setInterval(async () => {
    await refreshRerank()
    if (!rerank.value?.download?.downloading) {
      clearInterval(rerankTimer); rerankTimer = null
      if (rerank.value?.download?.error) message.error(t('settings.m.rerankDlFail', { e: rerank.value.download.error }))
      else if (rerank.value?.downloaded) message.success(t('settings.m.rerankReady'))
    }
  }, 1000)
}
async function rerankDelete() {
  await api('rerank_delete')
  message.success(t('settings.m.rerankDeleted'))
  refreshRerank()
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
  refreshRerank()
  api('recommend_model').then((r) => (recommend.value = r))
})
</script>

<template>
  <div class="fb-page">
    <n-space vertical size="large">
      <!-- ============ Ollama ============ -->
      <n-card :title="t('settings.ollamaTitle')">
        <n-space vertical size="large">
          <n-alert v-if="status && !status.ollama.running" type="error">
            {{ t('settings.ollamaMissing') }}
            <n-button style="margin-left:12px" type="primary" size="small" @click="installOllama">{{ t('settings.downloadInstall') }}</n-button>
          </n-alert>
          <n-alert v-else-if="status && (!status.ollama.chat_ready || !status.ollama.embed_ready)" type="warning">
            {{ t('settings.ollamaNoModel') }}
            <n-tag v-if="!status.ollama.chat_ready" size="small">{{ t('settings.tagChat') }}</n-tag>
            <n-tag v-if="!status.ollama.embed_ready" size="small">{{ t('settings.tagEmbed') }}</n-tag>
            <span style="margin-left:8px">{{ t('settings.clickDownload') }}</span>
          </n-alert>

          <n-space item-style="display:flex;align-items:center;gap:8px">
            <n-text style="width:80px">{{ t('settings.host') }}</n-text>
            <n-input v-model:value="host" style="width:320px" class="mono" placeholder="http://127.0.0.1:11434" />
          </n-space>
          <n-space item-style="display:flex;align-items:center;gap:8px">
            <n-text style="width:80px">{{ t('settings.chatModel') }}</n-text>
            <n-select v-model:value="chatModel" :options="chatModelOptions" style="width:320px"
              :placeholder="t('settings.pickInstalled')" tag filterable />
            <n-button type="primary" @click="saveOllama">{{ t('settings.save') }}</n-button>
          </n-space>
          <n-space item-style="display:flex;align-items:center;gap:8px">
            <n-text style="width:80px">{{ t('settings.keepAlive') }}</n-text>
            <n-select v-model:value="keepAlive" :options="keepAliveOptions" style="width:320px" />
            <n-switch v-model:value="preload" size="small">
              <template #suffix>{{ t('settings.preload') }}</template>
            </n-switch>
          </n-space>
          <n-space item-style="display:flex;align-items:center;gap:8px">
            <n-text style="width:80px">{{ t('settings.contextWin') }}</n-text>
            <n-select v-model:value="numCtx" :options="numCtxOptions" style="width:320px" />
          </n-space>
          <n-text depth="3" style="font-size:12px">
            {{ t('settings.keepHint') }}<br>
            {{ t('settings.ctxHint') }}
          </n-text>

          <n-descriptions v-if="recommend" :column="3" bordered size="small" label-placement="left">
            <n-descriptions-item :label="t('settings.ram')">{{ recommend.config.ram_gb }} GB</n-descriptions-item>
            <n-descriptions-item :label="t('settings.gpu')">
              {{ recommend.config.gpu
                ? (recommend.config.gpu_name || recommend.config.vram_gb + ' GB')
                : t('settings.noGpu') }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.recModel')">
              <b class="mono">{{ recommend.local.model }}</b>{{ t('settings.approxGB', { n: recommend.local.size_gb }) }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.recReason')" :span="3">
              {{ recommend.local.reason }} · {{ recommend.local.note }}
            </n-descriptions-item>
          </n-descriptions>

          <n-space>
            <n-card v-for="m in localModelCards" :key="m" size="small" style="width:280px"
              :title="m">
              <template #header-extra>
                <n-tag v-if="recommend && recommend.local.model === m" size="tiny" type="success"
                  :bordered="false">{{ t('settings.recTag') }}</n-tag>
                <n-tag v-if="status && status.ollama.models.includes(m)" type="success" size="small">{{ t('settings.installed') }}</n-tag>
                <n-button v-else size="small" type="primary" ghost @click="pull(m)">{{ t('settings.downloadN', { size: MODEL_SIZES[m] }) }}</n-button>
              </template>
            </n-card>
            <n-card size="small" style="width:280px" :title="t('settings.embedTitle')">
              <template #header-extra>
                <n-tag v-if="status && status.ollama.embed_ready" type="success" size="small">{{ t('settings.installed') }}</n-tag>
                <n-button v-else size="small" type="primary" ghost @click="pull('bge-m3')">{{ t('settings.embedDownload') }}</n-button>
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
      <n-card :title="t('settings.appearanceTitle')">
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
              <b>{{ t('theme.auto') }}</b>
              <span>{{ t('theme.autoDesc') }}</span>
              <n-tag v-if="themeKey === AUTO_KEY" size="tiny" round type="primary"
                style="margin-top:4px; width: fit-content;">{{ t('theme.current') }}</n-tag>
            </div>
          </div>
          <div v-for="th in themeList" :key="th.key" class="theme-card"
            :class="{ active: themeKey === th.key }" @click="pickTheme(th.key)">
            <div class="theme-preview" :style="{ background: th.naive.common.bodyColor }">
              <div class="tp-side" :style="{ background: th.vars['--fb-sidebar-bg'] }">
                <div class="tp-logo" :style="{ background: th.swatch }"></div>
                <div class="tp-menu"></div>
                <div class="tp-menu"></div>
                <div class="tp-menu"></div>
              </div>
              <div class="tp-main">
                <div class="tp-hero" :style="{ background: th.swatch }"></div>
                <div class="tp-line"></div>
                <div class="tp-line short"></div>
              </div>
            </div>
            <div class="theme-card-body">
              <b>{{ t('theme.' + th.key) }}</b>
              <span>{{ t('theme.' + th.key + 'Desc') }}</span>
              <n-tag v-if="themeKey === th.key" size="tiny" round type="primary"
                style="margin-top:4px; width: fit-content;">{{ t('theme.current') }}</n-tag>
            </div>
          </div>
        </n-space>
        <div style="border-top:1px solid var(--fb-border,#e8e8e8);margin:18px 0 14px"></div>
        <n-space align="center" :size="10">
          <n-text style="font-size:13px">{{ t('lang.label') }}</n-text>
          <n-select :value="lang" :options="LANGS.map(l => ({ label: l.label, value: l.key }))"
                    size="small" style="width:150px" @update:value="pickLang" />
          <n-text depth="3" style="font-size:12px">{{ t('lang.hint') }}</n-text>
        </n-space>
      </n-card>

      <!-- ============ 数据存储位置 ============ -->
      <n-card :title="t('settings.dataTitle')">
        <n-space vertical size="medium" v-if="dataDir">
          <div class="fb-kv">
            <span class="fb-kv-label">{{ t('settings.curPos') }}</span>
            <code class="fb-path" :title="dataDir.current">{{ dataDir.current }}</code>
            <n-tag size="small" round :type="dataDir.is_default ? 'default' : 'success'">
              {{ dataDir.is_default ? t('settings.tagDefault') : t('settings.tagCustom') }}
            </n-tag>
          </div>
          <n-alert v-if="dataDir.pointer_issue" type="error" :bordered="false">
            <div style="font-size:13px">
              {{ t('settings.pointerIssue', { p: dataDir.pointer_issue }) }}
            </div>
          </n-alert>
          <n-alert v-if="dataDir.alternatives && dataDir.alternatives.length"
            type="warning" :bordered="false">
            <div style="font-size:13px">
              {{ t('settings.discover') }}
            </div>
            <div v-for="a in dataDir.alternatives" :key="a.db_path"
              style="display:flex;align-items:center;gap:8px;margin-top:6px">
              <code class="fb-path" style="flex:1;min-width:0" :title="a.db_path">{{ a.dir }}</code>
              <n-text depth="3" style="font-size:12px;flex-shrink:0">
                {{ (a.size / 1e6).toFixed(0) }} MB
              </n-text>
              <n-button size="tiny" round type="primary" ghost @click="pendingDir = a.dir">
                {{ t('settings.switchHere') }}
              </n-button>
            </div>
          </n-alert>
          <n-text depth="3" style="font-size:12.5px">
            {{ t('settings.dbTotal', { size: (dataDir.total_size / 1e6).toFixed(1) }) }}
            {{ t('settings.switchHint') }}
          </n-text>
          <n-space>
            <n-button size="small" round @click="api('open_data_dir')">{{ t('settings.openDataDir') }}</n-button>
            <n-button size="small" round type="primary" ghost @click="pickNewDataDir">{{ t('settings.changeDir') }}</n-button>
            <n-button v-if="!dataDir.is_default || dataDir.pointer_issue"
              size="small" round @click="doResetDir">{{ t('settings.restoreDefault') }}</n-button>
          </n-space>
          <div v-if="pendingDir" class="fb-pending">
            <n-text style="font-size:13px">{{ t('settings.moveTo') }}</n-text>
            <code class="fb-path" style="border-style: solid;">{{ pendingDir }}</code>
            <n-button size="small" round type="primary" :loading="switching" @click="doSwitch(false)">{{ t('settings.startMove') }}</n-button>
            <n-button size="small" round quaternary @click="pendingDir = null">{{ t('settings.cancel') }}</n-button>
            <n-radio-group v-model:value="moveMode" size="small" style="margin-left:8px">
              <n-radio value="copy">{{ t('settings.copyRadio') }}</n-radio>
              <n-radio value="move">{{ t('settings.moveRadio') }}</n-radio>
            </n-radio-group>
          </div>
          <n-alert v-if="restartPending" type="warning" :bordered="false">
            {{ t('settings.switched') }}
            <n-button size="small" type="primary" style="margin-left:10px" @click="restartNow">{{ t('settings.restartNow') }}</n-button>
          </n-alert>
        </n-space>
      </n-card>

      <!-- ============ 数据库维护 ============ -->
      <n-card :title="t('settings.dbTitle')">
        <n-space vertical size="medium">
          <div class="fb-kv">
            <span class="fb-kv-label">{{ t('settings.dbSize') }}</span>
            <n-tag size="small" round>{{ fmtMB(dbMaint.db_size) }}</n-tag>
            <n-tag v-if="dbMaint.wal_size > 10 * 1e6" size="small" round type="warning">
              {{ t('settings.wal', { size: fmtMB(dbMaint.wal_size) }) }}
            </n-tag>
            <n-tag size="small" round depth="2">{{ t('settings.backups', { n: dbMaint.backups }) }}</n-tag>
          </div>
          <n-text depth="3" style="font-size:12.5px">
            {{ t('settings.vacHint') }}
          </n-text>
          <n-space>
            <n-button size="small" round type="primary" ghost :loading="dbMaint.vacuum_running"
              :disabled="dbMaint.vacuum_running" @click="doVacuum">
              {{ dbMaint.vacuum_running ? t('settings.vacuuming') : t('settings.vacuum') }}
            </n-button>
            <n-button size="small" round @click="loadDbMaint">{{ t('settings.refresh') }}</n-button>
          </n-space>
        </n-space>
      </n-card>

      <!-- ============ 模型接入 ============ -->
      <n-card :title="t('settings.modeTitle')">
        <n-space vertical size="medium">
          <n-radio-group :value="provMode" @update:value="(v) => (provMode = v)">
            <n-radio-button value="ollama">{{ t('settings.modeLocal') }}</n-radio-button>
            <n-radio-button value="api">{{ t('settings.modeApi') }}</n-radio-button>
          </n-radio-group>

          <template v-if="provMode === 'api'">
            <n-space :size="8" :wrap="true">
              <n-tag v-for="p in provPresets" :key="p.value" size="small" round
                style="cursor:pointer" :type="provBase === p.value ? 'primary' : 'default'"
                @click="pickPreset(p)">{{ p.label }}</n-tag>
            </n-space>
            <div class="fb-kv">
              <span class="fb-kv-label">{{ t('settings.base') }}</span>
              <n-input v-model:value="provBase" :placeholder="t('settings.basePh')"
                style="flex:1;min-width:280px" class="mono" />
            </div>
            <div class="fb-kv">
              <span class="fb-kv-label">{{ t('settings.apiKey') }}</span>
              <n-input ref="keyInputRef" :value="keyDisplay"
                @update:value="onKeyInput"
                @focus="onKeyFocus" @blur="onKeyBlur"
                :type="keyRevealed ? 'text' : 'password'"
                :placeholder="provKeyMask ? t('settings.keyPhNew') : t('settings.keyPhEmpty')"
                style="flex:1;min-width:280px" class="mono">
                <template #suffix>
                  <n-button quaternary circle size="tiny" tabindex="-1"
                    @click="toggleKeyReveal" :title="keyRevealed ? t('settings.hideKey') : t('settings.showKey')">
                    <n-icon :size="16" :depth="3">
                      <component :is="keyRevealed ? EyeOffOutline : EyeOutline" />
                    </n-icon>
                  </n-button>
                </template>
              </n-input>
            </div>
            <div class="fb-kv">
              <span class="fb-kv-label">{{ t('settings.chatModel') }}</span>
              <n-select v-if="provModels.length" v-model:value="provChatModel"
                :options="provModels.map(m => ({ label: m, value: m }))" tag filterable
                :placeholder="t('settings.pickFromServer')" style="flex:1;min-width:280px" class="mono" />
              <n-input v-else v-model:value="provChatModel" :placeholder="t('settings.modelPh')"
                style="flex:1;min-width:280px" class="mono" />
              <n-button size="small" round :loading="provFetching" @click="fetchProvModels">{{ t('settings.fetchModels') }}</n-button>
            </div>
            <div class="fb-kv">
              <span class="fb-kv-label">{{ t('settings.embed') }}</span>
              <n-select v-model:value="provEmbedSource" :options="embedSourceOptions"
                style="width:220px" />
              <n-select v-if="provEmbedSource === 'api' && provModels.length"
                v-model:value="provEmbedModel" :options="provModels.map(m => ({ label: m, value: m }))"
                tag filterable :placeholder="t('settings.vecModelPh')" style="flex:1;min-width:240px" class="mono" />
              <n-input v-else-if="provEmbedSource === 'api'" v-model:value="provEmbedModel"
                :placeholder="t('settings.vecModelPh2')" style="flex:1;min-width:240px" class="mono" />
            </div>
            <n-text depth="3" style="font-size:12px">
              {{ t('settings.provHint') }}
            </n-text>
          </template>

          <n-space>
            <n-button type="primary" size="small" round :loading="provSaving" @click="saveProvider">{{ t('settings.saveProvider') }}</n-button>
            <n-button size="small" round :loading="provTesting" @click="testProvider">{{ t('settings.testConn') }}</n-button>
            <n-button size="small" round :loading="provDetecting" @click="detectLocal">{{ t('settings.detectLocal') }}</n-button>
            <n-tag v-if="provider" size="small" round
              :type="provider.summary.chat.ok ? 'success' : 'error'">
              {{ t('settings.chatReason', { v: provider.summary.chat.reason }) }}
            </n-tag>
            <n-tag v-if="provider" size="small" round
              :type="provider.summary.embed.ok ? 'success' : 'warning'">
              {{ t('settings.embedReason', { v: provider.summary.embed.reason }) }}
            </n-tag>
          </n-space>
          <n-alert v-if="provTestResult" :bordered="false"
            :type="provTestResult.ok ? 'success' : 'error'">
            <template v-if="provTestResult.ok">
              {{ t('settings.testOk', { ms: provTestResult.latency_ms, reply: provTestResult.reply }) }}
            </template>
            <template v-else><span style="white-space:pre-line">✗ {{ provTestResult.error }}</span></template>
          </n-alert>
        </n-space>
      </n-card>

      <!-- ============ 备份管理 ============ -->
      <n-card :title="t('settings.backupTitle')">
        <n-space vertical size="large">
          <n-space>
            <n-button type="primary" ghost size="small" :loading="backingUp" @click="backupNow">{{ t('settings.backupNow') }}</n-button>
            <n-text depth="3" style="font-size:12px;align-self:center">
              {{ t('settings.backupAuto', { keep: dbMaint.backup_keep || 5, gb: ((dbMaint.backup_max_total || 0) / 1e9).toFixed(1) }) }}
              <template v-if="dbMaint.backups">
                {{ t('settings.backupStat', { n: dbMaint.backups, size: fmtMB(dbMaint.backups_size || 0) }) }}
              </template>
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
                <n-button size="tiny" type="warning" ghost @click="restoreBk(b)">{{ t('settings.restoreBk') }}</n-button>
              </n-space>
            </n-list-item>
          </n-list>
          <n-text v-else depth="3">{{ t('settings.noBackup') }}</n-text>
        </n-space>
      </n-card>

      <!-- ============ 配置导出 / 导入 ============ -->
      <n-card :title="t('settings.cfgTitle')">
        <n-space vertical size="medium">
          <n-space align="center">
            <n-button size="small" round type="primary" ghost @click="exportConfig">{{ t('settings.exportCfg') }}</n-button>
            <n-button size="small" round @click="triggerImport">{{ t('settings.importCfg') }}</n-button>
            <input ref="importFileEl" type="file" accept=".json,application/json"
              style="display:none" @change="onImportFile" />
          </n-space>
          <n-text depth="3" style="font-size:12px">
            {{ t('settings.cfgHint') }}
          </n-text>
        </n-space>
      </n-card>

      <!-- ============ 模型管理 ============ -->
      <n-card :title="t('settings.modelsTitle')">
        <n-space vertical size="large">
          <n-list bordered v-if="modelsDetail.length">
            <n-list-item v-for="m in modelsDetail" :key="m.name">
              <n-space justify="space-between" align="center" style="width:100%">
                <n-space align="center">
                  <n-text class="mono" strong>{{ m.name }}</n-text>
                  <n-tag size="small" type="success" v-if="m.is_chat">{{ t('settings.tagCurrent') }}</n-tag>
                  <n-tag size="small" type="info" v-if="m.is_embed">{{ t('settings.tagEmbedM') }}</n-tag>
                  <n-tag size="small" type="warning" v-if="m.is_vl">{{ t('settings.tagVl') }}</n-tag>
                  <n-text depth="3">{{ (m.size / 1e9).toFixed(1) }} GB</n-text>
                </n-space>
                <n-popconfirm @positive-click="deleteModel(m.name)">
                  <template #trigger>
                    <n-button size="small" type="error" ghost :loading="deleting">{{ t('settings.del') }}</n-button>
                  </template>
                  {{ t('settings.delConfirm', { name: m.name, size: (m.size / 1e9).toFixed(1) }) }}
                  {{ t('settings.delWarn') }}
                  <template v-if="m.is_chat || m.is_embed"><br/><b>{{ t('settings.delWarn2') }}</b></template>
                </n-popconfirm>
              </n-space>
            </n-list-item>
          </n-list>
          <n-text v-else depth="3">{{ t('settings.noModel') }}</n-text>
        </n-space>
      </n-card>

      <!-- ============ 全盘内容索引 ============ -->
      <n-card :title="t('settings.ciTitle')">
        <n-space vertical size="small">
          <n-text depth="3" style="font-size:12.5px">
            {{ t('settings.ciHint') }}
          </n-text>
          <n-space size="small" align="center" style="margin-top:4px">
            <n-button size="small" type="primary" ghost :disabled="ciRunning" :loading="ciBusy"
              @click="ciStart">{{ ciRunning ? t('settings.ciRunning') : t('settings.ciStart') }}</n-button>
            <n-button size="small" :disabled="!ciRunning" @click="ciStop">{{ t('settings.ciStop') }}</n-button>
            <n-button size="small" quaternary type="error" :disabled="ciRunning || !ciHasIndex"
              @click="ciReset">{{ t('settings.ciReset') }}</n-button>
            <n-button size="small" quaternary @click="loadContentIndex">{{ t('settings.refresh') }}</n-button>
          </n-space>
          <n-progress v-if="ciRunning && ci?.status?.total" type="line" :percentage="Math.min(99.5, Math.round((ci.status.done || 0) / ci.status.total * 100))"
            indicator-placement="inside" processing />
          <n-text depth="3" style="font-size:12px">
            {{ t('settings.ciStat', { ok: ci?.status?.indexed?.ok ?? 0 }) }}
            {{ t('settings.ciBody', { b: fmtBody(ci?.status?.indexed?.bytes ?? 0) }) }}
            {{ t('settings.ciFail', { f: ci?.status?.indexed?.failed ?? 0 }) }}
          </n-text>
          <n-text v-if="ci?.plan" depth="3" style="font-size:12px">
            {{ t('settings.ciPlan', { c: ci.plan.candidates, w: ci.plan.will_index }) }}
            {{ t('settings.ciBudget', { d: ci.plan.budget.max_docs, b: (ci.plan.budget.max_body_bytes / 1e9).toFixed(1) }) }}
          </n-text>
          <n-text depth="3" style="font-size:12px">
            {{ t('settings.ciHint2') }}
          </n-text>
          <n-text v-if="ci?.status?.budget_hit || (ci?.plan && ci.plan.budget.docs_left === 0)"
            type="warning" style="font-size:12px">
            {{ t('settings.ciBudgetHit') }}
          </n-text>
        </n-space>
      </n-card>

      <!-- ============ Rerank 检索精排 ============ -->
      <n-card :title="t('settings.rerankTitle')">
        <n-space vertical size="small">
          <n-text depth="3" style="font-size:12.5px">
            {{ t('settings.rerankHint') }}
          </n-text>
          <n-space size="small" align="center" style="margin-top:4px">
            <n-switch
              :value="rerank?.enabled || false"
              :disabled="!rerank?.runtime || !rerank?.downloaded"
              @update:value="toggleRerank" />
            <n-text style="font-size:12.5px">
              {{ !rerank?.runtime ? t('settings.rkNoRuntime')
                  : !rerank?.downloaded ? t('settings.rkNoModel')
                  : rerank.enabled ? t('settings.rkOn') : t('settings.rkOff') }}
            </n-text>
            <template v-if="rerank?.runtime && !rerank?.downloaded">
              <n-button size="small" type="primary" ghost :loading="rerank?.download?.downloading"
                @click="rerankDownload">
                {{ rerank?.download?.downloading ? t('settings.rkDownloading') : t('settings.rkDownload') }}
              </n-button>
              <n-text depth="3" style="font-size:12px">
                {{ rerank?.download?.downloading
                    ? (rerank.download.pct + '% · ' + ((rerank.download.done_bytes || 0) / 1048576).toFixed(0) + ' MB')
                    : t('settings.rkSize') }}
              </n-text>
            </template>
            <template v-if="rerank?.downloaded">
              <n-popconfirm @positive-click="rerankDelete">
                <template #trigger>
                  <n-button size="small" quaternary type="error">{{ t('settings.rkDel') }}</n-button>
                </template>
                {{ t('settings.rkDelHint') }}
              </n-popconfirm>
              <n-text depth="3" style="font-size:12px">
                {{ t('settings.rkReady', { size: ((rerank.size_bytes || 0) / 1048576).toFixed(0) }) }}
              </n-text>
            </template>
          </n-space>
          <n-text v-if="rerank?.failed" type="warning" style="font-size:12px">
            {{ t('settings.rkFail', { e: rerank.error }) }}
          </n-text>
        </n-space>
      </n-card>

      <!-- ============ 自动化开关 ============ -->
      <n-card :title="t('settings.autoTitle')">
        <n-space vertical size="large">
          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="autoStartOllama" @update:value="toggleAutoStartOllama" />
            <div>
              <n-text strong>{{ t('settings.autoStartOllama') }}</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                {{ t('settings.autoStartOllamaHint') }}
              </n-text>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="stopOllamaOnExit" @update:value="toggleStopOllamaOnExit" />
            <div>
              <n-text strong>{{ t('settings.stopOllamaOnExit') }}</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                {{ t('settings.stopOllamaOnExitHint') }}
              </n-text>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="autoKb" @update:value="toggleAutoKb" />
            <div>
              <n-text strong>{{ t('settings.autoKb') }}</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                {{ t('settings.autoKbHint') }}
              </n-text>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="!!imgSem?.enabled" @update:value="toggleImgSem" />
            <div>
              <n-text strong>{{ t('settings.imgSem') }}</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                {{ t('settings.imgSemHint') }}
              </n-text>
              <n-text v-if="winOcrOk" depth="3" style="display:block;font-size:12px">
                {{ t('settings.ocrOk', { langs: winOcrLangs }) }}
              </n-text>
              <n-text v-else depth="3" style="display:block;font-size:12px">
                {{ t('settings.ocrFail') }}
              </n-text>
              <n-space v-if="imgSem" style="margin-top:4px" size="small">
                <n-tag size="small" :type="imgSem.model_ready ? 'success' : 'warning'">
                  {{ imgSem.model_ready ? t('settings.imgReady', { m: imgSem.vl_model }) : t('settings.imgNotReady', { m: imgSem.vl_model }) }}
                </n-tag>
                <n-tag v-if="imgSem.described" size="small">{{ t('settings.imgDescribed', { n: imgSem.described }) }}</n-tag>
                <n-tag v-if="imgSem.pending" size="small">{{ t('settings.imgPending', { n: imgSem.pending }) }}</n-tag>
                <n-button v-if="!imgSem.model_ready" size="tiny" type="primary" ghost @click="pullVl">
                  {{ t('settings.imgDownload') }}
                </n-button>
                <n-button v-else-if="imgSem.pending" size="tiny" @click="api('describe_backlog')">
                  {{ t('settings.imgBackfill') }}
                </n-button>
              </n-space>
              <n-progress v-if="imgDescProgress"
                :percentage="imgDescProgress.n ? Math.round(imgDescProgress.i / imgDescProgress.n * 100) : 0"
                style="margin-top:6px" processing>
                {{ t('settings.imgProgress', { i: imgDescProgress.i, n: imgDescProgress.n, name: imgDescProgress.name }) }}
              </n-progress>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="autostart" @update:value="toggleAutostart" />
            <div>
              <n-text strong>{{ t('settings.autostart') }}</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                {{ t('settings.autostartHint') }}
              </n-text>
            </div>
          </n-space>

          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="autoOrganize.enabled" @update:value="toggleAutoOrganize" />
            <div style="flex:1">
              <n-text strong>{{ t('settings.autoOrg') }}</n-text>
              <n-text depth="3" style="display:block;font-size:12px">
                {{ t('settings.autoOrgHint') }}
              </n-text>
              <n-space v-if="autoOrganize.enabled" style="margin-top:6px" size="small" align="center">
                <n-select :value="autoOrganize.mode" :options="organizeModeOptions" size="small"
                  style="width:230px" @update:value="changeOrganizeMode" />
                <n-input :value="autoOrganize.root" :placeholder="t('settings.autoOrgPh')" readonly
                  style="width:320px" class="mono">
                  <template #suffix>
                    <n-button size="tiny" @click="pickOrganizeRoot">{{ t('settings.browse2') }}</n-button>
                  </template>
                </n-input>
              </n-space>
            </div>
          </n-space>
        </n-space>
      </n-card>

      <!-- ============ 自动扫描监控目录 ============ -->
      <n-card :title="t('settings.watchTitle')">
        <n-space vertical size="large">
          <n-space>
            <n-button type="primary" @click="addWatchRoot">{{ t('settings.watchAdd') }}</n-button>
            <n-button :loading="rescanning" @click="rescanNow">{{ t('settings.rescan') }}</n-button>
            <n-text depth="3">{{ t('settings.watchHint') }}</n-text>
          </n-space>
          <n-data-table v-if="watchRoots.length" :columns="rootColumns" :data="watchRoots" size="small" />
        </n-space>
      </n-card>

      <!-- ============ 排除目录 ============ -->
      <n-card :title="t('settings.exclTitle')">
        <n-space vertical size="large">
          <n-space>
            <n-button type="primary" @click="addExclude">{{ t('settings.exclAdd') }}</n-button>
            <n-text depth="3">{{ t('settings.exclHint') }}</n-text>
          </n-space>
          <n-list v-if="excludes.length" bordered size="small">
            <n-list-item v-for="r in excludes" :key="r.id">
              <n-space justify="space-between" style="width:100%" align="center">
                <n-space>
                  <n-text class="mono" style="font-size:12.5px">{{ r.path }}</n-text>
                  <n-text depth="3" style="font-size:12px">{{ new Date(r.added_at * 1000).toLocaleString() }}</n-text>
                </n-space>
                <n-button size="tiny" type="error" ghost @click="removeExclude(r.id)">{{ t('settings.exclRemove') }}</n-button>
              </n-space>
            </n-list-item>
          </n-list>
          <n-text v-else depth="3">{{ t('settings.exclEmpty') }}</n-text>
        </n-space>
      </n-card>

      <!-- ============ 分类规则 ============ -->
      <n-card :title="t('settings.rulesTitle')">
        <n-space vertical size="large">
          <n-space>
            <n-input v-model:value="filterExt" :placeholder="t('settings.rulePh')" style="width:240px" @input="applyFilter" />
            <n-button type="primary" @click="showAdd = true">{{ t('settings.ruleAdd') }}</n-button>
            <n-text depth="3">{{ t('settings.ruleCount', { n: rules.rules.length }) }}</n-text>
          </n-space>
          <n-data-table :columns="ruleColumns" :data="filteredRules" :max-height="360" size="small"
            :row-key="(r) => r.pattern" virtual-scroll />
        </n-space>
      </n-card>

      <!-- ============ 其他 ============ -->
      <n-card :title="t('settings.optTitle')">
        <n-space vertical>
          <n-space item-style="display:flex;align-items:center;gap:10px">
            <n-switch :value="imagesByYear" @update:value="toggleImagesByYear" />
            <n-text>{{ t('settings.imgYear') }}</n-text>
          </n-space>
        </n-space>
      </n-card>

      <!-- ============ 关于 ============ -->
      <n-card :title="t('settings.aboutTitle')">
        <n-space vertical>
          <n-space align="center">
            <n-text strong style="font-size:14px">{{ t('settings.aboutName') }}</n-text>
            <n-tag v-if="about" size="small" round type="info">v{{ about.version }}</n-tag>
            <n-button v-if="!checkingUpdate" size="tiny" secondary @click="checkUpdate">
              {{ t('settings.checkUpdate') }}
            </n-button>
            <n-button v-else size="tiny" secondary loading>{{ t('settings.checking') }}</n-button>
          </n-space>
          <n-alert v-if="updateInfo" :type="updateInfo.outdated ? 'warning' : 'success'" :bordered="false">
            <template #header>
              {{ updateInfo.outdated ? t('settings.updNew', { latest: updateInfo.latest, cur: updateInfo.current })
                 : updateInfo.latest ? t('settings.updLatest', { cur: updateInfo.current }) : updateInfo.error }}
            </template>
            <template #default>
              <n-space v-if="updateInfo.outdated && updateInfo.download && !updateReady"
                       align="center" size="small">
                <n-button size="tiny" type="primary" :loading="downloadingUpdate"
                          @click="downloadUpdate">
                  {{ downloadingUpdate ? t('settings.dlPct', { p: updatePct }) : t('settings.dlUpdate') }}
                </n-button>
                <n-button size="tiny" quaternary @click="api('open_url', updateInfo.download)">
                  {{ t('settings.manual') }}
                </n-button>
              </n-space>
              <n-progress v-if="downloadingUpdate" :percentage="updatePct" :height="6"
                          :show-indicator="false" style="margin-top:8px" />
              <div v-if="updateInfo.notes" style="margin-top:6px">{{ updateInfo.notes }}</div>
            </template>
          </n-alert>
          <n-alert v-if="updateReady" type="success" :bordered="false">
            <n-space align="center" size="small">
              <n-text>{{ t('settings.ready', { v: updateReady.version }) }}</n-text>
              <n-button size="tiny" type="primary" @click="installUpdate">{{ t('settings.restartInstall') }}</n-button>
            </n-space>
          </n-alert>
          <n-space v-if="!updateReady" align="center" size="small">
            <n-button size="tiny" quaternary :loading="rollbackLoading" @click="startRollback">
              {{ t('settings.rollback') }}
            </n-button>
            <n-text depth="3" style="font-size:12px">{{ t('settings.rollbackHint') }}</n-text>
          </n-space>
          <n-text depth="3" style="font-size:12.5px">
            {{ t('settings.aboutDesc') }}
          </n-text>
        </n-space>
      </n-card>
    </n-space>

    <n-modal v-model:show="showAdd" preset="card" :title="t('settings.addRuleTitle')" style="width:420px">
      <n-space vertical>
        <n-input v-model:value="newRule.pattern" :placeholder="t('settings.ruleExtPh')" />
        <n-select v-model:value="newRule.category" :options="rules.categories.map(c => ({ label: c, value: c }))" />
        <n-input v-model:value="newRule.sub" :placeholder="t('settings.ruleSubPh')" />
        <n-button type="primary" @click="addRule">{{ t('settings.add') }}</n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="pendingOverwrite" preset="dialog" type="warning"
      :title="t('settings.dbExistsTitle')" :positive-text="t('settings.dbExistsOk')" :negative-text="t('settings.cancel')"
      @positive-click="confirmOverwrite" @negative-click="pendingOverwrite = false">
      {{ t('settings.dbExistsBody', { p: pendingDir }) }}
    </n-modal>
  </div>
</template>
