// 仅开发模式加载（vite.config 的 serve 插件注入）：模拟 pywebview 桥，供浏览器里做 UI 视觉检查。
// 打包构建不会包含此文件。
(() => {
  if (window.pywebview) return
  const IMG = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp']
  const files = []
  const names = ['海边日落.jpg', '团队合照.png', '发票截图.jpg', '架构图.png', '产品原型.jpeg',
    '白板照片.jpg', '旅行相册.webp', '代码截图.png', '头像.jpg', '壁纸.png', '二维码.jpg',
    '手绘草图.png', '菜单图.jpg', 'logo.png', '证书扫描.jpg', 'PPT配图.png', '数据图表.png',
    '房间照片.jpg', '宠物照.jpg', '风景图.jpeg', '证件照.jpg', '图标素材.png', '菜单截图.jpg', '花园.png']
  names.forEach((n, i) => files.push({
    path: 'C:\\Users\\SevenJohn\\Pictures\\相册\\' + n, name: n,
    ext: n.split('.').pop(), category: '图片',
    size: 120000 + i * 9999, mtime: Date.now() / 1000 - i * 3600,
  }))
  const docs = ['年度总结报告.pdf', '项目计划书.md', '报销制度.docx', '会议纪要.txt',
    '数据表.xlsx', '读书笔记.md', '部署手册.pdf', '需求列表.csv']
  docs.forEach((n, i) => files.push({
    path: 'C:\\Users\\SevenJohn\\Documents\\工作\\' + n, name: n,
    ext: n.split('.').pop(), category: '文档',
    size: 240000 + i * 7777, mtime: Date.now() / 1000 - i * 7200,
  }))

  const favs = [
    { path: 'C:\\Users\\SevenJohn\\Documents\\工作\\报销制度.docx',
      note: '每月 15 号前提交，逾期不受理', created_at: Date.now() / 1000 - 7200 },
    { path: 'C:\\Users\\SevenJohn\\Pictures\\相册\\海边日落.jpg', note: '', created_at: Date.now() / 1000 - 3600 },
  ]

  const MOCK = {
    get_status: () => ({
      ollama: { running: true, version: '0.32.13',
        models: ['qwen-35b-iq3m:latest', 'bge-m3:latest'], model_info: {},
        host: 'http://127.0.0.1:11434', chat_model: 'qwen-35b-iq3m:latest',
        chat_ready: true, embed_ready: true },
      chat_model: 'qwen-35b-iq3m:latest', embed_model: 'bge-m3',
      kb: { files: 156, chunks: 6297, folders: 5, operations: 23,
        db_path: 'C:\\Users\\SevenJohn\\AppData\\Roaming\\FileButler\\filebutler.db' },
      images_by_year: '1', auto_kb: true, img_semantic: false,
      library: { files: 438, total_size: 11.2e9, categories: [
        { category: '图片', n: 121, size: 3.4e9 }, { category: '文档', n: 93, size: 1.1e9 },
        { category: '数据', n: 72, size: 0.4e9 }, { category: '代码', n: 61, size: 0.06e9 },
        { category: '其他', n: 57, size: 2.1e9 }, { category: '安装包', n: 25, size: 4.0e9 }] },
      thumb_base: 'http://localhost:5173', watching: 6,
    }),
    weekly_report: () => ({ week_start: '2026-08-17', data: {
      new_count: 37, new_size: 2.14e9, total_files: 438, total_size: 11.2e9,
      by_category: { 文档: { n: 12, size: 1.2e9 }, 图片: { n: 9, size: 0.8e9 }, 安装包: { n: 2, size: 0.1e9 } },
      new_sample: files.slice(24).concat(files.slice(0, 4))
        .map((f) => ({ path: f.path, name: f.name, category: f.category, size: f.size })),
      big_files: [
        { path: 'D:\\大文件\\游戏安装包.exe', name: '游戏安装包.exe', category: '安装包', size: 8.4e9 },
        { path: 'C:\\Users\\SevenJohn\\Videos\\录屏.mp4', name: '录屏.mp4', category: '视频', size: 2.1e9 }],
    } }),
    browse_files: (query, category, isImage, offset, limit, sortBy, sortOrder, tagIds, mFrom, mTo) => {
      let items = files
      if (category) items = items.filter((f) => f.category === category)
      if (isImage) items = items.filter((f) => IMG.includes(f.ext))
      if (mFrom != null) items = items.filter((f) => f.mtime >= mFrom)
      if (mTo != null) items = items.filter((f) => f.mtime < mTo)
      const by = sortBy || 'mtime'
      const dir = sortOrder === 'asc' ? 1 : -1
      items = items.slice().sort((a, b) => (a[by] > b[by] ? 1 : -1) * dir)
      const n = limit || 120
      const o = offset || 0
      return { total: items.length, items: items.slice(o, o + n), total_capped: false }
    },
    global_search: (q) => ({ query: q, filename: { total: 3, items: files.slice(0, 3) },
      content: { mode: 'vector', results: [
        { file_path: 'C:\\Users\\SevenJohn\\Documents\\工作\\报销制度.docx',
          text: '第六条 员工出差需提前三个工作日在 OA 系统提交出差申请。报销单需在出差返回后 15 天内提交，逾期不予受理。', score: 0.66 },
        { file_path: 'C:\\Users\\SevenJohn\\Documents\\工作\\项目计划书.md',
          text: '第三季度目标：完成智能分类引擎，支持模糊文件归类。', score: 0.52 }],
        images: [{ type: 'image', file_path: files[0].path, text: '海边日落照片', descr: '海边日落', ocr: '', score: 0.71 }] } }),
    preview_file: (p) => {
      const ext = p.split('.').pop()
      const type = p.endsWith('.md') ? 'markdown' : (p.endsWith('.pdf') ? 'pdf' : (IMG.includes(ext) ? 'image' : 'text'))
      return { type, content: '# 项目计划书\n\n## 三季度目标\n完成智能分类引擎，支持模糊文件归类。\n\n- 规则引擎兜底\n- AI 批量分类',
        name: p.split('\\').pop(), url: 'http://localhost:5173/preview?p=' + encodeURIComponent(p), path: p }
    },
    kb_folders: () => ({ folders: [
      { id: 1, path: 'C:\\Users\\SevenJohn\\Desktop', source: 'auto', last_index_at: Date.now() / 1000 - 3600, n_files: 156, n_chunks: 4492 },
      { id: 2, path: 'C:\\Users\\SevenJohn\\Documents', source: 'auto', last_index_at: Date.now() / 1000 - 7200, n_files: 93, n_chunks: 1102 },
      { id: 3, path: 'D:\\工作资料\\产品文档', source: 'manual', last_index_at: Date.now() / 1000 - 86400, n_files: 42, n_chunks: 703 }] }),
    qa_list_sessions: () => ({ sessions: [
      { id: 1, title: '报销制度要注意什么', updated_at: Date.now() / 1000 - 3600, n_msgs: 4 },
      { id: 2, title: '项目排期梳理', updated_at: Date.now() / 1000 - 86400, n_msgs: 6 }] }),
    qa_messages: () => ({ messages: [
      { role: 'user', content: '出差住宿报销标准是多少？' },
      { role: 'assistant', content: '根据报销制度：一线城市每晚 500 元，其他城市每晚 400 元。',
        sources: [{ index: 1, file: 'C:\\Users\\SevenJohn\\Documents\\工作\\报销制度.docx', score: 0.66, snippet: '住宿标准：一线城市每晚 500 元' }] }] }),
    watch_roots: () => ({ roots: [
      { id: 1, path: 'C:\\Users\\SevenJohn\\Desktop', label: 'Desktop', n_files: 156, enabled: 1 },
      { id: 2, path: 'C:\\Users\\SevenJohn\\Documents', label: 'Documents', n_files: 93, enabled: 1 },
      { id: 3, path: 'C:\\Users\\SevenJohn\\Downloads', label: 'Downloads', n_files: 38, enabled: 1 },
      { id: 4, path: 'C:\\Users\\SevenJohn\\Pictures', label: 'Pictures', n_files: 121, enabled: 1 }] }),
    list_models_detail: () => ({ running: true, chat_model: 'qwen-35b-iq3m:latest', models: [
      { name: 'qwen-35b-iq3m:latest', size: 15e9, modified: '2026-08-15', is_chat: true, is_embed: false, is_vl: false },
      { name: 'bge-m3:latest', size: 1.2e9, modified: '2026-08-16', is_chat: false, is_embed: true, is_vl: false },
      { name: 'qwen36-local:latest', size: 11e9, modified: '2026-08-14', is_chat: false, is_embed: false, is_vl: false }] }),
    list_backups: () => ({ backups: [
      { file: 'filebutler-20260816-223000.db', path: 'C:\\...\\backups\\filebutler-20260816-223000.db', size: 34e6, mtime: Date.now() / 1000 - 86400 },
      { file: 'filebutler-20260815-223000.db', path: 'C:\\...\\backups\\filebutler-20260815-223000.db', size: 33e6, mtime: Date.now() / 1000 - 172800 }] }),
    data_dir_status: () => ({ current: 'C:\\Users\\SevenJohn\\AppData\\Roaming\\FileButler',
      default_dir: 'C:\\Users\\SevenJohn\\AppData\\Roaming\\FileButler', is_default: true,
      total_size: 35.6e6, db_path: 'C:\\Users\\SevenJohn\\AppData\\Roaming\\FileButler\\filebutler.db' }),
    img_semantic_status: () => ({ enabled: false, vl_model: 'qwen2.5vl:7b', model_ready: false, described: 0, pending: 121 }),
    autostart_status: () => ({ enabled: false, target: null }),
    recommend_model: () => ({ model: 'qwen2.5:14b', ram_gb: 31.9 }),
    get_rules: () => ({ categories: ['文档', '图片', '视频', '音频', '压缩包', '安装包', '代码', '数据', '字体', '其他'],
      rules: [
        { pattern: 'pdf', category: '文档', sub: 'PDF', custom: false },
        { pattern: 'docx', category: '文档', sub: 'Word', custom: false },
        { pattern: 'jpg', category: '图片', sub: '', custom: false },
        { pattern: 'png', category: '图片', sub: '', custom: false },
        { pattern: 'mp4', category: '视频', sub: '', custom: false },
        { pattern: 'mp3', category: '音频', sub: '', custom: false },
        { pattern: 'zip', category: '压缩包', sub: '', custom: false },
        { pattern: 'exe', category: '安装包', sub: '', custom: false },
        { pattern: 'py', category: '代码', sub: 'Python', custom: false },
        { pattern: 'json', category: '数据', sub: '', custom: false },
        { pattern: 'ttf', category: '字体', sub: '', custom: false },
        { pattern: 'dat', category: '其他', sub: '', custom: true }] }),
    history: () => ({ batches: [
      { batch_id: 'a1b2c3', ts: Date.now() / 1000 - 7200, n: 12, undone_n: 0 },
      { batch_id: 'd4e5f6', ts: Date.now() / 1000 - 172800, n: 34, undone_n: 12 }] }),
    list_templates: () => ({ templates: [
      { id: 1, name: '下载目录清理', target_root: 'C:\\Users\\SevenJohn\\Downloads\\已整理', images_by_year: 1 }] }),
    scan_folder: () => ({
      root: 'C:\\Users\\SevenJohn\\Downloads',
      files: files.map((f) => ({ ...f, rule_hit: Math.random() > 0.3, sub: '' })),
      ambiguous: 7,
    }),
    pick_folder: () => null,
    open_path: () => ({ ok: true }),
    list_favorites: () => ({ items: favs.slice() }),
    add_favorite: (p) => {
      if (!favs.some((f) => f.path === p)) favs.unshift({ path: p, note: '', created_at: Date.now() / 1000 })
      return { ok: true }
    },
    remove_favorite: (p) => {
      const i = favs.findIndex((f) => f.path === p)
      if (i >= 0) favs.splice(i, 1)
      return { ok: true }
    },
    set_favorite_note: (p, note) => {
      const f = favs.find((x) => x.path === p)
      if (!f) return { ok: false, error: '该路径未在收藏夹中' }
      f.note = note || ''
      return { ok: true }
    },
    ocr_status: () => ({ available: true, reason: '', hint: '' }),
    content_index_status: () => ({
      status: { running: false, done: 0, total: 0, ok: 0, failed: 0, body_bytes: 0,
        budget_hit: false, stage: '', detail: '', finished_at: null,
        indexed: { docs: 0, ok: 0, failed: 0, bytes: 0 } },
      plan: { candidates: 12, will_index: 12, indexed: { docs: 0, ok: 0, failed: 0, bytes: 0 },
        budget: { max_docs: 50000, max_body_bytes: 2e9, docs_left: 50000, bytes_left: 2e9 } },
    }),
    content_index_budget: () => ({ max_docs: 50000, max_body_bytes: 2e9 }),
    open_url: () => ({ ok: true }),
    thumb_base: () => ({ base: null }),
    set_titlebar_theme: () => ({ ok: true }),
    search: () => ({ mode: 'vector', results: [
      { id: 1, seq: 0, text: '报销制度第六条：住宿标准一线城市 500 元/晚',
        file_path: 'C:\\Users\\SevenJohn\\Documents\\工作\\报销制度.docx', score: 0.7 }], images: [] }),
  }
  window.pywebview = {
    api: new Proxy({}, {
      get(t, prop) {
        // 关键：'then' 必须返回 undefined，否则 api Proxy 被当成 thenable，
        // Promise 链永远挂起（桥看似就绪但所有 await 卡死）
        if (prop === 'then' || prop === 'catch' || prop === 'finally') return undefined
        const m = MOCK[prop]
        return (...a) => Promise.resolve(typeof m === 'function' ? m(...a) : { ok: true })
      },
    }),
  }
})()
