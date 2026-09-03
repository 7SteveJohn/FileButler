// 三套主题：清新自然 / 静谧靛蓝 / 暗色科技
// naive: Naive UI themeOverrides；vars: 注入根节点的 CSS 变量

const FONT = "'Segoe UI Variable Display', 'HarmonyOS Sans SC', 'MiSans', 'Segoe UI', 'Microsoft YaHei UI', sans-serif"

export const THEMES = {
  jade: {
    key: 'jade',
    name: '清新自然',
    desc: '翡翠绿 · 白底通透',
    dark: false,
    swatch: 'linear-gradient(135deg,#34d399,#059669)',
    naive: {
      common: {
        primaryColor: '#059669', primaryColorHover: '#0ea371',
        primaryColorPressed: '#047857', primaryColorSuppl: '#059669',
        successColor: '#059669', borderRadius: '10px', borderRadiusSmall: '8px',
        fontFamily: FONT, bodyColor: '#f5f7f6', cardColor: '#ffffff',
      },
      Card: { borderRadius: '16px' },
      Input: { borderRadius: '10px' },
      Tag: { borderRadius: '999px' },
      Menu: { borderRadius: '10px', itemHeight: '42px' },
    },
    vars: {
      '--fb-accent': '#059669',
      '--fb-accent-2': '#34d399',
      '--fb-grad': 'linear-gradient(135deg,#34d399 0%,#059669 60%,#047857 100%)',
      '--fb-logo-shadow': 'rgba(5,150,105,.35)',
      '--fb-sidebar-bg': '#ffffff',
      '--fb-sidebar-border': '#e8ece9',
      '--fb-side-text': '#24312c',
      '--fb-side-text-dim': 'rgba(36,49,44,.55)',
      '--fb-body-bg': '#f5f7f6',
      '--fb-hero-text': '#ffffff',
      '--fb-chip-bg': '#eef5f1',
      '--fb-chip-active': '#059669',
      '--fb-card-hover-shadow': '0 8px 24px rgba(5,150,105,.12)',
      '--fb-thumb-bg': '#f2f4f3',
    },
  },

  indigo: {
    key: 'indigo',
    name: '静谧靛蓝',
    desc: '靛蓝主色 · 深色侧栏',
    dark: false,
    // 深色侧栏：菜单用 Naive 的 inverted 模式渲染（自带完整的深色态配色），
    // 不再手动补一堆 item 颜色——总有遗漏状态导致看不清
    menuInverted: true,
    naive: {
      common: {
        primaryColor: '#4f46e5', primaryColorHover: '#6366f1',
        primaryColorPressed: '#4338ca', primaryColorSuppl: '#4f46e5',
        borderRadius: '10px', borderRadiusSmall: '8px',
        fontFamily: FONT, bodyColor: '#f1f4f9', cardColor: '#ffffff',
      },
      Card: { borderRadius: '16px' },
      Input: { borderRadius: '10px' },
      Tag: { borderRadius: '999px' },
      Menu: { borderRadius: '10px', itemHeight: '42px' },
    },
    vars: {
      '--fb-accent': '#4f46e5',
      '--fb-accent-2': '#818cf8',
      '--fb-grad': 'linear-gradient(135deg,#818cf8 0%,#4f46e5 55%,#3730a3 100%)',
      '--fb-logo-shadow': 'rgba(79,70,229,.4)',
      '--fb-sidebar-bg': '#1c2333',
      '--fb-sidebar-border': '#273043',
      '--fb-side-text': '#e8ecf7',
      '--fb-side-text-dim': 'rgba(232,236,247,.55)',
      '--fb-body-bg': '#f1f4f9',
      '--fb-hero-text': '#ffffff',
      '--fb-chip-bg': '#eceef8',
      '--fb-chip-active': '#4f46e5',
      '--fb-card-hover-shadow': '0 8px 24px rgba(79,70,229,.12)',
      '--fb-thumb-bg': '#eef0f6',
    },
  },

  cyber: {
    key: 'cyber',
    name: '暗色科技',
    desc: '深色底 · 青色霓虹',
    dark: true,
    menuInverted: true,
    naive: {
      common: {
        primaryColor: '#22d3ee', primaryColorHover: '#67e8f9',
        primaryColorPressed: '#06b6d4', primaryColorSuppl: '#22d3ee',
        borderRadius: '10px', borderRadiusSmall: '8px',
        fontFamily: FONT, bodyColor: '#0b0e14',
        cardColor: '#121722', modalColor: '#161c29', popoverColor: '#1a2130',
      },
      Card: { borderRadius: '16px' },
      Input: { borderRadius: '10px' },
      Tag: { borderRadius: '999px' },
      Menu: {
        borderRadius: '10px', itemHeight: '42px',
        itemColorActive: 'rgba(34,211,238,.16)',
        itemColorActiveHover: 'rgba(34,211,238,.22)',
        itemColorHover: 'rgba(148,163,184,.10)',
      },
    },
    vars: {
      '--fb-accent': '#22d3ee',
      '--fb-accent-2': '#67e8f9',
      '--fb-grad': 'linear-gradient(135deg,#155e75 0%,#0e7490 45%,#164e63 100%)',
      '--fb-logo-shadow': 'rgba(34,211,238,.35)',
      '--fb-sidebar-bg': '#0d1119',
      '--fb-sidebar-border': '#1b2333',
      '--fb-side-text': '#dbe9f2',
      '--fb-side-text-dim': 'rgba(219,233,242,.55)',
      '--fb-body-bg': '#0b0e14',
      '--fb-hero-text': '#e0f7ff',
      '--fb-chip-bg': '#1a2333',
      '--fb-chip-active': '#22d3ee',
      '--fb-card-hover-shadow': '0 8px 24px rgba(34,211,238,.10)',
      '--fb-thumb-bg': '#161d2b',
    },
  },
}

export function loadThemeKey() {
  const saved = localStorage.getItem('fb_theme')
  // 兼容旧的明暗开关
  if (!saved && localStorage.getItem('fb_dark') === '1') return 'cyber'
  return THEMES[saved] ? saved : 'jade'
}

export function saveThemeKey(key) {
  localStorage.setItem('fb_theme', key)
}
