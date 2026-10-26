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

// 后端分类是中文原值（文档/图片/…），英文界面下要翻过去。
// 放在 lib 里是为了 Dashboard 和 Files 共用——之前只有 Files 的筛选行翻了，
// 列表和概览页那几处直接渲染原值，切到 English 还是一屏中文。
const CAT_KEY = {
  文档: 'doc', 图片: 'image', 视频: 'video', 音频: 'audio', 压缩包: 'archive',
  安装包: 'installer', 代码: 'code', 数据: 'data', 字体: 'font', 其他: 'other',
}

export function catLabel(c) {
  const key = CAT_KEY[c]
  return key ? i18n.global.t('files.cat.' + key) : c
}

// 子分类只有内置规则那几种中文写法，规则表里也会直接露出来。
const SUB_KEY = {
  快捷方式: 'shortcut', 思维导图: 'map', 文本: 'text', 日志: 'log',
  电子书: 'ebook', 笔记: 'note', 配置: 'config',
}

export function subLabel(s) {
  const key = SUB_KEY[s]
  return key ? i18n.global.t('files.sub.' + key) : s
}

// 后端模型状态带 code + args，界面按 code 取本地化文案；
// 老版本后端只给中文 reason，取不到 code 时原样显示，不至于空着。
export function statusText(s, fallback = '') {
  if (!s) return fallback
  const key = s.code ? 'status.' + s.code : ''
  if (key && i18n.global.te(key)) return i18n.global.t(key, s.args || {})
  return s.reason || fallback
}

export function setLocale(key, persist = true) {
  if (key !== 'zh-CN' && key !== 'en') return
  i18n.global.locale.value = key
  localStorage.setItem('fb.lang', key)
  document.documentElement.setAttribute('lang', key)
  // pywebview 每次启动端口不同 → origin 变，localStorage 靠不住，必须回写后端
  if (persist) api('set_ui_state', 'lang', key).catch(() => {})
}
