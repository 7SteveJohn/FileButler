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
    // 色板要跟实际主色一致：功能绿已调深到 #0a7d54，原先那块亮绿在应用里已不存在
    swatch: 'linear-gradient(135deg,#0a7d54,#0e9f6e)',
    caption: { bg: '#f4f6f4', fg: '#1f2a25' },
    naive: {
      common: {
        /* #0e9f6e 撑不起白字（3.35:1）也不够深到能印在自己的淡底上；
           同色相调深一档，四个不达标项一起解决，装饰用的 accent-2/渐变保持明亮。
           hover/pressed 只能往深走——再亮就掉出 AA（#0b8a5c 实测 4.37），
           所以这里是一整套"越交互越深"的色阶，而不是常规的 hover 变亮 */
        primaryColor: '#0a7d54', primaryColorHover: '#096e49',
        primaryColorPressed: '#075c40', primaryColorSuppl: '#0a7d54',
        successColor: '#0a7d54', borderRadius: '8px', borderRadiusSmall: '6px',
        fontFamily: FONT, bodyColor: '#f4f6f4', cardColor: '#ffffff',
        borderColor: 'rgba(17,24,39,.07)',
        textColor1: '#101826', textColor2: '#3d4a56', textColor3: '#5f6b78',
        fontWeightStrong: '600',
      },
      Card: { borderRadius: '12px', paddingMedium: '16px 20px' },
      Input: { borderRadius: '8px' },
      Tag: {
        borderRadius: '999px', fontWeight: '500',
        // naive 默认 warning/error 文字压在同色淡底上不到 2:1，压深文字色
        textColorWarning: '#8a5a00', textColorError: '#a8202b',
        textColorSuccess: '#0b6e3d', // 同上：naive 的 #18a05b 在淡绿底上只有 3.03
      },
      Menu: { borderRadius: '8px', itemHeight: '36px' },
      Button: { borderRadius: '8px', fontWeight: '500' },
      DataTable: { fontWeightStrong: '600' },
    },
    vars: {
      '--fb-accent': '#0a7d54',
      '--fb-accent-2': '#34d399',
      '--fb-grad': 'linear-gradient(135deg,#10b981 0%,#059669 100%)',
      '--fb-logo-shadow': 'rgba(5,150,105,.30)',
      '--fb-titlebar-bg': '#f4f6f4',
      '--fb-titlebar-fg': '#3f4a44',
      '--fb-sidebar-bg': '#ffffff',
      '--fb-sidebar-border': 'rgba(17,24,39,.07)',
      '--fb-side-text': '#1f2a25',
      '--fb-side-text-dim': 'rgba(31,42,37,.68)',
      /* 内容区文字：侧栏文字令牌是给深色侧栏用的，内容卡片不能复用
         （indigo 侧栏深、内容浅，复用近白的 side-text 就等于看不见） */
      '--fb-text': '#1f2a25',
      '--fb-text-dim': 'rgba(31,42,37,.68)',
      '--fb-body-bg': '#f4f6f4',
      '--fb-card-bg': '#ffffff',
      '--fb-border': 'rgba(17,24,39,.08)',
      '--fb-card-shadow': '0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.04)',
      '--fb-card-hover-shadow': '0 4px 6px rgba(16,24,40,.04), 0 10px 22px rgba(16,24,40,.08)',
      '--fb-hero-text': '#ffffff',
      '--fb-chip-bg': '#eef3f0',
      '--fb-chip-active': '#0a7d54',
      '--fb-thumb-bg': '#f3f5f4',
    },
  },

  indigo: {
    key: 'indigo',
    name: '静谧靛蓝',
    desc: '靛蓝主色 · 深色侧栏',
    dark: false,
    swatch: 'linear-gradient(135deg,#4f46e5,#818cf8)',
    menuInverted: true,
    caption: { bg: '#151b2c', fg: '#e5e9f5' },
    naive: {
      common: {
        primaryColor: '#4f46e5', primaryColorHover: '#6366f1',
        primaryColorPressed: '#4338ca', primaryColorSuppl: '#4f46e5',
        borderRadius: '8px', borderRadiusSmall: '6px',
        fontFamily: FONT, bodyColor: '#f3f5fa', cardColor: '#ffffff',
        borderColor: 'rgba(17,24,39,.07)',
        textColor1: '#101826', textColor2: '#3d4a56', textColor3: '#5f6b78',
        fontWeightStrong: '600',
      },
      Card: { borderRadius: '12px', paddingMedium: '16px 20px' },
      Input: { borderRadius: '8px' },
      Tag: {
        borderRadius: '999px', fontWeight: '500',
        // naive 默认 warning/error 文字压在同色淡底上不到 2:1，压深文字色
        textColorWarning: '#8a5a00', textColorError: '#a8202b',
        textColorSuccess: '#0b6e3d', // 同上：naive 的 #18a05b 在淡绿底上只有 3.03
      },
      Menu: { borderRadius: '8px', itemHeight: '36px' },
      Button: { borderRadius: '8px', fontWeight: '500' },
      DataTable: { fontWeightStrong: '600' },
    },
    vars: {
      '--fb-accent': '#4f46e5',
      '--fb-accent-2': '#818cf8',
      '--fb-grad': 'linear-gradient(135deg,#6366f1 0%,#4f46e5 100%)',
      '--fb-logo-shadow': 'rgba(79,70,229,.35)',
      '--fb-titlebar-bg': '#151b2c',
      '--fb-titlebar-fg': '#e5e9f5',
      '--fb-sidebar-bg': '#151b2c',
      '--fb-sidebar-border': 'rgba(255,255,255,.06)',
      '--fb-side-text': '#e5e9f5',
      '--fb-side-text-dim': 'rgba(229,233,245,.62)',
      '--fb-text': '#101826',
      '--fb-text-dim': 'rgba(16,24,38,.66)',
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
    swatch: 'linear-gradient(135deg,#22d3ee,#0e7490)',
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
      /* cyber 是深色主题：标签文字要往亮里走，naive 深色默认值本来就够，
         不能套用浅色主题那套"压深文字色"的做法（会反过来看不清）*/
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
      '--fb-side-text-dim': 'rgba(213,224,234,.62)',
      '--fb-text': '#e8eef4',
      '--fb-text-dim': 'rgba(232,238,244,.6)',
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
