<script setup>
import { ref, computed, h, onUnmounted } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NText, NTag, NDataTable, NProgress,
  NTabs, NTabPane, NSelect, NModal, NList, NListItem, NDescriptions, NDescriptionsItem,
  NCheckbox, useMessage, NEmpty, NAlert, NSpin,
} from 'naive-ui'
import { api, on } from '../lib/bridge'
import { catLabel } from '../lib/i18n'
import { useI18n } from 'vue-i18n'

const message = useMessage()
const { t } = useI18n()

// ---------- 状态 ----------
const tab = ref('organize')
const rootPath = ref('')
const scanning = ref(false)
const scanInfo = ref(null)          // {root, files, ambiguous}
const aiRunning = ref(false)
const aiProgress = ref(null)
const targetRoot = ref('')
const planData = ref(null)          // {plan, summary}
const checkedKeys = ref([])
const executing = ref(false)
const dedupe = ref(null)
const dedupeRunning = ref(false)
const history = ref([])
const batchOps = ref(null)
const showBatchOps = ref(false)

const CATEGORIES = ['文档', '图片', '视频', '音频', '压缩包', '安装包', '代码', '数据', '字体', '其他']
const categoryOptions = computed(() => CATEGORIES.map((c) => ({ label: catLabel(c), value: c })))

let offs = []
offs.push(on('ai_classify_start', (p) => { aiRunning.value = true }))
offs.push(on('ai_classify_progress', (p) => { aiProgress.value = p }))
offs.push(on('ai_classify_done', (p) => {
  // 后端回传补全 ai_category 的 files
  scanInfo.value.files = p.files
  aiRunning.value = false
  aiProgress.value = null
  message.success(t('org.aiDone'))
}))
offs.push(on('ai_classify_error', (e) => { aiRunning.value = false; message.error(String(e)) }))
onUnmounted(() => offs.forEach((f) => f()))

// ---------- 扫描 ----------
async function pickFolder() {
  const p = await api('pick_folder')
  if (p) rootPath.value = p
}

async function pickFolderThenKeep() {
  const p = await api('pick_folder')
  if (p) targetRoot.value = p
}

async function scan() {
  if (!rootPath.value) return message.warning(t('org.msgNeedFolder'))
  scanning.value = true
  scanInfo.value = null
  planData.value = null
  try {
    scanInfo.value = await api('scan_folder', rootPath.value)
    targetRoot.value = rootPath.value
    if (scanInfo.value.files.length === 0) message.info(t('org.msgNoFiles'))
  } catch (e) {
    message.error(t('org.msgScanFail', { e }))
  }
  scanning.value = false
}

const ambiguousFiles = computed(() =>
  (scanInfo.value?.files || []).filter((f) => !f.rule_hit))

const scanColumns = computed(() => [
  { title: t('org.colName'), key: 'name', ellipsis: { tooltip: (r) => r.ai_reason || r.name } },
  { title: t('org.colCategory'), key: 'category', width: 110, render: (r) => h(NTag, {
      size: 'small', bordered: false,
      type: r.ai_category ? 'success' : (r.rule_hit ? 'default' : 'warning'),
    }, { default: () => catLabel(r.final_category || r.category) }) },
  { title: t('org.colExt'), key: 'ext', width: 90, render: (r) => h('span', { class: 'mono' }, r.ext || '-') },
  { title: t('org.colSize'), key: 'size', width: 100, render: (r) => fmtSize(r.size) },
])

async function runAiClassify() {
  if (ambiguousFiles.value.length === 0) return message.info(t('org.msgNoAmbiguous'))
  const r = await api('ai_classify', JSON.parse(JSON.stringify(ambiguousFiles.value)))
  if (r.started) aiRunning.value = true
}

// ---------- 计划预览 ----------
const overrides = ref({})  // path -> category

const previewFiles = computed(() => {
  const files = scanInfo.value?.files || []
  return files.map((f) => {
    const cat = overrides.value[f.path] || f.ai_category || f.category
    return { ...f, final_category: cat }
  })
})

