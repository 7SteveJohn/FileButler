<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  NCard, NGrid, NGi, NStatistic, NTag, NSpace, NButton, NText, NSpin, NAlert,
  NList, NListItem, NResult,
} from 'naive-ui'
import { api, resetBridge } from '../lib/bridge'
import { useMessage } from 'naive-ui'

const message = useMessage()
const status = ref(null)
const loading = ref(true)
const report = ref(null)
const space = ref(null)
const savingReport = ref(false)

async function saveReport() {
  savingReport.value = true
  try {
    const r = await api('save_weekly_report')
    if (r.ok) message.success('周报已保存：' + r.path.split(/[\\/]/).pop())
    else message.error(r.error || '保存失败')
  } catch (e) {
    message.error(String(e))
  }
  savingReport.value = false
}

async function refresh() {
  loading.value = true
  // 加载超过 6 秒仍未完成 → 提示用户（避免"假白屏"：loading 无限转）
  const slowTimer = setTimeout(() => {
    if (loading.value) message.warning('首屏加载较慢，请稍候…（后台在启动）')
  }, 6000)
  try {
    // 三个请求并行：串行时总耗时 = 各接口之和（Ollama 探测可能要 2s）
    const [st, rep, sp] = await Promise.all([
      api('get_status'),
      api('weekly_report'),
      api('space_analysis'),
    ])
    status.value = st
    report.value = rep
    space.value = sp
  } catch (e) {
    status.value = { error: String(e) }
  } finally {
    clearTimeout(slowTimer)
    loading.value = false
  }
}
async function retry() {
  resetBridge()      // 强制重连后端
  await refresh()
}
onMounted(refresh)

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const dateStr = computed(() => {
  const d = new Date()
  const wd = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
  return `${d.getMonth() + 1}月${d.getDate()}日 星期${wd}`
})

function fmtSize(n) {
  if (n == null) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return n.toFixed(1) + ' ' + units[i]
}

function goto(k) { window.dispatchEvent(new CustomEvent('fb-goto', { detail: k })) }
function pct(n) {
  if (!space.value || !space.value.total) return 0
  return Math.max(1, Math.round((n / space.value.total) * 100))
}
</script>

