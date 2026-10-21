// 界面语言：localStorage 记住选择；首次启动跟随系统语言（中文系统才给中文）。
import { createI18n } from 'vue-i18n'
import zhCN from '../locales/zh-CN'
import en from '../locales/en'
import { api } from './bridge'

export const LANGS = [
  { key: 'zh-CN', label: '简体中文' },
  { key: 'en', label: 'English' },
]

function initial() {
  const saved = localStorage.getItem('fb.lang')
  if (saved === 'zh-CN' || saved === 'en') return saved
  return String(navigator.language || '').toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: initial(),
  fallbackLocale: 'zh-CN',
  messages: { 'zh-CN': zhCN, en },
})

export function currentLocale() {
  return i18n.global.locale.value
}

export function setLocale(key, persist = true) {
  if (key !== 'zh-CN' && key !== 'en') return
  i18n.global.locale.value = key
  localStorage.setItem('fb.lang', key)
  document.documentElement.setAttribute('lang', key)
  // pywebview 每次启动端口不同 → origin 变，localStorage 靠不住，必须回写后端
  if (persist) api('set_ui_state', 'lang', key).catch(() => {})
}
