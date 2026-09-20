// 三套主题：清新自然 / 静谧靛蓝 / 暗色科技
// naive: Naive UI themeOverrides；vars: 注入根节点的 CSS 变量
//
// 设计原则（对齐成熟桌面产品的克制风格）：
// - 中性色打底，主色只做点缀（选中态、主按钮、进度）
// - 分层靠「1px 细边框 + 极轻阴影」，不靠重阴影/重渐变
// - 自绘标题栏颜色与窗口底色一致，和系统融为一体

const FONT = "'Segoe UI Variable Display', 'HarmonyOS Sans SC', 'MiSans', 'Segoe UI', 'Microsoft YaHei UI', sans-serif"

// 系统标题栏随主题着色（Win10 深色模式 / Win11 标题栏颜色）——frameless 模式下
// 不再有系统标题栏，保留此字段用于窗口 background_color 与非无边框回退。
export const THEMES = {
  jade: {
    key: 'jade',
    name: '清新自然',
    desc: '翡翠绿 · 白底通透',
    dark: false,
    swatch: 'linear-gradient(135deg,#34d399,#059669)',
    caption: { bg: '#f4f6f4', fg: '#1f2a25' },
    naive: {
      common: {
        primaryColor: '#0e9f6e', primaryColorHover: '#31b488',
        primaryColorPressed: '#057a55', primaryColorSuppl: '#0e9f6e',
        successColor: '#0e9f6e', borderRadius: '8px', borderRadiusSmall: '6px',
        fontFamily: FONT, bodyColor: '#f4f6f4', cardColor: '#ffffff',
        borderColor: 'rgba(17,24,39,.07)',
        textColor1: '#101826', textColor2: '#3d4a56', textColor3: '#7a8694',
        fontWeightStrong: '600',
      },
      Card: { borderRadius: '12px', paddingMedium: '16px 20px' },
      Input: { borderRadius: '8px' },
      Tag: { borderRadius: '999px', fontWeight: '500' },
      Menu: { borderRadius: '8px', itemHeight: '36px' },
      Button: { borderRadius: '8px', fontWeight: '500' },
      DataTable: { fontWeightStrong: '600' },
    },
    vars: {
      '--fb-accent': '#0e9f6e',
      '--fb-accent-2': '#34d399',
      '--fb-grad': 'linear-gradient(135deg,#10b981 0%,#059669 100%)',
      '--fb-logo-shadow': 'rgba(5,150,105,.30)',
      '--fb-titlebar-bg': '#f4f6f4',
      '--fb-titlebar-fg': '#3f4a44',
      '--fb-sidebar-bg': '#ffffff',
      '--fb-sidebar-border': 'rgba(17,24,39,.07)',
      '--fb-side-text': '#1f2a25',
      '--fb-side-text-dim': 'rgba(31,42,37,.52)',
      '--fb-body-bg': '#f4f6f4',
      '--fb-card-bg': '#ffffff',
      '--fb-border': 'rgba(17,24,39,.08)',
      '--fb-card-shadow': '0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.04)',
      '--fb-card-hover-shadow': '0 4px 6px rgba(16,24,40,.04), 0 10px 22px rgba(16,24,40,.08)',
      '--fb-hero-text': '#ffffff',
      '--fb-chip-bg': '#eef3f0',
      '--fb-chip-active': '#0e9f6e',
      '--fb-thumb-bg': '#f3f5f4',
    },
  },

  indigo: {
    key: 'indigo',
    name: '静谧靛蓝',
    desc: '靛蓝主色 · 深色侧栏',
    dark: false,
    menuInverted: true,
    caption: { bg: '#f3f5fa', fg: '#252b3b' },
    naive: {
      common: {
        primaryColor: '#4f46e5', primaryColorHover: '#6366f1',
        primaryColorPressed: '#4338ca', primaryColorSuppl: '#4f46e5',
        borderRadius: '8px', borderRadiusSmall: '6px',
        fontFamily: FONT, bodyColor: '#f3f5fa', cardColor: '#ffffff',
        borderColor: 'rgba(17,24,39,.07)',
        textColor1: '#101826', textColor2: '#3d4a56', textColor3: '#7a8694',
        fontWeightStrong: '600',
      },
      Card: { borderRadius: '12px', paddingMedium: '16px 20px' },
      Input: { borderRadius: '8px' },
      Tag: { borderRadius: '999px', fontWeight: '500' },
      Menu: { borderRadius: '8px', itemHeight: '36px' },
      Button: { borderRadius: '8px', fontWeight: '500' },
      DataTable: { fontWeightStrong: '600' },
    },
    vars: {
      '--fb-accent': '#4f46e5',
      '--fb-accent-2': '#818cf8',
      '--fb-grad': 'linear-gradient(135deg,#6366f1 0%,#4f46e5 100%)',
      '--fb-logo-shadow': 'rgba(79,70,229,.35)',
      '--fb-titlebar-bg': '#f3f5fa',
      '--fb-titlebar-fg': '#3d4459',
      '--fb-sidebar-bg': '#151b2c',
      '--fb-sidebar-border': 'rgba(255,255,255,.06)',
      '--fb-side-text': '#e5e9f5',
      '--fb-side-text-dim': 'rgba(229,233,245,.5)',
      '--fb-body-bg': '#f3f5fa',
      '--fb-card-bg': '#ffffff',
      '--fb-border': 'rgba(17,24,39,.08)',
      '--fb-card-shadow': '0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.04)',
      '--fb-card-hover-shadow': '0 4px 6px rgba(16,24,40,.04), 0 10px 22px rgba(16,24,40,.08)',
      '--fb-hero-text': '#ffffff',
      '--fb-chip-bg': '#eceef8',
      '--fb-chip-active': '#4f46e5',
      '--fb-thumb-bg': '#eef0f7',
    },
  },

  cyber: {
    key: 'cyber',
    name: '暗色科技',
    desc: '深色底 · 青色点缀',
    dark: true,
    menuInverted: true,
    caption: { bg: '#0b0e13', fg: '#aebac6' },
    naive: {
      common: {
        primaryColor: '#22d3ee', primaryColorHover: '#67e8f9',
        primaryColorPressed: '#06b6d4', primaryColorSuppl: '#22d3ee',
        borderRadius: '8px', borderRadiusSmall: '6px',
        fontFamily: FONT, bodyColor: '#0b0e13',
        cardColor: '#131822', modalColor: '#181f2c', popoverColor: '#181f2c',
        borderColor: 'rgba(255,255,255,.07)',
        textColor1: '#e8eef4', textColor2: '#b7c2cd', textColor3: '#74828f',
        fontWeightStrong: '600',
      },
      Card: { borderRadius: '12px', paddingMedium: '16px 20px' },
      Input: { borderRadius: '8px' },
      Tag: { borderRadius: '999px', fontWeight: '500' },
      Menu: {
        borderRadius: '8px', itemHeight: '36px',
        itemColorActive: 'rgba(34,211,238,.13)',
        itemColorActiveHover: 'rgba(34,211,238,.19)',
        itemColorHover: 'rgba(148,163,184,.08)',
      },
      Button: { borderRadius: '8px', fontWeight: '500' },
      DataTable: { fontWeightStrong: '600' },
    },
    vars: {
      '--fb-accent': '#22d3ee',
      '--fb-accent-2': '#67e8f9',
      '--fb-grad': 'linear-gradient(135deg,#0e7490 0%,#164e63 100%)',
      '--fb-logo-shadow': 'rgba(34,211,238,.30)',
      '--fb-titlebar-bg': '#0b0e13',
      '--fb-titlebar-fg': '#aebac6',
      '--fb-sidebar-bg': '#0e121a',
      '--fb-sidebar-border': 'rgba(255,255,255,.06)',
      '--fb-side-text': '#d5e0ea',
      '--fb-side-text-dim': 'rgba(213,224,234,.5)',
      '--fb-body-bg': '#0b0e13',
      '--fb-card-bg': '#131822',
      '--fb-border': 'rgba(255,255,255,.065)',
      '--fb-card-shadow': '0 1px 2px rgba(0,0,0,.28)',
      '--fb-card-hover-shadow': '0 6px 14px rgba(0,0,0,.36), 0 0 0 1px rgba(34,211,238,.12)',
      '--fb-hero-text': '#e0f7ff',
      '--fb-chip-bg': '#1a2333',
      '--fb-chip-active': '#22d3ee',
      '--fb-thumb-bg': '#141b28',
    },
  },
}