<template>
  <div class="fb-page" v-if="status && !status.error">
    <n-spin :show="loading">
      <!-- Hero 横幅 -->
      <div class="fb-hero">
        <h2 class="fb-hero-title">{{ greeting }}{{ status.llm?.mode === 'api' ? '，云端已就绪' : '，一切尽在本地' }} 👋</h2>
        <div class="fb-hero-sub">
          {{ dateStr }} ·
          {{ status.llm?.mode === 'api'
            ? '云端 API 出答案 · 你的文件始终留在本机'
            : '本地模式：无需 API Key，数据零上传（可在设置切换云端 API）' }}
        </div>
        <div class="fb-hero-stats">
          <div class="fb-hero-stat">
            <b>{{ status.library.files }}</b><span>已编目文件</span>
          </div>
          <div class="fb-hero-stat">
            <b>{{ report?.data?.new_count ?? '—' }}</b><span>本周新增</span>
          </div>
          <div class="fb-hero-stat">
            <b>{{ status.kb.chunks }}</b><span>知识库分块</span>
          </div>
          <div class="fb-hero-stat">
            <b>{{ status.llm?.chat?.ok ? '在线' : '离线' }}</b><span>AI 模型</span>
          </div>
        </div>
      </div>

      <n-grid :cols="2" :x-gap="16" :y-gap="16" style="margin-top:16px">
        <!-- 模型接入状态（本地 Ollama / 云端 API 自适应） -->
        <n-gi>
          <n-card class="fb-hoverable" size="medium">
            <template #header>
              模型接入
              <n-tag size="small" round style="margin-left:8px"
                :type="status.llm?.mode === 'api' ? 'info' : 'success'">
                {{ status.llm?.mode === 'api' ? '云端 API' : '本地 Ollama' }}
              </n-tag>
            </template>
            <n-space vertical size="large">
              <n-space>
                <n-tag :type="status.llm?.chat?.ok ? 'success' : 'error'" round size="small">
                  对话：{{ status.llm?.chat?.reason || '未知' }}
                </n-tag>
                <n-tag :type="status.llm?.embed?.ok ? 'success' : 'warning'" round size="small">
                  向量：{{ status.llm?.embed?.reason || '未配置' }}
                </n-tag>
              </n-space>
              <n-text depth="3" class="mono" style="font-size:12px">
                {{ status.llm?.mode === 'api' ? (status.llm.api_base || '未配置服务地址') : status.ollama.host }}
              </n-text>
              <n-space>
                <n-button v-if="!status.llm?.chat?.ok || !status.llm?.embed?.ok"
                  type="primary" size="small" round @click="goto('settings')">
                  前往设置处理
                </n-button>
                <n-button v-else size="small" quaternary type="primary" round
                  @click="goto('settings')">
                  切换接入方式
                </n-button>
              </n-space>
            </n-space>
          </n-card>
        </n-gi>

        <!-- 文件库 -->
        <n-gi>
          <n-card class="fb-hoverable" title="本地文件库（自动扫描）" size="medium">
            <n-space vertical size="large">
              <n-space size="large">
                <div class="fb-stat-ico">🗂</div>
                <n-space vertical :size="2">
                  <n-text strong style="font-size:17px">{{ status.library.files }} 个文件</n-text>
                  <n-text depth="3" style="font-size:12.5px">
                    共 {{ fmtSize(status.library.total_size) }} · 监控 {{ status.watching }} 个目录
                  </n-text>
                </n-space>
              </n-space>
              <n-space :wrap="true">
                <n-tag v-for="c in status.library.categories.slice(0, 7)" :key="c.category"
                  size="small" round :bordered="false">
                  {{ c.category }} {{ c.n }}
                </n-tag>
              </n-space>
              <n-button size="small" quaternary type="primary" @click="goto('files')">
                打开文件搜索 →
              </n-button>
            </n-space>
          </n-card>
        </n-gi>

        <!-- 周报 -->
        <n-gi :span="2">
          <n-card class="fb-hoverable" title="本周文件动态" size="medium"
            v-if="report && report.data">
            <template #header-extra>
              <n-space size="small" align="center">
                <n-text depth="3" style="font-size:12px">周起始 {{ report.week_start }}</n-text>
                <n-button size="tiny" type="primary" ghost :loading="savingReport" @click="saveReport">
                  保存 HTML
                </n-button>
              </n-space>
            </template>
            <n-grid :cols="4" :x-gap="12">
              <n-gi><n-statistic label="本周新增/变更" :value="report.data.new_count" /></n-gi>
              <n-gi><n-statistic label="新增体积" :value="fmtSize(report.data.new_size)" /></n-gi>
              <n-gi><n-statistic label="编目总数" :value="report.data.total_files" /></n-gi>
              <n-gi><n-statistic label="总体积" :value="fmtSize(report.data.total_size)" /></n-gi>
            </n-grid>
            <n-space v-if="report.data.by_category && Object.keys(report.data.by_category).length"
              style="margin-top:14px">
              <n-tag v-for="(v, cat) in report.data.by_category" :key="cat" size="small" round>
                {{ cat }} +{{ v.n }}（{{ fmtSize(v.size) }}）
              </n-tag>
            </n-space>

            <template v-if="report.data.new_sample && report.data.new_sample.length">
              <div class="fb-sub-title">本周新增</div>
              <n-list :show-divider="false">
                <n-list-item v-for="f in report.data.new_sample.slice(0, 5)" :key="f.path"
                  style="padding:5px 0;cursor:pointer" @click="api('open_path', f.path)">
                  <n-space justify="space-between">
                    <n-text style="font-size:13px">{{ f.name }}</n-text>
                    <n-text depth="3" style="font-size:12px">{{ f.category }} · {{ fmtSize(f.size) }}</n-text>
                  </n-space>
                </n-list-item>
              </n-list>
            </template>

            <template v-if="report.data.big_files && report.data.big_files.length">
              <hr class="fb-sub-divider" />
              <div class="fb-sub-title">体积排行 TOP</div>
              <n-list :show-divider="false">
                <n-list-item v-for="f in report.data.big_files.slice(0, 3)" :key="f.path"
                  style="padding:5px 0;cursor:pointer" @click="api('open_path', f.path)">
                  <n-space justify="space-between">
                    <n-text style="font-size:13px">{{ f.name }}</n-text>
                    <n-text depth="3" style="font-size:12px">{{ f.category }} · {{ fmtSize(f.size) }}</n-text>
                  </n-space>
                </n-list-item>
              </n-list>
            </template>
          </n-card>
        </n-gi>

        <!-- 空间占用 -->
        <n-gi :span="2">
          <n-card class="fb-hoverable" title="空间占用" size="medium" v-if="space">
            <template #header-extra>
              <n-text depth="3" style="font-size:12px">共 {{ fmtSize(space.total) }}</n-text>
            </template>
            <n-space vertical size="large">
              <div v-if="space.by_category && space.by_category.length">
                <div class="fb-sub-title">按类别</div>
                <div v-for="c in space.by_category" :key="c.category" class="fb-space-row">
                  <span class="fb-space-label">{{ c.category }}</span>
                  <div class="fb-space-bar">
                    <div class="fb-space-fill" :style="{ width: pct(c.size) + '%' }"></div>
                  </div>
                  <span class="fb-space-size">{{ fmtSize(c.size) }}</span>
                </div>
              </div>
              <div v-if="space.by_dir && space.by_dir.length">
                <div class="fb-sub-title">占用 TOP 目录</div>
                <n-list :show-divider="false">
                  <n-list-item v-for="d in space.by_dir.slice(0, 8)" :key="d.dir"
                    style="padding:4px 0;cursor:pointer" @click="api('open_path', d.dir)">
                    <n-space justify="space-between">
                      <n-text style="font-size:13px" ellipsis>{{ d.dir }}</n-text>
                      <n-text depth="3" style="font-size:12px">{{ fmtSize(d.size) }}</n-text>
                    </n-space>
                  </n-list-item>
                </n-list>
              </div>
            </n-space>
          </n-card>
        </n-gi>
      </n-grid>

      <div class="fb-footnote">
        所有文件操作均经「预览 → 确认」并记录日志，支持一键撤销 · 累计 {{ status.kb.operations }} 次操作
        · 数据库 {{ status.kb.db_path }}
      </div>
    </n-spin>
  </div>
  <div class="fb-page" v-else-if="loading">
    <n-spin :show="true" size="large">
      <div class="fb-dash-loading">
        <div>正在加载概览…</div>
        <div class="fb-dash-loading-sub">首次进入需等待模型状态检测，请稍候</div>
      </div>
    </n-spin>
  </div>
  <div class="fb-page" v-else-if="status && status.error">
    <n-result status="error" title="首屏加载失败"
      :description="`无法连接 FileButler 后端服务。\n模型状态：${status.llm?.chat?.ok === false ? 'Ollama 离线' : '后端未就绪'}。\n\n请确认：本应用窗口未被防火墙拦截、Ollama（如已安装）是否需要启动。\n\n错误：${status.error}`">
      <template #footer>
        <n-space>
          <n-button type="primary" @click="retry">重试</n-button>
          <n-button quaternary @click="goto('settings')">打开设置</n-button>
        </n-space>
      </template>
    </n-result>
  </div>
</template>
