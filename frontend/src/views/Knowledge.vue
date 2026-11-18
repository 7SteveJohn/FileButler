<script setup>
import { ref, computed, nextTick, onUnmounted, onActivated, h } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NText, NTag, NDataTable, NProgress,
  NTabs, NTabPane, NEmpty, NAlert, NPopover, NList, NListItem, useMessage, NSpin, NModal,
  NSelect,
} from 'naive-ui'
import { api, on } from '../lib/bridge'
import { useI18n } from 'vue-i18n'

const message = useMessage()
const { t } = useI18n()
const tab = ref('manage')

// ---------- 文件夹管理 ----------
const folders = ref([])
const foldersLoading = ref(false)
const indexing = ref({})       // folder_id -> {stage, i, n, detail}
const indexResult = ref(null)

async function loadFolders() {
  foldersLoading.value = true
  try {
    folders.value = (await api('kb_folders')).folders
  } catch (e) { message.error(String(e)) }
  foldersLoading.value = false
}

// ---------- 问答/检索范围（限定某个库或某个文件） ----------
const scopeFolder = ref(null)
const scopePath = ref(null)
const scopeFiles = ref([])

const scopeFolderOptions = computed(() =>
  folders.value.map((f) => ({ label: f.path, value: f.id })))
const scopeFileOptions = computed(() =>
  scopeFiles.value.map((p) => ({ label: p.split(/[\\/]/).pop(), value: p })))

async function onScopeFolder(id) {
  scopePath.value = null
  scopeFiles.value = []
  if (!id) return
  try {
    scopeFiles.value = (await api('kb_files', id)).items || []
  } catch (e) { /* 文件清单拿不到时仍可按整个文件夹限定 */ }
}

const scopeArg = () => (scopePath.value ? { path: scopePath.value }
  : scopeFolder.value ? { folder_id: scopeFolder.value } : null)

async function addFolder() {
  const p = await api('pick_folder')
  if (!p) return
  await api('kb_add_folder', p)
  message.success(t('kb.msgAdded'))
  loadFolders()
}

async function removeFolder(id) {
  // auto 文件夹跟随监控目录，不允许在此手动移除
  const f = folders.value.find((x) => x.id === id)
  if (f && f.source === 'auto') {
    message.warning(t('kb.msgAuto'))
    return
  }
  await api('kb_remove_folder', id)
  loadFolders()
}

async function startIndex(id) {
  const r = await api('kb_index', id)
  if (r.started) indexing.value[id] = { stage: 'scan', i: 0, n: 0, detail: '' }
}

function fmtEta(s) {
  if (!s || s < 0) return ''
  if (s < 60) return t('kb.sec', { s })
  const m = Math.floor(s / 60), r = s % 60
  if (m < 60) return r ? t('kb.min', { m, r }) : t('kb.minOnly', { m })
  return t('kb.hour', { h: Math.floor(m / 60), m: m % 60 })
}

function kbPct(id) {
  const p = indexing.value[id]
  return p?.n ? Math.round(p.i / p.n * 100) : 0
}

function kbStageText(id) {
  const stage = indexing.value[id]?.stage
  return ({ scan: t('kb.stage.scan'), parse: t('kb.stage.parse'), done: t('kb.stage.done') })[stage] || ''
}

async function cancelIndex(id) {
  const r = await api('kb_index_cancel', id)
  if (!r.ok) message.warning(r.error || t('kb.msgCancelFail'))
  else message.info(t('kb.msgCancelling'))
}

// ---------- 未能索引的文件 ----------
const failModal = ref(false)
const failItems = ref([])
const failFolder = ref(null)
async function openFailed(f) {
  failFolder.value = f
  failItems.value = []
  try {
    const r = await api('kb_failed_files', f.id)
    failItems.value = r.items || []
    failModal.value = true
  } catch (e) { message.error(String(e)) }
}