async function buildPlan() {
  if (!scanInfo.value) return
  const map = {}
  for (const f of previewFiles.value) {
    if (f.final_category !== (f.ai_category || f.category)) map[f.path] = [f.final_category, '']
  }
  executing.value = true
  try {
    const imagesByYear = (await api('get_status')).images_by_year === '1'
    planData.value = await api('build_plan',
      JSON.parse(JSON.stringify(previewFiles.value)), targetRoot.value,
      Object.keys(map).length ? map : null, imagesByYear)
    checkedKeys.value = planData.value.plan
      .filter((r) => r.action === 'move')
      .map((r) => r.src)
    const n = checkedKeys.value.length
    if (n === 0) message.info(t('org.msgAllInPlace'))
  } catch (e) {
    message.error(t('org.msgPlanFail', { e }))
  }
  executing.value = false
}

const planColumns = computed(() => [
  { type: 'selection' },
  { title: t('org.colSrc'), key: 'src', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.src) },
  { title: t('org.colDst'), key: 'dst', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.dst || r.src) },
  { title: t('org.colCategory'), key: 'category', width: 110, render: (r) => h(NTag, { size: 'small', bordered: false }, { default: () => catLabel(r.category) }) },
])

const planRows = computed(() => planData.value?.plan || [])

async function execute() {
  if (checkedKeys.value.length === 0) return message.warning(t('org.msgNeedCheck'))
  executing.value = true
  try {
    const rows = planRows.value.filter((r) => checkedKeys.value.includes(r.src))
    const result = await api('execute_plan', JSON.parse(JSON.stringify(rows)))
    const ok = result.results.filter((r) => r.ok).length
    const fail = result.results.length - ok
    if (fail === 0) message.success(t('org.msgDoneMove', { ok }))
    else message.warning(t('org.msgDonePartial', { ok, fail }))
    loadHistory()
    scan()  // 重新扫描反映现状
  } catch (e) {
    message.error(t('org.msgExecFail', { e }))
  }
  executing.value = false
}

// ---------- 重复检测 ----------
async function runDedupe() {
  if (!scanInfo.value) return message.warning(t('org.msgNeedScan'))
  dedupeRunning.value = true
  dedupe.value = null
  try {
    dedupe.value = await api('find_duplicates', JSON.parse(JSON.stringify(scanInfo.value.files)))
    if (dedupe.value.groups.length === 0) message.info(t('org.msgNoDup'))
  } catch (e) {
    message.error(t('org.msgDetectFail', { e }))
  }
  dedupeRunning.value = false
}

const dedupeColumns = computed(() => [
  { title: t('org.colHash'), key: 'hash', width: 130, render: (r) => h('span', { class: 'mono' }, r.hash) },
  { title: t('org.colSize'), key: 'size', width: 90, render: (r) => fmtSize(r.size) },
  { title: t('org.colPaths'), key: 'paths', render: (r) =>
    h('div', null, r.paths.map((p, i) => h('div', {
      class: 'mono', style: 'cursor:pointer;color:' + (i === 0 ? '#18a058' : '#666') + ';padding:1px 0',
      onClick: () => api('open_path', p),
      title: p,
    }, (i === 0 ? t('org.keepSuggest') : t('org.dupMark')) + p))) },
])

// ---------- 重复清理 ----------
const selectedDupGroups = ref([])
const keepRule = ref('newest')
const keepRuleOptions = computed(() => [
  { label: t('org.keepNewest'), value: 'newest' },
  { label: t('org.keepOldest'), value: 'oldest' },
  { label: t('org.keepShortest'), value: 'shortest' },
])
const cleaning = ref(false)

const dedupeColumnsWithSelection = computed(() => [{ type: 'selection' }, ...dedupeColumns.value])

async function cleanupDuplicates() {
  const groups = dedupe.value.groups.filter((g) => selectedDupGroups.value.includes(g.hash))
  if (!groups.length) return message.warning(t('org.msgNeedDupGroups'))
  const toMove = groups.reduce((n, g) => n + g.paths.length - 1, 0)
  const ok = await new Promise((resolve) => {
    // 简易确认：用 window.confirm 足够，避免再引弹窗组件
    resolve(window.confirm(t('org.confirmDup', { n: toMove })))
  })
  if (!ok) return
  cleaning.value = true
  try {
    const r = await api('dedupe_cleanup', JSON.parse(JSON.stringify(groups)), keepRule.value, false)
    if (r.failed === 0) {
      message.success(t('org.msgMovedClean', { moved: r.moved, kept: r.kept, id: r.batch_id }))
    } else {
      message.warning(t('org.msgMovePartial', { moved: r.moved, failed: r.failed }))
    }
    loadHistory()
    scan()
  } catch (e) { message.error(String(e)) }
  cleaning.value = false
}