export function loadThemeKey() {
  const saved = localStorage.getItem('fb_theme')
  // 兼容旧的明暗开关
  if (!saved && localStorage.getItem('fb_dark') === '1') return 'cyber'
  if (saved === AUTO_KEY) return AUTO_KEY
  return THEMES[saved] ? saved : 'jade'
}

export function saveThemeKey(key) {
  localStorage.setItem('fb_theme', key)
}

// ---------- 跟随系统深浅色 ----------
// 保存的 key 可以为 'auto'：亮色解析为 jade，暗色解析为 cyber
export const AUTO_KEY = 'auto'

const media = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-color-scheme: dark)')
  : null

export function systemPrefersDark() {
  return media ? media.matches : false
}

export function onSystemThemeChange(cb) {
  if (media && media.addEventListener) media.addEventListener('change', cb)
}

export function resolveThemeKey(key, sysDark = systemPrefersDark()) {
  if (key === AUTO_KEY) return sysDark ? 'cyber' : 'jade'
  return THEMES[key] ? key : 'jade'
}

// 文件类别 → 徽章色相（低饱和 HSL，供扩展名徽章等使用）
export const CATEGORY_HUES = {
  文档: 226, 图片: 152, 视频: 268, 音频: 28, 压缩包: 18,
  安装包: 205, 代码: 174, 数据: 196, 字体: 320, 其他: 220,
}