const folderColumns = computed(() => [
  { title: t('kb.colPath'), key: 'path', ellipsis: { tooltip: true }, render: (r) => h('span', null, [
      r.source === 'auto' ? h(NTag, { size: 'tiny', type: 'info', bordered: false,
        style: 'margin-right:6px' }, { default: () => t('kb.auto') }) : null,
      r.path,
    ]) },
  { title: t('kb.colFiles'), key: 'n_files', width: 70 },
  { title: t('kb.colFailed'), key: 'n_failed', width: 80, render: (r) => r.n_failed > 0
      ? h(NButton, { size: 'tiny', type: 'warning', text: true, onClick: () => openFailed(r) },
          { default: () => t('kb.failedN', { n: r.n_failed }) })
      : h('span', { style: 'opacity:.45' }, '—') },
  { title: t('kb.colChunks'), key: 'n_chunks', width: 70 },
  { title: t('kb.colLast'), key: 'last_index_at', width: 150, render: (r) => r.last_index_at ? new Date(r.last_index_at * 1000).toLocaleString() : t('kb.neverIndexed') },
  { title: t('kb.colActions'), key: 'actions', width: 220, render: (r) => {
    const idx = indexing.value[r.id]
    return h_NSpace(r, idx)
  } },
])

// 单独抽出来避免 render 里写太多 h()
function h_NSpace(r, idx) {
  return h(NSpace, null, {
    default: () => [
      h(NButton, { size: 'small', type: 'primary', secondary: true, onClick: () => startIndex(r.id), loading: !!idx }, { default: () => t('kb.btnIndex') }),
      h(NButton, { size: 'small', onClick: () => api('open_path', r.path) }, { default: () => t('kb.btnOpen') }),
      h(NButton, { size: 'small', type: 'error', ghost: true, onClick: () => removeFolder(r.id) }, { default: () => t('kb.btnRemove') }),
    ],
  })
}

let offs = []
offs.push(on('kb_index_start', (p) => { indexing.value[p.folder_id] = { stage: 'scan', i: 0, n: 0, detail: '' } }))
offs.push(on('kb_index_progress', (p) => { indexing.value[p.folder_id] = p }))
offs.push(on('kb_index_done', (p) => {
  delete indexing.value[p.folder_id]
  const r = p.result
  if (r && r.cancelled) {
    message.info(t('kb.msgStopped', { a: r.indexed, f: r.failed }))
  } else if (r && !r.error) {
    indexResult.value = r
    message.success(t('kb.msgDone', { a: r.indexed, s: r.skipped, r: r.removed, f: r.failed }))
  }
  loadFolders()
}))
offs.push(on('kb_index_error', (e) => {
  indexing.value = {}
  message.error(String(e))
}))
onUnmounted(() => offs.forEach((f) => f()))

// ---------- 问答 ----------
const question = ref('')
const messages = ref([])       // {role, content, sources?, streaming?}
const asking = ref(false)
const searchResults = ref(null)
const chatBox = ref(null)

// 会话历史
const sessions = ref([])
const currentSession = ref(null)

async function loadSessions() {
  try { sessions.value = (await api('qa_list_sessions')).sessions } catch (e) { /* 忽略 */ }
}

function newChat() {
  currentSession.value = null
  messages.value = []
  searchResults.value = null
}

async function openSession(s) {
  currentSession.value = s.id
  const r = await api('qa_messages', s.id)
  messages.value = r.messages
  searchResults.value = null
}

async function deleteSession(s) {
  await api('qa_delete_session', s.id)
  if (currentSession.value === s.id) newChat()
  loadSessions()
}

async function doAsk() {
  const q = question.value.trim()
  if (!q) return
  if (asking.value) return
  question.value = ''
  // 无会话则新建
  if (!currentSession.value) {
    currentSession.value = (await api('qa_new_session', q.slice(0, 40))).id
    loadSessions()
  }
  messages.value.push({ role: 'user', content: q })
  await api('qa_save_message', currentSession.value, 'user', q)
  const assistantMsg = { role: 'assistant', content: '', sources: [], streaming: true }
  messages.value.push(assistantMsg)
  asking.value = true
  scrollToBottom()

  const history = messages.value.slice(0, -2).map((m) => ({ role: m.role, content: m.content }))

  let streamOff = on('ask_delta', (p) => {
    assistantMsg.content += p.token
    scrollToBottom()
  })
  try {
    const result = await api('ask', q, JSON.parse(JSON.stringify(history)), scopeArg())
    if (result.error) {
      assistantMsg.content = '❌ ' + result.error
    } else {
      const citedSet = new Set(result.cited || [])
      const srcs = (result.sources || []).map((s) => ({
        ...s,
        cited: citedSet.has(s.index),
      }))
      assistantMsg.content = result.answer
      assistantMsg.sources = srcs
      await api('qa_save_message', currentSession.value, 'assistant', result.answer, srcs)
    }
  } catch (e) {
    assistantMsg.content = '❌ ' + String(e)
  }
  streamOff()
  assistantMsg.streaming = false
  asking.value = false
  loadSessions()
  scrollToBottom()
}

