<script setup>
import { ref, computed, h, onUnmounted } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NText, NTag, NDataTable, NProgress,
  NTabs, NTabPane, NSelect, NModal, NList, NListItem, NDescriptions, NDescriptionsItem,
  NCheckbox, useMessage, NEmpty, NAlert, NSpin,
} from 'naive-ui'
import { api, on } from '../lib/bridge'

const message = useMessage()

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
const categoryOptions = CATEGORIES.map((c) => ({ label: c, value: c }))

let offs = []
offs.push(on('ai_classify_start', (p) => { aiRunning.value = true }))
offs.push(on('ai_classify_progress', (p) => { aiProgress.value = p }))
offs.push(on('ai_classify_done', (p) => {
  // 后端回传补全 ai_category 的 files
  scanInfo.value.files = p.files
  aiRunning.value = false
  aiProgress.value = null
  message.success('AI 分类完成')
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
  if (!rootPath.value) return message.warning('请先选择要整理的文件夹')
  scanning.value = true
  scanInfo.value = null
  planData.value = null
  try {
    scanInfo.value = await api('scan_folder', rootPath.value)
    targetRoot.value = rootPath.value
    if (scanInfo.value.files.length === 0) message.info('该文件夹没有发现文件')
  } catch (e) {
    message.error('扫描失败：' + e)
  }
  scanning.value = false
}

const ambiguousFiles = computed(() =>
  (scanInfo.value?.files || []).filter((f) => !f.rule_hit))

const scanColumns = [
  { title: '文件名', key: 'name', ellipsis: { tooltip: (r) => r.ai_reason || r.name } },
  { title: '类别', key: 'category', width: 110, render: (r) => h(NTag, {
      size: 'small', bordered: false,
      type: r.ai_category ? 'success' : (r.rule_hit ? 'default' : 'warning'),
    }, { default: () => r.final_category || r.category }) },
  { title: '扩展名', key: 'ext', width: 90, render: (r) => h('span', { class: 'mono' }, r.ext || '-') },
  { title: '大小', key: 'size', width: 100, render: (r) => fmtSize(r.size) },
]

async function runAiClassify() {
  if (ambiguousFiles.value.length === 0) return message.info('没有需要 AI 判断的文件')
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
    if (n === 0) message.info('所有文件都已在合适的位置，无需整理')
  } catch (e) {
    message.error('生成计划失败：' + e)
  }
  executing.value = false
}

const planColumns = [
  { type: 'selection' },
  { title: '原路径', key: 'src', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.src) },
  { title: '→ 目标', key: 'dst', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.dst || r.src) },
  { title: '类别', key: 'category', width: 110, render: (r) => h(NTag, { size: 'small', bordered: false }, { default: () => r.category }) },
]

const planRows = computed(() => planData.value?.plan || [])

async function execute() {
  if (checkedKeys.value.length === 0) return message.warning('请先勾选要整理的文件')
  executing.value = true
  try {
    const rows = planRows.value.filter((r) => checkedKeys.value.includes(r.src))
    const result = await api('execute_plan', JSON.parse(JSON.stringify(rows)))
    const ok = result.results.filter((r) => r.ok).length
    const fail = result.results.length - ok
    if (fail === 0) message.success(`整理完成：${ok} 个文件已移动`)
    else message.warning(`完成：${ok} 成功 / ${fail} 失败（详见操作历史）`)
    loadHistory()
    scan()  // 重新扫描反映现状
  } catch (e) {
    message.error('执行失败：' + e)
  }
  executing.value = false
}

// ---------- 重复检测 ----------
async function runDedupe() {
  if (!scanInfo.value) return message.warning('请先扫描文件夹')
  dedupeRunning.value = true
  dedupe.value = null
  try {
    dedupe.value = await api('find_duplicates', JSON.parse(JSON.stringify(scanInfo.value.files)))
    if (dedupe.value.groups.length === 0) message.info('没有发现重复文件')
  } catch (e) {
    message.error('检测失败：' + e)
  }
  dedupeRunning.value = false
}

