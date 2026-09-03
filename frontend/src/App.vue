<script setup>
import { h, ref, computed, watchEffect, onUnmounted } from 'vue'
import {
  NConfigProvider, NMessageProvider, NLayout, NLayoutSider, NLayoutContent,
  NMenu, NText, NButton, NIcon, NDropdown,
  zhCN, dateZhCN, darkTheme,
} from 'naive-ui'
import { HomeOutline, FolderOpenOutline, BookOutline, SettingsOutline, ImagesOutline } from '@vicons/ionicons5'
import { THEMES, loadThemeKey, saveThemeKey } from './lib/theme'
import logoUrl from './assets/logo.png'
import Dashboard from './views/Dashboard.vue'
import Organizer from './views/Organizer.vue'
import Knowledge from './views/Knowledge.vue'
import Files from './views/Files.vue'
import Settings from './views/Settings.vue'

const view = ref('dashboard')
const collapsed = ref(false)

// 侧栏版本号：从后端读取（不再硬编码，避免发版忘改）
import { api } from './lib/bridge'
const appVer = ref('…')
api('app_version').then((r) => (appVer.value = r.version)).catch(() => (appVer.value = ''))

// ---------- 主题系统 ----------
const themeKey = ref(loadThemeKey())
const themeDef = computed(() => THEMES[themeKey.value] || THEMES.jade)
const naiveTheme = computed(() => (themeDef.value.dark ? darkTheme : null))

// 主题 CSS 变量挂在文档根节点：teleport 到 body 的组件（灯箱/弹窗）也能拿到主题色
watchEffect(() => {
  const root = document.documentElement
  for (const [k, v] of Object.entries(themeDef.value.vars)) {
    root.style.setProperty(k, v)
  }
})

function setTheme(k) {
  themeKey.value = k
  saveThemeKey(k)
}

// 设置页「外观」卡片也会改主题
window.addEventListener('fb-theme', (e) => { themeKey.value = e.detail })

// 自定义 render-label：当前主题前显示 ✓（n-dropdown 2.45 没有 show-check 属性）
function renderThemeLabel(option) {
  return h('span', { class: 'fb-theme-opt' }, [
    h('span', { class: 'fb-theme-dot', style: { background: option.swatch } }),
    h('span', { class: 'fb-theme-opt-text' }, `${option.name} · ${option.desc}`),
    h('span', { class: 'fb-theme-opt-check' }, option.key === themeKey.value ? '✓' : ' '),
  ])
}

const themeOptions = Object.values(THEMES).map((t) => ({
  key: t.key,
  swatch: t.swatch,
  name: t.name,
  desc: t.desc,
  label: `${t.name} · ${t.desc}`,
}))

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
      <div style="height:100%;background:var(--fb-body-bg)">
        <n-layout has-sider style="height:100%;background:transparent">
          <n-layout-sider
            class="fb-sider"
            bordered
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
              <n-dropdown :value="themeKey" :options="themeOptions" :render-label="renderThemeLabel"
                trigger="click" :show-arrow="false" @select="(k) => setTheme(k)">
                <div class="fb-theme-btn" :title="'当前主题：' + themeDef.name + '，点击切换'">
                  <span class="fb-theme-dot" :style="{ background: themeDef.swatch }"></span>
                  <span class="fb-theme-label">{{ collapsed ? '🎨' : themeDef.name }}</span>
                </div>
              </n-dropdown>
              <span v-if="!collapsed" class="fb-version">v{{ appVer }} · 全本地</span>
            </div>
          </n-layout-sider>
          <n-layout-content content-style="height:100%;overflow:auto" style="background:transparent">
            <!-- KeepAlive：切换页面不再销毁重建（每次重挂载要重拉搜索/目录树，
                 是"切到文件列表要等一会"的根因）。refreshKey 变化时仍强制重建。 -->
            <KeepAlive :max="10">
              <component :is="components[view]" :key="view + '-' + refreshKey" />
            </KeepAlive>
          </n-layout-content>
        </n-layout>
      </div>
    </n-message-provider>
  </n-config-provider>
</template>