function scrollToBottom() {
  nextTick(() => {
    const el = chatBox.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// ---------- 快速搜索 & 引用内联预览 ----------
async function doSearch() {
  const q = question.value.trim()
  if (!q) return
  searchResults.value = null
  const r = await api('search', q, 10, scopeArg())
  searchResults.value = r
  tab.value = 'results'
}

function isSourceCited(m, s) {
  if (typeof s.cited === 'boolean') return s.cited
  return new RegExp(`\\[${s.index}\\]`).test(m.content || '')
}

function searchModeType(mode) {
  if (mode === 'hybrid' || mode === 'vector') return 'success'
  if (mode === 'fts') return 'info'
  return 'warning'
}

function searchModeLabel(mode) {
  if (mode === 'hybrid') return t('kb.hybrid')
  if (mode === 'vector') return t('kb.vector')
  if (mode === 'fts') return t('kb.fts')
  return t('kb.keyword')
}

const srcPreview = ref(null)      // {path, name, locator, snippet, type, content?, url?}
const srcPreviewLoading = ref(false)

async function openSourcePreview(s) {
  const path = s.file || s.file_path
  if (!path) return
  srcPreviewLoading.value = true
  const loc = s.locator || s.section || ''
  const snippet = s.snippet || s.text || ''
  const pm = /第\s*(\d+)\s*页/.exec(loc) || /\[第(\d+)页\]/.exec(snippet)
  const page = pm ? Number(pm[1]) : 0
  try {
    const r = await api('preview_file', path)
    if (r.type === 'pdf' && r.url && page > 0) {
      r.url += `#page=${page}`
    }
    srcPreview.value = {
      ...r,
      path,
      name: s.title || r.name || path.split(/[\\/]/).pop(),
      locator: loc,
      snippet: snippet.slice(0, 360),
    }
  } catch (e) {
    api('open_path', path)
  }
  srcPreviewLoading.value = false
}

function openFile(p) { api('open_path', p) }

loadFolders()
loadSessions()
onActivated(() => { loadFolders() })
</script>

<template>
  <div class="fb-page">
    <n-tabs v-model:value="tab" type="line">
      <!-- ================= 文件夹管理 ================= -->
      <n-tab-pane name="manage" :tab="t('kb.tabSources')">
        <n-card>
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" @click="addFolder">{{ t('kb.addFolder') }}</n-button>
              <n-text depth="3">{{ t('kb.support') }}</n-text>
            </n-space>

            <template v-for="f in folders" :key="f.id">
              <div v-if="indexing[f.id]" class="fb-scanbar">
                <div class="fb-scanbar-row">
                  <span class="fb-scanbar-dot"></span>
                  <span class="fb-scanbar-stage">{{ kbStageText(f.id) }}</span>
                  <span class="fb-scanbar-detail mono">{{ indexing[f.id].detail }}</span>
                  <span class="fb-scanbar-num mono">
                    {{ indexing[f.id].i }}/{{ indexing[f.id].n }} · {{ kbPct(f.id) }}%
                    <template v-if="indexing[f.id].eta"> · {{ t('kb.eta', { v: fmtEta(indexing[f.id].eta) }) }}</template>
                  </span>
                  <n-button size="tiny" quaternary type="error" @click="cancelIndex(f.id)">{{ t('kb.cancel') }}</n-button>
                </div>
                <n-progress type="line" :percentage="kbPct(f.id)" :show-indicator="false"
                  :height="5" :border-radius="3" processing />
              </div>
            </template>

            <n-data-table v-if="folders.length" :columns="folderColumns" :data="folders" size="small" />
            <n-empty v-else :description="t('kb.emptyFolders')" />
          </n-space>
        </n-card>

        <n-modal v-model:show="failModal" preset="card" style="width:640px"
                 :title="t('kb.failTitle', { n: failItems.length })">
          <n-text depth="3" style="font-size:12.5px">
            {{ t('kb.failHint') }}
          </n-text>
          <n-list v-if="failItems.length" style="margin-top:10px;max-height:52vh;overflow:auto">
            <n-list-item v-for="it in failItems" :key="it.path">
              <n-space vertical size="small" style="width:100%">
                <n-text style="font-size:12.5px;word-break:break-all">{{ it.path }}</n-text>
                <n-button size="tiny" secondary @click="api('open_path', it.path)">{{ t('kb.btnOpen') }}</n-button>
              </n-space>
            </n-list-item>
          </n-list>
          <n-empty v-else :description="t('kb.noFail')" style="margin:16px 0" />
        </n-modal>
      </n-tab-pane>

      <!-- ================= 问答 ================= -->
      <n-tab-pane name="ask" :tab="t('kb.tabAsk')">
        <div style="display:flex;gap:14px;height:calc(100vh - 220px);min-height:380px">
          <!-- 会话历史侧栏 -->
          <div style="width:210px;flex-shrink:0;border-right:1px solid var(--fb-border, rgba(127,127,127,.18));padding-right:10px;overflow:auto">
            <n-button size="small" block type="primary" secondary @click="newChat">{{ t('kb.newChat') }}</n-button>
            <div style="margin-top:8px">
              <div v-for="s in sessions" :key="s.id"
                @click="openSession(s)"
                :style="{
                  padding: '6px 8px', borderRadius: '6px', cursor: 'pointer',
                  marginBottom: '4px', fontSize: '12.5px',
                  background: currentSession === s.id ? 'color-mix(in srgb, var(--fb-accent, #18a058) 14%, transparent)' : 'transparent',
                }">
                <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                  :title="s.title">{{ s.title || t('kb.nullTitle') }}</div>
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span style="color:var(--fb-text-dim, #999);font-size:11px">{{ t('kb.msgs', { n: s.n_msgs }) }}</span>
                  <span style="color:#e06c75;font-size:11px;cursor:pointer"
                    @click.stop="deleteSession(s)">{{ t('kb.del') }}</span>
                </div>
              </div>
            </div>
          </div>
          <!-- 对话区（min-width:0 + min-height:0 防止长路径/多消息撑破弹性盒边界） -->
          <n-card style="flex:1;min-width:0" content-style="display:flex;flex-direction:column;height:100%;min-height:0">
            <div ref="chatBox" class="chat-scroll">
              <n-empty v-if="messages.length === 0" style="margin-top:60px"
                :description="t('kb.askEmpty')" />
              <div v-for="(m, i) in messages" :key="i" class="chat-bubble" :class="m.role">
                {{ m.content }}<span v-if="m.streaming" class="blink">▍</span>
                <div v-if="m.sources && m.sources.length" style="margin-top:8px;border-top:1px dashed rgba(127,127,127,.3);padding-top:6px">
                  <n-text depth="3" style="font-size:12px">{{ t('kb.sources') }}</n-text><br>
                  <span v-for="s in m.sources" :key="s.index" class="src-tag mono"
                    :class="{ cited: isSourceCited(m, s) }"
                    @click="openSourcePreview(s)"
                    :title="(isSourceCited(m, s) ? '✓ ' + t('kb.citedMark') + '\n' : '') + (s.snippet || s.file)">
                    [{{ s.index }}] {{ s.title || s.file.split(/[\\/]/).pop() }}<template v-if="s.locator"> · {{ s.locator }}</template><template v-if="s.score"> · {{ s.score }}</template>
                  </span>
                </div>
              </div>
            </div>
            <n-space align="center" :size="8" style="padding-top:10px;flex-shrink:0">
              <n-text depth="3" style="font-size:12px">{{ t('kb.scope') }}</n-text>
              <n-select v-model:value="scopeFolder" size="small" style="width:190px"
                :options="scopeFolderOptions" clearable :placeholder="t('kb.scopeAll')"
                @update:value="onScopeFolder" />
              <n-select v-if="scopeFolder" v-model:value="scopePath" size="small"
                style="width:260px" :options="scopeFileOptions" filterable clearable
                :placeholder="t('kb.scopeAllFiles')" />
            </n-space>
            <div style="display:flex;gap:8px;align-items:center;padding-top:12px;flex-shrink:0">
              <n-input v-model:value="question" :placeholder="t('kb.askPh')"
                style="flex:1;min-width:0" :disabled="asking" @keyup.enter="doAsk" />
              <n-button type="primary" :loading="asking" @click="doAsk">{{ t('kb.ask') }}</n-button>
              <n-button :disabled="asking || !question.trim()" @click="doSearch">{{ t('kb.searchOnly') }}</n-button>
            </div>
          </n-card>
        </div>
      </n-tab-pane>

      <!-- ================= 搜索结果 ================= -->
      <n-tab-pane v-if="searchResults" name="results" :tab="t('kb.tabResults')">
        <n-card>
          <n-alert :type="searchModeType(searchResults.mode)" :bordered="false">
            {{ searchModeLabel(searchResults.mode) }} ·
            {{ t('kb.results', { n: searchResults.results.length }) }}
          </n-alert>
          <n-list style="margin-top:12px">
            <n-list-item v-for="(r, i) in searchResults.results" :key="i">
              <n-space align="center" :size="8">
                <n-text class="mono" style="cursor:pointer;color:#5a9cf8"
                  @click="openSourcePreview({ file: r.file_path, text: r.text, section: r.section })">
                  {{ r.file_path }}
                </n-text>
                <n-tag v-if="r.section" size="tiny" :bordered="false">{{ r.section }}</n-tag>
              </n-space>
              <div style="margin-top:4px;opacity:.78">{{ (r.text || '').slice(0, 260) }}…</div>
            </n-list-item>
          </n-list>
        </n-card>
      </n-tab-pane>
    </n-tabs>

    <!-- 引用原文内联预览 -->
    <n-modal :show="!!srcPreview" @update:show="(v) => { if (!v) srcPreview = null }"
      preset="card" style="width:860px"
      :title="(srcPreview?.name || t('kb.previewTitle')) + (srcPreview?.locator ? ' · ' + srcPreview.locator : '')"
      content-style="max-height:78vh;overflow:auto">
      <n-spin :show="srcPreviewLoading">
        <n-alert v-if="srcPreview?.snippet" type="info" :bordered="false" style="margin-bottom:10px;font-size:12.5px">
          <span style="white-space:pre-wrap">{{ srcPreview.snippet }}</span>
        </n-alert>
        <iframe v-if="srcPreview?.type === 'pdf'" :src="srcPreview.url"
          style="width:100%;height:66vh;border:0;border-radius:6px"></iframe>
        <div v-else-if="srcPreview?.type === 'html'" class="fb-office" v-html="srcPreview.content"></div>
        <pre v-else-if="srcPreview?.type === 'text' || srcPreview?.type === 'markdown'" class="mono"
          style="white-space:pre-wrap;word-break:break-word;font-size:12.5px;line-height:1.6">{{ srcPreview.content }}</pre>
        <div v-else-if="srcPreview?.type === 'image'" class="fb-modal-image">
          <img :src="srcPreview.url" :alt="srcPreview.name" />
        </div>
        <n-empty v-else :description="srcPreview?.path || ''" />
      </n-spin>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="openFile(srcPreview?.path)">{{ t('kb.openSystem') }}</n-button>
          <n-button size="small" @click="api('open_location', srcPreview?.path)">{{ t('kb.openLocation') }}</n-button>
          <n-button size="small" @click="srcPreview = null">{{ t('kb.close') }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.blink { animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }
.src-tag.cited {
  border: 1px solid rgba(24, 160, 88, 0.55);
  background: rgba(24, 160, 88, 0.14);
  font-weight: 600;
}
.fb-office { font-size: 13px; line-height: 1.7; padding: 4px 2px; overflow-x: auto; }
.fb-office :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0 14px; font-size: 12.5px; }
.fb-office :deep(td), .fb-office :deep(th) { border: 1px solid rgba(127,127,127,.35); padding: 4px 8px; text-align: left; }
.fb-office :deep(th) { background: rgba(127,127,127,.12); font-weight: 600; }
</style>