const dedupeColumns = [
  { title: '哈希(前16)', key: 'hash', width: 130, render: (r) => h('span', { class: 'mono' }, r.hash) },
  { title: '大小', key: 'size', width: 90, render: (r) => fmtSize(r.size) },
  { title: '文件列表（点击打开所在位置）', key: 'paths', render: (r) =>
    h('div', null, r.paths.map((p, i) => h('div', {
      class: 'mono', style: 'cursor:pointer;color:' + (i === 0 ? '#18a058' : '#666') + ';padding:1px 0',
      onClick: () => api('open_path', p),
      title: p,
    }, (i === 0 ? '✓ 保留建议  ' : '   重复     ') + p))) },
]

// ---------- 重复清理 ----------
const selectedDupGroups = ref([])
const keepRule = ref('newest')
const keepRuleOptions = [
  { label: '保留最新', value: 'newest' },
  { label: '保留最早', value: 'oldest' },
  { label: '保留路径最短', value: 'shortest' },
]
const cleaning = ref(false)

const dedupeColumnsWithSelection = [{ type: 'selection' }, ...dedupeColumns]

async function cleanupDuplicates() {
  const groups = dedupe.value.groups.filter((g) => selectedDupGroups.value.includes(g.hash))
  if (!groups.length) return message.warning('请先勾选要清理的重复组')
  const toMove = groups.reduce((n, g) => n + g.paths.length - 1, 0)
  const ok = await new Promise((resolve) => {
    // 简易确认：用 window.confirm 足够，避免再引弹窗组件
    resolve(window.confirm(`将把 ${toMove} 个重复文件移入「待清理」文件夹（不删除，可在操作历史撤销）。继续？`))
  })
  if (!ok) return
  cleaning.value = true
  try {
    const r = await api('dedupe_cleanup', JSON.parse(JSON.stringify(groups)), keepRule.value, false)
    if (r.failed === 0) {
      message.success(`已移入待清理 ${r.moved} 个（保留 ${r.kept} 个），批次 ${r.batch_id}`)
    } else {
      message.warning(`移动 ${r.moved} 个，失败 ${r.failed} 个`)
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
  if (!scanInfo.value) return message.warning('请先扫描文件夹')
  similarRunning.value = true
  similar.value = null
  selectedSimGroups.value = []
  try {
    const st = await api('get_status')
    simThumbBase.value = st.thumb_base
    similar.value = await api('find_similar_images',
      JSON.parse(JSON.stringify(scanInfo.value.files)))
    if (similar.value.groups.length === 0) message.info('没有发现相似图片（缩放/压缩/连拍近似重复）')
  } catch (e) {
    message.error('检测失败：' + e)
  }
  similarRunning.value = false
}

const simColumns = [
  { title: '相似组（点击打开所在位置）', key: 'paths', render: (r) =>
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
      }, (i === 0 ? '✓ 保留建议  ' : '近似重复  ') + p),
    ]))) },
]

const simColumnsWithSelection = [{ type: 'selection' }, ...simColumns]

async function cleanupSimilar() {
  const groups = similar.value.groups.filter((g) => selectedSimGroups.value.includes(g.hash))
  if (!groups.length) return message.warning('请先勾选要清理的相似组')
  const toMove = groups.reduce((n, g) => n + g.paths.length - 1, 0)
  const ok = window.confirm(`将把 ${toMove} 个近似重复图片移入「待清理」文件夹（不删除，可在操作历史撤销）。继续？`)
  if (!ok) return
  cleaning.value = true
  try {
    const r = await api('dedupe_cleanup', JSON.parse(JSON.stringify(groups)), keepRule.value, false)
    if (r.failed === 0) {
      message.success(`已移入待清理 ${r.moved} 个（保留 ${r.kept} 个），批次 ${r.batch_id}`)
    } else {
      message.warning(`移动 ${r.moved} 个，失败 ${r.failed} 个`)
    }
    loadHistory()
    scan()
  } catch (e) { message.error(String(e)) }
  cleaning.value = false
}