// ---------- 相似图片（dHash 近似重复） ----------
const similar = ref(null)
const similarRunning = ref(false)
const selectedSimGroups = ref([])
const simThumbBase = ref(null)

async function runSimilar() {
  if (!scanInfo.value) return message.warning(t('org.msgNeedScan'))
  similarRunning.value = true
  similar.value = null
  selectedSimGroups.value = []
  try {
    const st = await api('get_status')
    simThumbBase.value = st.thumb_base
    similar.value = await api('find_similar_images',
      JSON.parse(JSON.stringify(scanInfo.value.files)))
    if (similar.value.groups.length === 0) message.info(t('org.msgNoSimilar'))
  } catch (e) {
    message.error(t('org.msgDetectFail', { e }))
  }
  similarRunning.value = false
}

const simColumns = computed(() => [
  { title: t('org.colSimPaths'), key: 'paths', render: (r) =>
    h('div', null, r.paths.map((p, i) => h('div', {
      style: 'display:flex;align-items:center;gap:10px;padding:2px 0;cursor:pointer',
      onClick: () => api('open_path', p),
      title: p,
    }, [
      h('img', {
        src: simThumbBase.value ? simThumbBase.value + '/thumb?p=' + encodeURIComponent(p) : '',
        style: 'width:64px;height:46px;object-fit:cover;border-radius:4px;background:rgba(127,127,127,.15);flex:none',
        alt: '',
      }),
      h('span', {
        class: 'mono', style: 'font-size:12px;color:' + (i === 0 ? '#18a058' : '#888'),
      }, (i === 0 ? t('org.keepSuggest') : t('org.simMark')) + p),
    ]))) },
])

const simColumnsWithSelection = computed(() => [{ type: 'selection' }, ...simColumns.value])

async function cleanupSimilar() {
  const groups = similar.value.groups.filter((g) => selectedSimGroups.value.includes(g.hash))
  if (!groups.length) return message.warning(t('org.msgNeedSimGroups'))
  const toMove = groups.reduce((n, g) => n + g.paths.length - 1, 0)
  const ok = window.confirm(t('org.confirmSim', { n: toMove }))
  if (!ok) return
  cleaning.value = true
  try {
    const r = await api('dedupe_cleanup', JSON.parse(JSON.stringify(groups)), keepRule.value, false)
    if (r.failed === 0) {
      message.success(t('org.msgMovedClean', { moved: r.moved, kept: r.kept, id: r.batch_id }))
    } else {
      message.warning(t('org.msgMovePartial', { moved: r.moved, failed: r.failed }))
    }
    loadHistory()
    scan()
  } catch (e) { message.error(String(e)) }
  cleaning.value = false
}

// ---------- 大文件清理（复用 browse_files 的 size 语法，零后端改动） ----------
const bigFiles = ref(null)
const bigRunning = ref(false)
const bigChecked = ref([])
const bigMinSize = ref('1gb')
const bigSizeOptions = computed(() => [
  { label: '> 1 GB', value: '1gb' },
  { label: '> 500 MB', value: '500mb' },
  { label: '> 100 MB', value: '100mb' },
  { label: '> 10 MB', value: '10mb' },
  { label: t('org.unlimited'), value: '' },
])

async function findBigFiles() {
  bigRunning.value = true
  bigChecked.value = []
  try {
    const q = bigMinSize.value ? `size:>${bigMinSize.value}` : null
    const r = await api('browse_files', q, null, null, 0, 300, 'size', 'desc')
    bigFiles.value = r.items
    if (!r.items.length) message.info(t('org.msgBigNone'))
  } catch (e) { message.error(String(e)) }
  bigRunning.value = false
}

