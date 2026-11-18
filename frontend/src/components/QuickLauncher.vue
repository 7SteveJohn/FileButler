<script setup>
// Ctrl+K 快速唤起：搜文件（FTS 现成）+ 页面/主题命令（Raycast 式）
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../lib/bridge'
import { THEMES, saveThemeKey, CATEGORY_HUES } from '../lib/theme'

const { t } = useI18n()
const show = ref(false)
const query = ref('')
const active = ref(0)
const files = ref([])
const inputEl = ref(null)
let debounceTimer = null

const commands = computed(() => [
  { kind: 'page', key: 'dashboard', label: t('nav.dashboard'), hint: t('ql.pageHint'), kw: 'dashboard overview 概览' },
  { kind: 'page', key: 'files', label: t('nav.files'), hint: t('ql.pageHint'), kw: 'files search 文件' },
  { kind: 'page', key: 'organizer', label: t('nav.organizer'), hint: t('ql.pageHint'), kw: 'organizer clean dedupe 文件整理' },
  { kind: 'page', key: 'knowledge', label: t('nav.knowledge'), hint: t('ql.pageHint'), kw: 'knowledge rag ask 知识库' },
  { kind: 'page', key: 'settings', label: t('nav.settings'), hint: t('ql.pageHint'), kw: 'settings config 设置' },
  { kind: 'theme', key: 'theme:auto', label: t('ql.switchTheme', { name: t('theme.auto') }), hint: t('ql.cmdHint'),
    swatch: 'linear-gradient(135deg,#f4f6f4 50%,#0b0e13 50%)', kw: 'theme auto dark light 主题 跟随系统' },
  ...Object.values(THEMES).map((th) => ({
    kind: 'theme', key: 'theme:' + th.key,
    label: t('ql.switchTheme', { name: t('theme.' + th.key) }),
    hint: t('ql.cmdHint'), swatch: th.swatch,
    kw: `theme ${th.key} ${th.name} 主题`,
  })),
])

const cmdResults = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q
    ? commands.value.filter((c) => c.label.toLowerCase().includes(q) || (c.kw && c.kw.toLowerCase().includes(q)))
    : commands.value
})
const items = computed(() => [
  ...cmdResults.value,
  ...files.value.map((f) => ({
    kind: 'file', key: f.path, label: f.name, hint: f.path,
    category: f.category, ext: f.ext,
  })),
])

watch(query, () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(searchFiles, 150)
  active.value = 0
})

let searchSeq = 0
async function searchFiles() {
  const q = query.value.trim()
  if (!q) { searchSeq++; files.value = []; return }
  const seq = ++searchSeq
  try {
    const r = await api('quick_search', q, 8)
    if (seq !== searchSeq) return
    const ql = q.toLowerCase()
    // 准度：前缀精确命中排最前，其余保留后端 FTS5 BM25 相关度顺序（ES2019 稳定排序）
    const list = (r.items || []).slice().sort((a, b) => {
      const al = (a.name || '').toLowerCase().startsWith(ql) ? 0 : 1
      const bl = (b.name || '').toLowerCase().startsWith(ql) ? 0 : 1
      return al - bl
    })
    files.value = list
  } catch (e) {
    if (seq === searchSeq) files.value = []
  }
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
          :placeholder="t('ql.ph')" spellcheck="false" @keydown="onInputKeydown" />
        <div class="fb-ql-list">
          <div v-for="(it, idx) in items" :key="it.kind + ':' + it.key"
            class="fb-ql-item" :class="{ active: idx === active }"
            @mouseenter="active = idx" @click="exec(it)">
            <span v-if="it.kind === 'theme'" class="fb-ql-swatch" :style="{ background: it.swatch }"></span>
            <svg v-else-if="it.kind === 'page'" class="fb-ql-ico" width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M1.5 4.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 1.8h6A1.5 1.5 0 0 1 15 6.3v5.2a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 2 11.5Z"
                stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
            </svg>
            <span v-else class="fb-ql-badge" :style="{ '--h': String(CATEGORY_HUES[it.category] || 220) }">{{ (it.ext || 'file').slice(0, 4).toUpperCase() }}</span>
            <span class="fb-ql-label">{{ it.label }}</span>
            <span class="fb-ql-hint" :title="it.kind === 'file' ? it.hint : ''">{{ it.hint }}</span>
          </div>
          <div v-if="!items.length" class="fb-ql-empty">{{ t('ql.empty') }}</div>
        </div>
        <div class="fb-ql-foot">{{ t('ql.foot') }}</div>
      </div>
    </div>
  </teleport>
</template>