// ---------- 整理模板 ----------
const templates = ref([])
const showSaveTpl = ref(false)
const tplName = ref('')

async function loadTemplates() {
  try { templates.value = (await api('list_templates')).templates } catch (e) { /* 忽略 */ }
}

function applyTemplate(t) {
  targetRoot.value = t.target_root
  message.info(`已应用模板「${t.name}」`)
}

async function saveTemplateNow() {
  if (!tplName.value.trim()) return message.warning('请输入模板名')
  const byYear = (await api('get_status')).images_by_year === '1'
  await api('save_template', tplName.value.trim(), targetRoot.value, byYear)
  message.success('模板已保存')
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
  const ok = window.confirm(`将 ${emptyDirs.value.length} 个空文件夹移入系统回收站？\n可在资源管理器回收站中恢复。`)
  if (!ok) return
  cleaningDirs.value = true
  const r = await api('trash_empty_dirs', JSON.parse(JSON.stringify(emptyDirs.value)))
  const okN = r.results.filter((x) => x.ok).length
  message.success(`已送入回收站 ${okN}/${r.results.length}`)
  cleaningDirs.value = false
  loadEmptyDirs()
}

async function undo(batchId) {
  const r = await api('undo_batch', batchId)
  if (r.failed === 0) message.success(`已撤销 ${r.undone} 个操作`)
  else message.warning(`撤销 ${r.undone} 个，失败 ${r.failed} 个`)
  loadHistory()
}