const bigWaste = computed(() =>
  (bigFiles.value || [])
    .filter((f) => bigChecked.value.includes(f.path))
    .reduce((s, f) => s + (f.size || 0), 0))

const bigColumns = computed(() => [
  { type: 'selection' },
  { title: t('org.colSize'), key: 'size', width: 100, render: (r) => h('b', null, fmtSize(r.size)) },
  { title: t('org.colName'), key: 'name', ellipsis: { tooltip: true } },
  { title: t('org.colCategory'), key: 'category', width: 90, render: (r) => h(NTag, { size: 'small', bordered: false }, { default: () => catLabel(r.category) }) },
  { title: t('org.colMtime'), key: 'mtime', width: 160, render: (r) => new Date(r.mtime * 1000).toLocaleString() },
  { title: t('org.colPath'), key: 'path', ellipsis: { tooltip: true },
    render: (r) => h('span', { class: 'mono', style: 'cursor:pointer', title: t('org.colPath'),
      onClick: () => api('open_path', r.path) }, r.path) },
])

async function trashBigFiles() {
  const paths = (bigFiles.value || []).filter((f) => bigChecked.value.includes(f.path)).map((f) => f.path)
  if (!paths.length) return message.warning(t('org.msgNeedBig'))
  const ok = window.confirm(t('org.confirmBig', { n: paths.length, size: fmtSize(bigWaste.value) }))
  if (!ok) return
  const r = await api('trash_files', JSON.parse(JSON.stringify(paths)))
  const okN = r.results.filter((x) => x.ok).length
  message.success(t('org.msgTrashed', { ok: okN, all: paths.length }))
  findBigFiles()
}

// ---------- 冲突副本（同步冲突 + 编号副本，全索引范围） ----------
const conflicts = ref(null)
const conflictsRunning = ref(false)
const conflictChecked = ref([])

async function findConflicts() {
  conflictsRunning.value = true
  conflictChecked.value = []
  try {
    conflicts.value = (await api('find_conflict_files')).items
    if (!conflicts.value.length) message.info(t('org.msgConflictNone'))
  } catch (e) { message.error(String(e)) }
  conflictsRunning.value = false
}

const conflictColumns = computed(() => [
  { type: 'selection' },
  { title: t('org.colReason'), key: 'reason', width: 100,
    render: (r) => h(NTag, { size: 'small', type: r.reason === '同步冲突' ? 'error' : 'warning', bordered: false },
      { default: () => r.reason }) },
  { title: t('org.colName'), key: 'name', ellipsis: { tooltip: (r) => r.path },
    render: (r) => r.path.split(/[\\/]/).pop() },
  { title: t('org.colSize'), key: 'size', width: 100, render: (r) => fmtSize(r.size) },
  { title: t('org.colMtime'), key: 'mtime', width: 160, render: (r) => new Date(r.mtime * 1000).toLocaleString() },
  { title: t('org.colRelated'), key: 'related', ellipsis: { tooltip: true },
    render: (r) => r.related ? h('span', { class: 'mono' }, r.related) : h('span', { style: 'opacity:.4' }, '-') },
])

async function trashConflicts() {
  const paths = (conflicts.value || []).filter((f) => conflictChecked.value.includes(f.path)).map((f) => f.path)
  if (!paths.length) return message.warning(t('org.msgNeedConflict'))
  const ok = window.confirm(t('org.confirmConflict', { n: paths.length }))
  if (!ok) return
  const r = await api('trash_files', JSON.parse(JSON.stringify(paths)))
  const okN = r.results.filter((x) => x.ok).length
  message.success(t('org.msgTrashed', { ok: okN, all: paths.length }))
  findConflicts()
}

// ---------- 整理模板 ----------
const templates = ref([])
const showSaveTpl = ref(false)
const tplName = ref('')

async function loadTemplates() {
  try { templates.value = (await api('list_templates')).templates } catch (e) { /* 忽略 */ }
}

function applyTemplate(tp) {
  targetRoot.value = tp.target_root
  message.info(t('org.msgTplApplied', { name: tp.name }))
}

