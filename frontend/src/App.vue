<script setup>
import { h, ref, computed, watch, watchEffect, nextTick, onUnmounted, onMounted } from 'vue'
import {
  NConfigProvider, NMessageProvider, NLayout, NLayoutSider, NLayoutContent,
  NMenu, NText, NButton, NIcon, NDropdown, NModal,
  zhCN, dateZhCN, darkTheme,
} from 'naive-ui'
import { HomeOutline, FolderOpenOutline, BookOutline, SettingsOutline, ImagesOutline } from '@vicons/ionicons5'
import { THEMES, AUTO_KEY, loadThemeKey, saveThemeKey, resolveThemeKey, systemPrefersDark, onSystemThemeChange } from './lib/theme'
import logoUrl from './assets/logo.png'
import QuickLauncher from './components/QuickLauncher.vue'
import Dashboard from './views/Dashboard.vue'
import Organizer from './views/Organizer.vue'
import Knowledge from './views/Knowledge.vue'
import Files from './views/Files.vue'
import Settings from './views/Settings.vue'

const _VIEWS = ['dashboard', 'files', 'organizer', 'knowledge', 'settings']
const view = ref('dashboard')
const collapsed = ref(false)

// 侧栏版本号：从后端读取（不再硬编码，避免发版忘改）
import { api, on } from './lib/bridge'
const appVer = ref('…')
api('app_version').then((r) => (appVer.value = r.version)).catch(() => (appVer.value = ''))

// ---------- 主题系统（支持「跟随系统」） ----------
// 注意：pywebview 每次启动随机端口 → localStorage origin 变化，偏好必须
// 存后端（ui_state）。localStorage 只作为本会话的即时缓存。
const themeKey = ref(loadThemeKey())
const sysDark = ref(systemPrefersDark())
onSystemThemeChange((e) => { sysDark.value = e.matches })
const themeDef = computed(() => THEMES[resolveThemeKey(themeKey.value, sysDark.value)])
const naiveTheme = computed(() => (themeDef.value.dark ? darkTheme : null))

// 后端恢复 + 变更持久化
api('get_ui_state').then(({ state }) => {
  if (!state) return
  if (state.theme) themeKey.value = state.theme
  if (state.last_view && _VIEWS.includes(state.last_view)) view.value = state.last_view
  if (state.sider_collapsed != null) collapsed.value = state.sider_collapsed === '1'
  showOnboarding.value = state.onboarded !== '1'
}).catch(() => {})

watch(themeKey, (k) => { saveThemeKey(k); api('set_ui_state', 'theme', k).catch(() => {}) })
watch(view, (v) => { localStorage.setItem('fb_last_view', v); api('set_ui_state', 'last_view', v).catch(() => {}) })
watch(collapsed, (c) => {
  localStorage.setItem('fb_sider_collapsed', c ? '1' : '0')
  api('set_ui_state', 'sider_collapsed', c ? '1' : '0').catch(() => {})
})

// 主题 CSS 变量挂在文档根节点：teleport 到 body 的组件（灯箱/弹窗）也能拿到主题色
watchEffect(() => {
  const root = document.documentElement
  for (const [k, v] of Object.entries(themeDef.value.vars)) {
    root.style.setProperty(k, v)
  }
})

// 窗口 background_color 与后端记住的启动底色同步（frameless 下用于窗口边缘/启动帧）
watchEffect(() => {
  const t = themeDef.value
  const cap = t.caption || { bg: t.vars['--fb-body-bg'], fg: t.vars['--fb-side-text'] }
  api('set_titlebar_theme', !!t.dark, cap.bg, cap.fg).catch(() => {})
})

function setTheme(k) {
  if (k === AUTO_KEY) sysDark.value = resolveThemeKey(AUTO_KEY) === 'cyber'
  themeKey.value = k
}

// 设置页「外观」卡片也会改主题
window.addEventListener('fb-theme', (e) => {
  if (e.detail === AUTO_KEY) setTheme(AUTO_KEY)
  else themeKey.value = e.detail
})

