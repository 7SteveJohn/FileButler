<script setup>
import { ref, computed, nextTick, onUnmounted, h } from 'vue'
import {
  NCard, NButton, NSpace, NInput, NText, NTag, NDataTable, NProgress,
  NTabs, NTabPane, NEmpty, NAlert, NPopover, NList, NListItem, useMessage, NSpin,
} from 'naive-ui'
import { api, on } from '../lib/bridge'

const message = useMessage()
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

async function addFolder() {
  const p = await api('pick_folder')
  if (!p) return
  await api('kb_add_folder', p)
  message.success('已添加，点击「索引」开始构建知识库')
  loadFolders()
}

async function removeFolder(id) {
  // auto 文件夹跟随监控目录，不允许在此手动移除
  const f = folders.value.find((x) => x.id === id)
  if (f && f.source === 'auto') {
    message.warning('自动知识源跟随「设置 → 监控目录」，请在那里移除')
    return
  }
  await api('kb_remove_folder', id)
  loadFolders()
}

async function startIndex(id) {
  const r = await api('kb_index', id)
  if (r.started) indexing.value[id] = { stage: 'scan', i: 0, n: 0, detail: '' }
}

const folderColumns = [
  { title: '路径', key: 'path', ellipsis: { tooltip: true }, render: (r) => h('span', null, [
      r.source === 'auto' ? h(NTag, { size: 'tiny', type: 'info', bordered: false,
        style: 'margin-right:6px' }, { default: () => '自动' }) : null,
      r.path,
    ]) },
  { title: '文件', key: 'n_files', width: 70 },
  { title: '分块', key: 'n_chunks', width: 70 },
  { title: '最近索引', key: 'last_index_at', width: 150, render: (r) => r.last_index_at ? new Date(r.last_index_at * 1000).toLocaleString() : '未索引' },
  { title: '操作', key: 'actions', width: 220, render: (r) => {
    const idx = indexing.value[r.id]
    return h_NSpace(r, idx)
  } },
]

// 单独抽出来避免 render 里写太多 h()
function h_NSpace(r, idx) {
  return h(NSpace, null, {
    default: () => [
      h(NButton, { size: 'small', type: 'primary', secondary: true, onClick: () => startIndex(r.id), loading: !!idx }, { default: () => '索引' }),
      h(NButton, { size: 'small', onClick: () => api('open_path', r.path) }, { default: () => '打开' }),
      h(NButton, { size: 'small', type: 'error', ghost: true, onClick: () => removeFolder(r.id) }, { default: () => '移除' }),
    ],
  })
}

