<script setup>
// Ctrl+K 快速唤起：搜文件（FTS 现成）+ 页面/主题命令（Raycast 式）
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { api } from '../lib/bridge'
import { THEMES, saveThemeKey, CATEGORY_HUES } from '../lib/theme'

const show = ref(false)
const query = ref('')
const active = ref(0)
const files = ref([])
const inputEl = ref(null)
let debounceTimer = null

const PAGES = [
  { kind: 'page', key: 'dashboard', label: '概览', hint: '页面' },
  { kind: 'page', key: 'files', label: '文件', hint: '页面' },
  { kind: 'page', key: 'organizer', label: '文件整理', hint: '页面' },
  { kind: 'page', key: 'knowledge', label: '知识库', hint: '页面' },
  { kind: 'page', key: 'settings', label: '设置', hint: '页面' },
]
const THEME_CMDS = [
  { kind: 'theme', key: 'theme:auto', label: '切换主题：跟随系统', hint: '命令',
    swatch: 'linear-gradient(135deg,#f4f6f4 50%,#0b0e13 50%)' },
  ...Object.values(THEMES).map((t) => ({
    kind: 'theme', key: 'theme:' + t.key, label: '切换主题：' + t.name, hint: '命令', swatch: t.swatch,
  })),
]
const COMMANDS = [...PAGES, ...THEME_CMDS]

const cmdResults = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? COMMANDS.filter((c) => c.label.toLowerCase().includes(q)) : COMMANDS
})
const items = computed(() => [
  ...cmdResults.value,
  ...files.value.map((f) => ({ kind: 'file', key: f.path, label: f.name, hint: f.path })),
])

watch(query, () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(searchFiles, 150)
  active.value = 0
})

async function searchFiles() {
  const q = query.value.trim()
  if (!q) { files.value = []; return }
  try {
    const r = await api('quick_search', q, 8)
    const ql = q.toLowerCase()
    // 准度：名字以输入开头的排前面，其余按最近修改
    const items = (r.items || []).slice().sort((a, b) => {
      const al = a.name.toLowerCase().startsWith(ql) ? 0 : 1
      const bl = b.name.toLowerCase().startsWith(ql) ? 0 : 1
      return al - bl || (b.mtime || 0) - (a.mtime || 0)
    })
    files.value = items
  } catch (e) { files.value = [] }
}

function open() {
  show.value = true
  query.value = ''
  files.value = []
  active.value = 0
  nextTick(() => inputEl.value?.focus())
}
function close() { show.value = false }

function exec(item) {
  if (!item) return
  close()
  if (item.kind === 'page') {
    window.dispatchEvent(new CustomEvent('fb-goto', { detail: item.key }))
  } else if (item.kind === 'theme') {
    const k = item.key.slice(6)
    saveThemeKey(k)
    window.dispatchEvent(new CustomEvent('fb-theme', { detail: k }))
  } else if (item.kind === 'file') {
    api('open_path', item.key)
  }
}

function scrollActive() {
  nextTick(() => document.querySelector('.fb-ql-item.active')?.scrollIntoView({ block: 'nearest' }))
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    show.value ? close() : open()
    return
  }
  if (!show.value) return
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    active.value = Math.min(active.value + 1, items.value.length - 1)
    scrollActive()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = Math.max(active.value - 1, 0)
    scrollActive()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    exec(items.value[active.value])
  }
}

function onInputKeydown(e) {
  if (['ArrowDown', 'ArrowUp', 'Enter'].includes(e.key)) e.preventDefault()
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  // 全局热键直通：main.py 热键触发后发该事件，直接弹出搜索层
  window.addEventListener('fb-open-launcher', open)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('fb-open-launcher', open)
})
</script>

<template>
  <teleport to="body">
    <div v-if="show" class="fb-ql-backdrop" @click.self="close">
      <div class="fb-ql">
        <input ref="inputEl" v-model="query" class="fb-ql-input"
          placeholder="搜索文件，或输入命令…" spellcheck="false" @keydown="onInputKeydown" />
        <div class="fb-ql-list">
          <div v-for="(it, idx) in items" :key="it.kind + ':' + it.key"
            class="fb-ql-item" :class="{ active: idx === active }"
            @mouseenter="active = idx" @click="exec(it)">
            <span v-if="it.kind === 'theme'" class="fb-ql-swatch" :style="{ background: it.swatch }"></span>
            <svg v-else-if="it.kind === 'page'" class="fb-ql-ico" width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M1.5 4.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 1.8h6A1.5 1.5 0 0 1 15 6.3v5.2a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 2 11.5Z"
                stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
            </svg>
            <span v-else class="fb-ql-badge" :style="{ '--h': String(CATEGORY_HUES[it.category] || 220) }">{{ (it.key.split('.').pop() || '').slice(0, 4).toUpperCase() }}</span>
            <span class="fb-ql-label">{{ it.label }}</span>
            <span class="fb-ql-hint" :title="it.kind === 'file' ? it.hint : ''">{{ it.hint }}</span>
          </div>
          <div v-if="!items.length" class="fb-ql-empty">没有匹配的文件或命令</div>
        </div>
        <div class="fb-ql-foot">↑↓ 选择 · Enter 执行 · Esc 关闭</div>
      </div>
    </div>
  </teleport>
</template>