// ---------- 后台编目活动指示器（标题栏） ----------
const scanning = ref(false)
on('lib_scan_start', () => { scanning.value = true })
on('lib_scan_done', () => { scanning.value = false })
on('lib_scan_error', () => { scanning.value = false })

// ---------- 页面滚动位置记忆（每页独立） ----------
// out-in 模式下新页在 leave 结束后才挂载，必须在 after-enter 恢复才有效
const scrollEl = ref(null)
const scrollMap = {}
const pendingView = ref(null)
watch(view, (nv, ov) => {
  if (ov) scrollMap[ov] = scrollEl.value ? scrollEl.value.scrollTop : 0
  pendingView.value = nv
})
function restoreScroll() {
  const v = pendingView.value
  if (v && scrollEl.value) scrollEl.value.scrollTop = scrollMap[v] || 0
}

// ---------- 首次运行引导（标记存后端，跨重启稳定） ----------
const themeList = Object.values(THEMES)
const showOnboarding = ref(false)
function finishOnboarding() {
  showOnboarding.value = false
  api('set_ui_state', 'onboarded', '1').catch(() => {})
}

// ---------- 自绘标题栏（frameless 窗口） ----------
// 整条栏是 pywebview 拖拽区；logo/文字 pointer-events:none 保证 mousedown
// 直接命中栏本身（pywebview 的 drag_region_direct_target_only 只认直接命中）。
const maximized = ref(false)

function refreshMaximized() {
  api('win_state').then((r) => { maximized.value = !!r.maximized }).catch(() => {})
}

function winMin() { api('win_minimize').catch(() => {}) }

function winToggleMax() {
  api('win_toggle_maximize').then((r) => { if (r.ok) maximized.value = !!r.maximized }).catch(() => {})
}

function winClose() { api('win_close').catch(() => {}) }

function onTitleDblclick(e) {
  if (e.target.closest('.fb-titlebar-actions')) return
  winToggleMax()
}

onMounted(refreshMaximized)

// 自定义 render-label：当前主题前显示 ✓（n-dropdown 2.45 没有 show-check 属性）
function renderThemeLabel(option) {
  return h('span', { class: 'fb-theme-opt' }, [
    h('span', { class: 'fb-theme-dot', style: { background: option.swatch } }),
    h('span', { class: 'fb-theme-opt-text' }, `${option.name} · ${option.desc}`),
    h('span', { class: 'fb-theme-opt-check' }, option.key === themeKey.value ? '✓' : ' '),
  ])
}

const themeOptions = [
  { key: AUTO_KEY, swatch: 'linear-gradient(135deg,#f4f6f4 50%,#0b0e13 50%)',
    name: '跟随系统', desc: '自动切换明暗', label: '跟随系统 · 自动切换明暗' },
  ...Object.values(THEMES).map((t) => ({
    key: t.key,
    swatch: t.swatch,
    name: t.name,
    desc: t.desc,
    label: `${t.name} · ${t.desc}`,
  })),
]

function renderIcon(icon) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const menuOptions = [
  { label: '概览', key: 'dashboard', icon: renderIcon(HomeOutline) },
  { label: '文件', key: 'files', icon: renderIcon(ImagesOutline) },
  { label: '文件整理', key: 'organizer', icon: renderIcon(FolderOpenOutline) },
  { label: '知识库', key: 'knowledge', icon: renderIcon(BookOutline) },
  { label: '设置', key: 'settings', icon: renderIcon(SettingsOutline) },
]

// 标题栏面包屑：当前页面名
const VIEW_LABELS = { dashboard: '概览', files: '文件', organizer: '文件整理', knowledge: '知识库', settings: '设置' }
const viewLabel = computed(() => VIEW_LABELS[view.value] || 'FileButler')

const components = { dashboard: Dashboard, files: Files, organizer: Organizer, knowledge: Knowledge, settings: Settings }

// 全局状态刷新事件
const refreshKey = ref(0)
function onGlobalRefresh() { refreshKey.value++ }
window.addEventListener('fb-refresh', onGlobalRefresh)
window.addEventListener('fb-goto', (e) => { view.value = e.detail })
onUnmounted(() => {
  window.removeEventListener('fb-refresh', onGlobalRefresh)
})