async function saveTemplateNow() {
  if (!tplName.value.trim()) return message.warning(t('org.msgNeedTplName'))
  const byYear = (await api('get_status')).images_by_year === '1'
  await api('save_template', tplName.value.trim(), targetRoot.value, byYear)
  message.success(t('org.msgTplSaved'))
  showSaveTpl.value = false
  tplName.value = ''
  loadTemplates()
}

async function delTemplate(id) {
  await api('delete_template', id)
  loadTemplates()
}

// ---------- 历史与撤销 ----------
async function loadHistory() {
  const r = await api('history')
  history.value = r.batches
}

// ---------- 空文件夹 ----------
const emptyDirs = ref([])
const loadingDirs = ref(false)
const cleaningDirs = ref(false)
const scanned = ref(false)
async function loadEmptyDirs() {
  loadingDirs.value = true
  scanned.value = false
  try {
    const r = await api('list_empty_dirs')
    emptyDirs.value = r.dirs || []
    scanned.value = true
  } catch (e) { message.error(String(e)) }
  loadingDirs.value = false
}
async function cleanEmptyDirs() {
  const ok = window.confirm(t('org.confirmEmpty', { n: emptyDirs.value.length }))
  if (!ok) return
  cleaningDirs.value = true
  const r = await api('trash_empty_dirs', JSON.parse(JSON.stringify(emptyDirs.value)))
  const okN = r.results.filter((x) => x.ok).length
  message.success(t('org.msgEmptyTrashed', { ok: okN, all: r.results.length }))
  cleaningDirs.value = false
  loadEmptyDirs()
}

async function undo(batchId) {
  const r = await api('undo_batch', batchId)
  if (r.failed) message.warning(t('org.msgUndoPartial', { done: r.undone, fail: r.failed }))
  else if (r.recycle_bin) {
    message.info(t('org.msgUndone', { done: r.undone, bin: r.recycle_bin }))
  } else if (!r.undone) {
    message.info(t('org.msgUndoNone'))
  } else message.success(t('org.msgUndoneAll', { n: r.undone }))
  loadHistory()
}

async function redo(batchId) {
  const r = await api('redo_batch', batchId)
  if (r.failed === 0) message.success(t('org.msgRedone', { n: r.redone }))
  else message.warning(t('org.msgRedoPartial', { done: r.redone, fail: r.failed }))
  loadHistory()
}

async function showOps(batchId) {
  const r = await api('batch_operations', batchId)
  batchOps.value = r.ops
  showBatchOps.value = true
}

function fmtSize(n) {
  if (n == null) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return n.toFixed(1) + ' ' + units[i]
}
function fmtTime(ts) { return new Date(ts * 1000).toLocaleString() }

loadHistory()
loadTemplates()
</script>