async function redo(batchId) {
  const r = await api('redo_batch', batchId)
  if (r.failed === 0) message.success(`已重做 ${r.redone} 个操作（恢复整理后的位置）`)
  else message.warning(`重做 ${r.redone} 个，失败 ${r.failed} 个`)
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
      <n-tab-pane name="organize" tab="智能整理">
        <n-card>
          <n-space vertical size="large">
            <n-space align="center">
              <n-input v-model:value="rootPath" placeholder="选择要整理的文件夹…" style="width:480px" readonly>
                <template #suffix>
                  <n-button size="tiny" @click="pickFolder">浏览</n-button>
                </template>
              </n-input>
              <n-button type="primary" :loading="scanning" @click="scan">扫描</n-button>
              <n-button v-if="scanInfo && ambiguousFiles.length > 0" :loading="aiRunning" type="warning" ghost @click="runAiClassify">
                AI 分类（{{ ambiguousFiles.length }} 个模糊文件）
              </n-button>
            </n-space>

            <n-progress v-if="aiRunning && aiProgress" :percentage="Math.round(aiProgress.i / aiProgress.n * 100)" processing>
              <span class="mono">AI 判断 {{ aiProgress.i }}/{{ aiProgress.n }} · {{ aiProgress.name }}</span>
            </n-progress>

            <template v-if="scanInfo">
              <n-space align="center">
                <n-text>共 <b>{{ scanInfo.files.length }}</b> 个文件</n-text>
                <n-tag v-if="scanInfo.ambiguous" type="warning">规则无法归类 {{ scanInfo.ambiguous }} 个</n-tag>
              </n-space>

              <n-data-table :columns="scanColumns" :data="previewFiles" :max-height="300" size="small"
                :row-key="(r) => r.path" virtual-scroll />

              <n-space align="center" item-style="display:flex;align-items:center;gap:6px">
                <n-text>整理到：</n-text>
                <n-input v-model:value="targetRoot" style="width:400px" class="mono">
                  <template #suffix>
                    <n-button size="tiny" @click="pickFolderThenKeep">浏览</n-button>
                  </template>
                </n-input>
                <n-select v-if="templates.length" :options="templates.map(t => ({ label: '模板: ' + t.name, value: t.id }))"
                  placeholder="整理模板" size="small" style="width:170px" @update:value="(id) => applyTemplate(templates.find(t => t.id === id))" />
                <n-button size="small" @click="showSaveTpl = true">存为模板</n-button>
              </n-space>
              <n-space>
                <n-button type="primary" :loading="executing" @click="buildPlan">生成整理计划（预览）</n-button>
              </n-space>
            </template>

            <template v-if="planData">
              <n-alert v-if="Object.keys(planData.summary).length" type="success" :bordered="false">
                <n-space>
                  <span v-for="(v, cat) in planData.summary" :key="cat">
                    <n-tag size="small">{{ cat }}</n-tag> {{ v.count }} 个 · {{ fmtSize(v.size) }}
                  </span>
                </n-space>
              </n-alert>
              <n-data-table v-model:checked-row-keys="checkedKeys" :columns="planColumns" :data="planRows"
                :max-height="320" size="small" :row-key="(r) => r.src" virtual-scroll />
              <n-space>
                <n-button type="error" :loading="executing" :disabled="checkedKeys.length === 0" @click="execute">
                  确认执行（移动 {{ checkedKeys.length }} 个文件）
                </n-button>
                <n-text depth="3">执行后可在「操作历史」中一键撤销</n-text>
              </n-space>
            </template>
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 重复 ================= -->
      <n-tab-pane name="dedupe" tab="重复检测">
        <n-card>
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" :loading="dedupeRunning" :disabled="!scanInfo" @click="runDedupe">
                检测重复文件
              </n-button>
              <n-text v-if="!scanInfo" depth="3">请先在「智能整理」页扫描一个文件夹</n-text>
            </n-space>
            <template v-if="dedupe">
              <n-alert v-if="dedupe.groups.length" type="warning" :bordered="false">
                发现 {{ dedupe.groups.length }} 组重复文件，可释放约 {{ fmtSize(dedupe.total_waste) }} 空间。
                勾选要清理的组，选择保留规则后一键移入「待清理」文件夹——<b>不删除文件</b>，随时可在「操作历史」撤销。
              </n-alert>
              <n-space v-if="dedupe.groups.length">
                <n-button size="small" @click="selectedDupGroups = dedupe.groups.map(g => g.hash)">全选</n-button>
                <n-button size="small" @click="selectedDupGroups = []">清空</n-button>
                <n-select v-model:value="keepRule" :options="keepRuleOptions" size="small" style="width:150px" />
                <n-button size="small" type="warning" :loading="cleaning"
                  :disabled="!selectedDupGroups.length" @click="cleanupDuplicates">
                  移入待清理（{{ selectedDupGroups.length }} 组）
                </n-button>
              </n-space>
              <n-data-table :columns="dedupeColumnsWithSelection"
                :data="dedupe.groups" size="small" :row-key="(r) => r.hash"
                :checked-row-keys="selectedDupGroups"
                @update:checked-row-keys="(keys) => (selectedDupGroups = keys)" />
            </template>
            <n-empty v-else-if="!dedupeRunning" description="尚未检测" />
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 相似图片 ================= -->
      <n-tab-pane name="similar" tab="相似图片">
        <n-card>
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" :loading="similarRunning" :disabled="!scanInfo" @click="runSimilar">
                检测相似图片（近似重复）
              </n-button>
              <n-text v-if="!scanInfo" depth="3">请先在「智能整理」页扫描一个文件夹</n-text>
              <n-text v-else depth="3">用感知哈希找缩放版 / 重压缩 / 连拍等「看起来一样」的图</n-text>
            </n-space>
            <template v-if="similar">
              <n-alert v-if="similar.groups.length" type="warning" :bordered="false">
                发现 {{ similar.groups.length }} 组相似图片，可释放约 {{ fmtSize(similar.total_waste) }} 空间。
                勾选要清理的组后移入「待清理」文件夹——<b>不删除文件</b>，可在「操作历史」撤销。
              </n-alert>
              <n-space v-if="similar.groups.length">
                <n-button size="small" @click="selectedSimGroups = similar.groups.map(g => g.hash)">全选</n-button>
                <n-button size="small" @click="selectedSimGroups = []">清空</n-button>
                <n-select v-model:value="keepRule" :options="keepRuleOptions" size="small" style="width:150px" />
                <n-button size="small" type="warning" :loading="cleaning"
                  :disabled="!selectedSimGroups.length" @click="cleanupSimilar">
                  移入待清理（{{ selectedSimGroups.length }} 组）
                </n-button>
              </n-space>
              <n-data-table :columns="simColumnsWithSelection"
                :data="similar.groups" size="small" :row-key="(r) => r.hash"
                :checked-row-keys="selectedSimGroups"
                @update:checked-row-keys="(keys) => (selectedSimGroups = keys)" />
            </template>
            <n-empty v-else-if="!similarRunning" description="尚未检测" />
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 历史 ================= -->
      <n-tab-pane name="history" tab="操作历史">
        <n-card>
          <n-list v-if="history.length" bordered>
            <n-list-item v-for="b in history" :key="b.batch_id">
              <n-space justify="space-between" align="center" style="width:100%">
                <n-space align="center">
                  <n-tag :type="b.undone_n > 0 ? 'default' : 'info'" size="small">
                    {{ b.undone_n > 0 ? '已撤销' : '已执行' }}
                  </n-tag>
                  <n-text>{{ fmtTime(b.ts) }}</n-text>
                  <n-text depth="3">{{ b.n }} 个操作 · 批次 {{ b.batch_id }}</n-text>
                </n-space>
                <n-space>
                  <n-button size="small" @click="showOps(b.batch_id)">查看明细</n-button>
                  <n-button v-if="!b.undone_n" size="small" type="warning" ghost @click="undo(b.batch_id)">撤销此批次</n-button>
                  <n-button v-else size="small" type="info" ghost @click="redo(b.batch_id)">重做（取消撤销）</n-button>
                </n-space>
              </n-space>
            </n-list-item>
          </n-list>
          <n-empty v-else description="暂无操作记录" />
        </n-card>
      </n-tab-pane>

      <!-- ================= 空文件夹 ================= -->
      <n-tab-pane name="emptydirs" tab="空文件夹">
        <n-card>
          <n-space vertical size="large">
            <n-space align="center">
              <n-button size="small" type="primary" ghost :loading="loadingDirs" @click="loadEmptyDirs">
                扫描空文件夹
              </n-button>
              <n-button v-if="emptyDirs.length" size="small" type="error" ghost
                :loading="cleaningDirs" @click="cleanEmptyDirs">
                全部送入回收站（{{ emptyDirs.length }}）
              </n-button>
              <n-text depth="3" style="font-size:12px">
                空目录送系统回收站可恢复；深层空目录也会列出
              </n-text>
            </n-space>
            <n-list v-if="emptyDirs.length" bordered style="max-height:420px;overflow:auto">
              <n-list-item v-for="d in emptyDirs" :key="d">
                <n-space justify="space-between" align="center" style="width:100%">
                  <n-text class="mono" style="font-size:12px">{{ d }}</n-text>
                  <n-button size="tiny" quaternary @click="api('open_path', d)">打开</n-button>
                </n-space>
              </n-list-item>
            </n-list>
            <n-empty v-else-if="scanned" description="没有发现空文件夹" />
          </n-space>
        </n-card>
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showBatchOps" preset="card" title="批次操作明细" style="width:860px">
      <n-data-table :columns="[
        { title: '时间', key: 'ts', width: 160, render: (r) => fmtTime(r.ts) },
        { title: '原路径', key: 'src', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.src) },
        { title: '新路径', key: 'dst', ellipsis: { tooltip: true }, render: (r) => h('span', { class: 'mono' }, r.dst) },
      ]" :data="batchOps" :max-height="480" size="small" />
    </n-modal>

    <n-modal v-model:show="showSaveTpl" preset="dialog" title="保存整理模板"
      positive-text="保存" negative-text="取消" @positive-click="saveTemplateNow">
      模板名：<n-input v-model:value="tplName" placeholder="如：工作盘整理 / 下载目录清理" style="width:280px" />
      <br><n-text depth="3" style="font-size:12px">保存当前「整理到」目录和图片归档设置</n-text>
    </n-modal>
  </div>
</template>