// 接收后端 SendTo/右键 文件入库通知：dispatch 事件让 Files.vue 处理
// （App.vue 顶层不能 useMessage——Provider 还没渲染会 inject 失败导致白屏）
window.__fbSendToIngest = (names) => {
  if (!Array.isArray(names) || !names.length) return
  window.dispatchEvent(new CustomEvent('fb-goto', { detail: 'files' }))
  const text = names.length === 1
    ? '已加入文件库：' + names[0]
    : `已加入 ${names.length} 个文件到文件库`
  window.dispatchEvent(new CustomEvent('fb-toast', { detail: { type: 'success', text } }))
}
</script>

<template>
  <n-config-provider :locale="zhCN" :date-locale="dateZhCN"
    :theme="naiveTheme" :theme-overrides="themeDef.naive" style="height:100%">
    <n-message-provider>
      <div class="fb-root" :class="{ 'fb-dark': themeDef.dark }">
        <!-- 自绘标题栏（ZCode 风格）：左活动指示 / 中面包屑标题 / 右工具图标 + 窗口控制。
             刻意不放 logo：侧栏品牌区已有同一个图标 + 应用名，frameless 窗口里两处并排会重复 -->
        <div class="fb-titlebar pywebview-drag-region" @dblclick="onTitleDblclick">
          <div class="fb-tb-left">
            <span v-if="scanning" class="fb-tb-activity" title="正在后台编目文件库">
              <span class="fb-tb-spin"></span>编目中
            </span>
          </div>
          <div class="fb-tb-center">
            <svg class="fb-tb-crumb-ico" width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M1.5 4.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 1.8h6A1.5 1.5 0 0 1 15 6.3v5.2a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 2 11.5Z"
                    stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
            </svg>
            <span class="fb-tb-crumb">{{ viewLabel }}</span>
          </div>
          <div class="fb-tb-right">
            <n-dropdown :value="themeKey" :options="themeOptions" :render-label="renderThemeLabel"
              trigger="click" :show-arrow="false" @select="(k) => setTheme(k)">
              <button class="fb-tb-btn" :title="'主题：' + themeDef.name">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M8 1.8a6.2 6.2 0 1 0 0 12.4c.9 0 1.4-.6 1.4-1.3 0-.4-.2-.7-.4-1-.2-.3-.4-.6-.4-1 0-.7.6-1.3 1.4-1.3h1.6a2.9 2.9 0 0 0 2.9-2.9A6.2 6.2 0 0 0 8 1.8Z" stroke="currentColor" stroke-width="1.2" />
                  <circle cx="5.2" cy="6.4" r="1" fill="currentColor" />
                  <circle cx="8.6" cy="4.6" r="1" fill="currentColor" />
                  <circle cx="11.4" cy="6.9" r="1" fill="currentColor" />
                </svg>
              </button>
            </n-dropdown>
            <span class="fb-tb-sep"></span>
            <button class="fb-tb-btn fb-win-btn" title="最小化" @click="winMin">
              <svg width="10" height="10" viewBox="0 0 10 10"><path d="M0 5h10" stroke="currentColor" stroke-width="1" /></svg>
            </button>
            <button class="fb-tb-btn fb-win-btn" :title="maximized ? '还原' : '最大化'" @click="winToggleMax">
              <svg v-if="!maximized" width="10" height="10" viewBox="0 0 10 10">
                <rect x="0.5" y="0.5" width="9" height="9" rx="1" fill="none" stroke="currentColor" stroke-width="1" />
              </svg>
              <svg v-else width="10" height="10" viewBox="0 0 10 10">
                <rect x="0.5" y="2.5" width="7" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1" />
                <path d="M2.5 2.5v-2h7v7h-2" fill="none" stroke="currentColor" stroke-width="1" />
              </svg>
            </button>
            <button class="fb-tb-btn fb-win-btn fb-win-close" title="隐藏到托盘" @click="winClose">
              <svg width="10" height="10" viewBox="0 0 10 10"><path d="M0 0l10 10M10 0L0 10" stroke="currentColor" stroke-width="1" /></svg>
            </button>
          </div>
        </div>

        <n-layout has-sider class="fb-shell">
          <n-layout-sider
            class="fb-sider"
            :bordered="false"
            collapse-mode="width"
            :collapsed-width="64"
            :width="200"
            :collapsed="collapsed"
            show-trigger
            @collapse="collapsed = true"
            @expand="collapsed = false"
          >
            <div class="fb-brand">
              <img :src="logoUrl" class="fb-logo-img" alt="FileButler" />
              <div v-if="!collapsed">
                <div class="fb-brand-name">FileButler</div>
                <div class="fb-brand-sub">本地智能文件管家</div>
              </div>
            </div>
            <n-menu
              :value="view"
              :options="menuOptions"
              :collapsed="collapsed"
              :collapsed-width="64"
              :collapsed-icon-size="20"
              :inverted="!!themeDef.menuInverted"
              @update:value="(k) => (view = k)"
            />
            <div class="fb-side-footer">
              <span v-if="!collapsed" class="fb-version">v{{ appVer }} · 全本地</span>
            </div>
          </n-layout-sider>
          <n-layout-content content-style="height:100%;overflow:auto" style="background:transparent">
            <!-- KeepAlive + 过渡：切页保留状态，带轻量淡入动效；
                 滚动容器自己管理（各页滚动位置独立保存/恢复） -->
            <div ref="scrollEl" class="fb-view-scroll">
              <transition name="fb-view" mode="out-in" @after-enter="restoreScroll">
                <KeepAlive :max="10">
                  <component :is="components[view]" :key="view + '-' + refreshKey" />
                </KeepAlive>
              </transition>
            </div>
          </n-layout-content>
        </n-layout>

        <!-- Ctrl+K 快速唤起（全局） -->
        <QuickLauncher />

        <!-- 首次运行引导 -->
        <n-modal :show="showOnboarding" preset="card" style="width:520px"
          :mask-closable="false" :closable="false" title="欢迎使用 FileButler">
          <n-space vertical size="large">
            <n-space align="center">
              <img :src="logoUrl" style="width:44px;height:44px;border-radius:12px" alt="" />
              <n-text style="font-size:14px">本地智能文件管家 —— 自动编目、秒级搜索、AI 整理与问答</n-text>
            </n-space>
            <n-space vertical size="small">
              <n-text depth="2">· 自动编目：默认索引所有盘符与常用目录，实时监控新增</n-text>
              <n-text depth="2">· AI 能力：配合本机 Ollama 或云端 API，语义搜索与知识库问答</n-text>
              <n-text depth="2">· 一切本地：文件数据零上传，可随时在设置中迁移与备份</n-text>
            </n-space>
            <div>
              <n-text depth="3" style="font-size:12px">先挑一个顺眼的主题（可在左下角随时更换）：</n-text>
              <n-space :size="10" style="margin-top:8px">
                <div v-for="t in themeList" :key="t.key" class="theme-card"
                  style="width:140px" :class="{ active: themeKey === t.key }" @click="setTheme(t.key)">
                  <div class="theme-preview" :style="{ background: t.naive.common.bodyColor }">
                    <div class="tp-side" :style="{ background: t.vars['--fb-sidebar-bg'] }">
                      <div class="tp-logo" :style="{ background: t.swatch }"></div>
                      <div class="tp-menu"></div>
                      <div class="tp-menu"></div>
                    </div>
                    <div class="tp-main">
                      <div class="tp-hero" :style="{ background: t.swatch }"></div>
                      <div class="tp-line"></div>
                    </div>
                  </div>
                  <div class="theme-card-body"><b>{{ t.name }}</b></div>
                </div>
              </n-space>
            </div>
            <n-button type="primary" block @click="finishOnboarding">开始使用</n-button>
          </n-space>
        </n-modal>
      </div>
    </n-message-provider>
  </n-config-provider>
</template>