<template>
  <div class="fb-page">
    <n-tabs v-model:value="tab" type="line">
      <!-- ================= 整理 ================= -->
      <n-tab-pane name="organize" :tab="t('org.tabOrganize')">
        <n-card>
          <n-space vertical size="large">
            <n-space align="center">
              <n-input v-model:value="rootPath" :placeholder="t('org.pickPh')" style="width:480px" readonly>
                <template #suffix>
                  <n-button size="tiny" @click="pickFolder">{{ t('org.browse') }}</n-button>
                </template>
              </n-input>
              <n-button type="primary" :loading="scanning" @click="scan">{{ t('org.scan') }}</n-button>
              <n-button v-if="scanInfo && ambiguousFiles.length > 0" :loading="aiRunning" type="warning" ghost @click="runAiClassify">
                {{ t('org.aiClassify', { n: ambiguousFiles.length }) }}
              </n-button>
            </n-space>

            <n-progress v-if="aiRunning && aiProgress" :percentage="Math.round(aiProgress.i / aiProgress.n * 100)" processing>
              <span class="mono">{{ t('org.aiProgress', { i: aiProgress.i, n: aiProgress.n, name: aiProgress.name }) }}</span>
            </n-progress>

            <template v-if="scanInfo">
              <n-space align="center">
                <n-text>{{ t('org.filesN', { n: scanInfo.files.length }) }}</n-text>
                <n-tag v-if="scanInfo.ambiguous" type="warning">{{ t('org.ambiguous', { n: scanInfo.ambiguous }) }}</n-tag>
              </n-space>

              <n-data-table :columns="scanColumns" :data="previewFiles" :max-height="300" size="small"
                :row-key="(r) => r.path" virtual-scroll />

              <n-space align="center" item-style="display:flex;align-items:center;gap:6px">
                <n-text>{{ t('org.toDir') }}</n-text>
                <n-input v-model:value="targetRoot" style="width:400px" class="mono">
                  <template #suffix>
                    <n-button size="tiny" @click="pickFolderThenKeep">{{ t('org.browse') }}</n-button>
                  </template>
                </n-input>
                <n-select v-if="templates.length" :options="templates.map(tp => ({ label: t('org.tplPrefix') + tp.name, value: tp.id }))"
                  :placeholder="t('org.tplPh')" size="small" style="width:170px" @update:value="(id) => applyTemplate(templates.find(t => t.id === id))" />
                <n-button size="small" @click="showSaveTpl = true">{{ t('org.saveTpl') }}</n-button>
              </n-space>
              <n-space>
                <n-button type="primary" :loading="executing" @click="buildPlan">{{ t('org.buildPlan') }}</n-button>
              </n-space>
            </template>

            <template v-if="planData">
              <n-alert v-if="Object.keys(planData.summary).length" type="success" :bordered="false">
                <n-space>
                  <span v-for="(v, cat) in planData.summary" :key="cat">
                    <n-tag size="small">{{ catLabel(cat) }}</n-tag> {{ v.count }} {{ t('files.batch.unit') }} · {{ fmtSize(v.size) }}
                  </span>
                </n-space>
              </n-alert>
              <n-data-table v-model:checked-row-keys="checkedKeys" :columns="planColumns" :data="planRows"
                :max-height="320" size="small" :row-key="(r) => r.src" virtual-scroll />
              <n-space>
                <n-button type="error" :loading="executing" :disabled="checkedKeys.length === 0" @click="execute">
                  {{ t('org.confirmRun', { n: checkedKeys.length }) }}
                </n-button>
                <n-text depth="3">{{ t('org.undoHint') }}</n-text>
              </n-space>
            </template>
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 重复 ================= -->
      <n-tab-pane name="dedupe" :tab="t('org.tabDedupe')">
        <n-card>
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" :loading="dedupeRunning" :disabled="!scanInfo" @click="runDedupe">
                {{ t('org.detect') }}
              </n-button>
              <n-text v-if="!scanInfo" depth="3">{{ t('org.needScanFirst') }}</n-text>
            </n-space>
            <template v-if="dedupe">
              <n-alert v-if="dedupe.groups.length" type="warning" :bordered="false">
                {{ t('org.dupFound', { n: dedupe.groups.length, size: fmtSize(dedupe.total_waste) }) }}
                {{ t('org.dupHint') }}
              </n-alert>
              <n-space v-if="dedupe.groups.length">
                <n-button size="small" @click="selectedDupGroups = dedupe.groups.map(g => g.hash)">{{ t('org.selectAll') }}</n-button>
                <n-button size="small" @click="selectedDupGroups = []">{{ t('org.clearAll') }}</n-button>
                <n-select v-model:value="keepRule" :options="keepRuleOptions" size="small" style="width:150px" />
                <n-button size="small" type="warning" :loading="cleaning"
                  :disabled="!selectedDupGroups.length" @click="cleanupDuplicates">
                  {{ t('org.moveToClean', { n: selectedDupGroups.length }) }}
                </n-button>
              </n-space>
              <n-data-table :columns="dedupeColumnsWithSelection"
                :data="dedupe.groups" size="small" :row-key="(r) => r.hash"
                :checked-row-keys="selectedDupGroups"
                @update:checked-row-keys="(keys) => (selectedDupGroups = keys)" />
            </template>
            <n-empty v-else-if="!dedupeRunning" :description="t('org.notDetected')" />
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 相似图片 ================= -->
      <n-tab-pane name="similar" :tab="t('org.tabSimilar')">
        <n-card>
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" :loading="similarRunning" :disabled="!scanInfo" @click="runSimilar">
                {{ t('org.detectSim') }}
              </n-button>
              <n-text v-if="!scanInfo" depth="3">{{ t('org.needScanFirst') }}</n-text>
              <n-text v-else depth="3">{{ t('org.simHint') }}</n-text>
            </n-space>
            <template v-if="similar">
              <n-alert v-if="similar.groups.length" type="warning" :bordered="false">
                {{ t('org.simFound', { n: similar.groups.length, size: fmtSize(similar.total_waste) }) }}
                {{ t('org.simHint2') }}
              </n-alert>
              <n-space v-if="similar.groups.length">
                <n-button size="small" @click="selectedSimGroups = similar.groups.map(g => g.hash)">{{ t('org.selectAll') }}</n-button>
                <n-button size="small" @click="selectedSimGroups = []">{{ t('org.clearAll') }}</n-button>
                <n-select v-model:value="keepRule" :options="keepRuleOptions" size="small" style="width:150px" />
                <n-button size="small" type="warning" :loading="cleaning"
                  :disabled="!selectedSimGroups.length" @click="cleanupSimilar">
                  {{ t('org.moveToClean', { n: selectedSimGroups.length }) }}
                </n-button>
              </n-space>
              <n-data-table :columns="simColumnsWithSelection"
                :data="similar.groups" size="small" :row-key="(r) => r.hash"
                :checked-row-keys="selectedSimGroups"
                @update:checked-row-keys="(keys) => (selectedSimGroups = keys)" />
            </template>
            <n-empty v-else-if="!similarRunning" :description="t('org.notDetected')" />
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 历史 ================= -->
      <n-tab-pane name="history" :tab="t('org.tabHistory')">
        <n-card>
          <n-list v-if="history.length" bordered>
            <n-list-item v-for="b in history" :key="b.batch_id">
              <n-space justify="space-between" align="center" style="width:100%">
                <n-space align="center">
                  <n-tag :type="b.undone_n > 0 ? 'default' : 'info'" size="small">
                    {{ b.undone_n > 0 ? t('org.undone') : t('org.executed') }}
                  </n-tag>
                  <n-text>{{ fmtTime(b.ts) }}</n-text>
                  <n-text depth="3">{{ t('org.opsN', { n: b.n, id: b.batch_id }) }}</n-text>
                </n-space>
                <n-space>
                  <n-button size="small" @click="showOps(b.batch_id)">{{ t('org.viewDetail') }}</n-button>
                  <n-button v-if="!b.undone_n" size="small" type="warning" ghost @click="undo(b.batch_id)">{{ t('org.undoBatch') }}</n-button>
                  <n-button v-else size="small" type="info" ghost @click="redo(b.batch_id)">{{ t('org.redoBatch') }}</n-button>
                </n-space>
              </n-space>
            </n-list-item>
          </n-list>
          <n-empty v-else :description="t('org.noHistory')" />
        </n-card>
      </n-tab-pane>

      <!-- ================= 大文件 ================= -->
      <n-tab-pane name="bigfiles" :tab="t('org.tabBig')">
        <n-card>
          <n-space vertical size="large">
            <n-space align="center">
              <n-select v-model:value="bigMinSize" :options="bigSizeOptions" size="small" style="width:130px" />
              <n-button type="primary" :loading="bigRunning" @click="findBigFiles">{{ t('org.findBig') }}</n-button>
              <n-text depth="3" style="font-size:12px">{{ t('org.bigHint') }}</n-text>
            </n-space>
            <template v-if="bigFiles && bigFiles.length">
              <n-space align="center">
                <n-text>{{ t('org.canFree', { size: fmtSize(bigWaste) }) }}</n-text>
                <n-button size="small" @click="bigChecked = bigFiles.map((f) => f.path)">{{ t('org.selectAll') }}</n-button>
                <n-button size="small" @click="bigChecked = []">{{ t('org.clearAll') }}</n-button>
                <n-button size="small" type="error" ghost :disabled="!bigChecked.length" @click="trashBigFiles">
                  {{ t('org.trashN', { n: bigChecked.length }) }}
                </n-button>
              </n-space>
              <n-data-table v-model:checked-row-keys="bigChecked" :columns="bigColumns"
                :data="bigFiles" :max-height="420" size="small" :row-key="(r) => r.path" virtual-scroll />
            </template>
            <n-empty v-else-if="!bigRunning && bigFiles" :description="t('org.noBig')" />
            <n-empty v-else-if="!bigRunning" :description="t('org.clickFindBig')" />
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 冲突副本 ================= -->
      <n-tab-pane name="conflicts" :tab="t('org.tabConflicts')">
        <n-card>
          <n-space vertical size="large">
            <n-space align="center">
              <n-button type="primary" :loading="conflictsRunning" @click="findConflicts">{{ t('org.detectConflict') }}</n-button>
              <n-text depth="3" style="font-size:12px">
                {{ t('org.conflictHint') }}
              </n-text>
            </n-space>
            <template v-if="conflicts && conflicts.length">
              <n-space align="center">
                <n-text>{{ t('org.conflictSum', { n: conflicts.length, c: conflictChecked.length }) }}</n-text>
                <n-button size="small" @click="conflictChecked = conflicts.map((f) => f.path)">{{ t('org.selectAll') }}</n-button>
                <n-button size="small" @click="conflictChecked = []">{{ t('org.clearAll') }}</n-button>
                <n-button size="small" type="error" ghost :disabled="!conflictChecked.length" @click="trashConflicts">
                  {{ t('org.trashN', { n: conflictChecked.length }) }}
                </n-button>
              </n-space>
              <n-data-table v-model:checked-row-keys="conflictChecked" :columns="conflictColumns"
                :data="conflicts" :max-height="420" size="small" :row-key="(r) => r.path" virtual-scroll />
            </template>
            <n-empty v-else-if="!conflictsRunning && conflicts" :description="t('org.noConflict')" />
            <n-empty v-else-if="!conflictsRunning" :description="t('org.clickDetectConflict')" />
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 空文件夹 ================= -->
      <n-tab-pane name="emptydirs" :tab="t('org.tabEmpty')">
        <n-card>
          <n-space vertical size="large">
            <n-space align="center">
              <n-button size="small" type="primary" ghost :loading="loadingDirs" @click="loadEmptyDirs">
                {{ t('org.scanEmpty') }}
              </n-button>
              <n-button v-if="emptyDirs.length" size="small" type="error" ghost
                :loading="cleaningDirs" @click="cleanEmptyDirs">
                {{ t('org.trashEmpty', { n: emptyDirs.length }) }}
              </n-button>
              <n-text depth="3" style="font-size:12px">
                {{ t('org.emptyHint') }}
              </n-text>
            </n-space>
            <n-list v-if="emptyDirs.length" bordered style="max-height:420px;overflow:auto">
              <n-list-item v-for="d in emptyDirs" :key="d">
                <n-space justify="space-between" align="center" style="width:100%">
                  <n-text class="mono" style="font-size:12px">{{ d }}</n-text>
                  <n-button size="tiny" quaternary @click="api('open_path', d)">{{ t('org.open') }}</n-button>
                </n-space>
              </n-list-item>
            </n-list>
            <n-empty v-else-if="scanned" :description="t('org.noEmpty')" />
          </n-space>
        </n-card>
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showBatchOps" preset="card" :title="t('org.batchDetail')" style="width:860px">
      <n-data-table :columns="[
        { title: t('org.colTime'), key: 'ts', width: 160, render: (r) => fmtTime(r.ts) },
        { title: t('org.colSrc'), key: 'src', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.src) },
        { title: t('org.colNewPath'), key: 'dst', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.dst) },
      ]" :data="batchOps" :max-height="480" size="small" />
    </n-modal>

    <n-modal v-model:show="showSaveTpl" preset="dialog" :title="t('org.saveTplTitle')"
      :positive-text="t('org.save')" :negative-text="t('org.cancel')" @positive-click="saveTemplateNow">
      {{ t('org.tplNameLabel') }}<n-input v-model:value="tplName" :placeholder="t('org.tplNamePh')" style="width:280px" />
      <br><n-text depth="3" style="font-size:12px">{{ t('org.tplNameHint') }}</n-text>
    </n-modal>
  </div>
</template>