let offs = []
offs.push(on('kb_index_start', (p) => { indexing.value[p.folder_id] = { stage: 'scan', i: 0, n: 0, detail: '' } }))
offs.push(on('kb_index_progress', (p) => { indexing.value[p.folder_id] = p }))
offs.push(on('kb_index_done', (p) => {
  delete indexing.value[p.folder_id]
  const r = p.result
  if (r && !r.error) {
    indexResult.value = r
    message.success(`索引完成：新增 ${r.indexed}，跳过 ${r.skipped}，清理 ${r.removed}，失败 ${r.failed}`)
  }
  loadFolders()
}))
offs.push(on('kb_index_error', (e) => {
  indexing.value = {}
  message.error(String(e))
}))

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
    const result = await api('ask', q, JSON.parse(JSON.stringify(history)))
    if (result.error) {
      assistantMsg.content = '❌ ' + result.error
    } else {
      assistantMsg.content = result.answer
      assistantMsg.sources = result.sources
      await api('qa_save_message', currentSession.value, 'assistant', result.answer, result.sources)
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

// ---------- 快速搜索 ----------
async function doSearch() {
  const q = question.value.trim()
  if (!q) return
  searchResults.value = null
  const r = await api('search', q, 10)
  searchResults.value = r
  tab.value = 'results'
}

function openFile(p) { api('open_path', p) }

loadFolders()
loadSessions()
</script>

<template>
  <div class="fb-page">
    <n-tabs v-model:value="tab" type="line">
      <!-- ================= 文件夹管理 ================= -->
      <n-tab-pane name="manage" tab="知识源">
        <n-card>
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" @click="addFolder">＋ 添加文件夹</n-button>
              <n-text depth="3">支持 PDF / Word / PPT / Excel / Markdown / TXT / CSV / 代码文件</n-text>
            </n-space>

            <template v-for="f in folders" :key="f.id">
              <n-alert v-if="indexing[f.id]" type="info" :bordered="false">
                <n-progress
                  :percentage="indexing[f.id].n ? Math.round(indexing[f.id].i / indexing[f.id].n * 100) : 0"
                  indicator-placement="inside" processing />
                <n-text class="mono" style="font-size:12px">
                  {{ { scan: '扫描文件', parse: '解析文档', embed: '生成向量', done: '完成' }[indexing[f.id].stage] || '' }}
                  {{ indexing[f.id].i }}/{{ indexing[f.id].n }} {{ indexing[f.id].detail }}
                </n-text>
              </n-alert>
            </template>

            <n-data-table v-if="folders.length" :columns="folderColumns" :data="folders" size="small" />
            <n-empty v-else description="还没有添加知识源文件夹" />
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- ================= 问答 ================= -->
      <n-tab-pane name="ask" tab="知识库问答">
        <div style="display:flex;gap:14px;height:calc(100vh - 220px)">
          <!-- 会话历史侧栏 -->
          <div style="width:210px;flex-shrink:0;border-right:1px solid var(--border-color,#e0e0e6);padding-right:10px;overflow:auto">
            <n-button size="small" block type="primary" secondary @click="newChat">＋ 新对话</n-button>
            <div style="margin-top:8px">
              <div v-for="s in sessions" :key="s.id"
                @click="openSession(s)"
                :style="{
                  padding: '6px 8px', borderRadius: '6px', cursor: 'pointer',
                  marginBottom: '4px', fontSize: '12.5px',
                  background: currentSession === s.id ? 'rgba(24,160,88,.12)' : 'transparent',
                }">
                <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                  :title="s.title">{{ s.title || '对话' }}</div>
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span style="color:#999;font-size:11px">{{ s.n_msgs }} 条</span>
                  <span style="color:#d99;font-size:11px;cursor:pointer"
                    @click.stop="deleteSession(s)">删除</span>
                </div>
              </div>
            </div>
          </div>
          <!-- 对话区 -->
          <n-card style="flex:1" content-style="display:flex;flex-direction:column;height:100%">
            <div ref="chatBox" class="chat-scroll">
              <n-empty v-if="messages.length === 0" style="margin-top:60px"
                description="基于你的本地文档回答问题，回答附带来源引用；对话自动保存" />
              <div v-for="(m, i) in messages" :key="i" class="chat-bubble" :class="m.role">
                {{ m.content }}<span v-if="m.streaming" class="blink">▍</span>
                <div v-if="m.sources && m.sources.length" style="margin-top:8px;border-top:1px dashed #ddd;padding-top:6px">
                  <n-text depth="3" style="font-size:12px">引用来源：</n-text><br>
                  <span v-for="s in m.sources" :key="s.index" class="src-tag mono"
                    @click="openFile(s.file)" :title="s.snippet">
                    [{{ s.index }}] {{ s.file.split(/[\\/]/).pop() }}<template v-if="s.score"> · {{ s.score }}</template>
                  </span>
                </div>
              </div>
            </div>
            <n-space style="padding-top:12px" :wrap="false">
              <n-input v-model:value="question" placeholder="问问你的知识库…（Enter 发送）"
                :disabled="asking" @keyup.enter="doAsk" />
              <n-button type="primary" :loading="asking" @click="doAsk">提问</n-button>
              <n-button :disabled="asking || !question.trim()" @click="doSearch">仅搜索</n-button>
            </n-space>
          </n-card>
        </div>
      </n-tab-pane>

      <!-- ================= 搜索结果 ================= -->
      <n-tab-pane v-if="searchResults" name="results" tab="搜索结果">
        <n-card>
          <n-alert :type="searchResults.mode === 'vector' ? 'success' : 'warning'" :bordered="false">
            {{ searchResults.mode === 'vector' ? '语义检索' : '关键词检索（向量模型不可用时的降级模式）' }} ·
            {{ searchResults.results.length }} 条结果
          </n-alert>
          <n-list style="margin-top:12px">
            <n-list-item v-for="(r, i) in searchResults.results" :key="i">
              <n-text class="mono" style="cursor:pointer;color:#5a9cf8" @click="openFile(r.file_path)">
                {{ r.file_path }}
              </n-text>
              <div style="margin-top:4px;color:#555">{{ r.text.slice(0, 260) }}…</div>
            </n-list-item>
          </n-list>
        </n-card>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.blink { animation: blink 1s step-start infinite; }
@keyframes blink { 50% { opacity: 0; } }
</style>
