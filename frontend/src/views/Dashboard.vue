<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  NCard, NGrid, NGi, NStatistic, NTag, NSpace, NButton, NText, NSpin, NAlert,
  NList, NListItem, NResult, NIcon,
} from 'naive-ui'
import { FolderOpenOutline } from '@vicons/ionicons5'
import { api, resetBridge, on } from '../lib/bridge'
import { useMessage } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { catLabel, statusText } from '../lib/i18n'

const message = useMessage()
const { t, locale } = useI18n()
void locale
const status = ref(null)
const loading = ref(true)
const report = ref(null)
const space = ref(null)
const savingReport = ref(false)
// 渐进加载：三块数据各自独立到位，首屏壳不等于最慢的接口
const loadingStatus = ref(true)
const loadingReport = ref(true)
const loadingSpace = ref(true)

async function saveReport() {
  savingReport.value = true
  try {
    const r = await api('save_weekly_report')
    if (r.ok) message.success(t('dashboard.weekSaved', { n: r.path.split(/[\\/]/).pop() }))
    else message.error(r.error || t('dashboard.saveFail'))
  } catch (e) {
    message.error(String(e))
  }
  savingReport.value = false
}

async function refresh() {
  loading.value = true
  loadingStatus.value = loadingReport.value = loadingSpace.value = true
  // 三块并行、各自填充：首屏壳立即可见，慢的（空间分析/周报）局部转圈
  api('get_status')
    .then((r) => { status.value = r })
    .catch((e) => { status.value = { error: String(e) } })
    .finally(() => { loadingStatus.value = false; loading.value = false })
  api('weekly_report')
    .then((r) => { report.value = r })
    .catch(() => { report.value = null })
    .finally(() => { loadingReport.value = false })
  api('space_analysis')
    .then((r) => { space.value = r })
    .catch(() => { space.value = null })
    .finally(() => { loadingSpace.value = false })
}
async function retry() {
  resetBridge()      // 强制重连后端
  await refresh()
}
const libStats = ref(null)
async function loadLib() {
  try { libStats.value = await api('lib_stats') } catch (e) { /* 忽略，稍后重试 */ }
}

let offOllamaReady = null
// Ollama 后台拉起就绪后后端会推事件：首屏先按离线渲染，就绪自动转在线（不再干等）
onMounted(() => {
  offOllamaReady = on('ollama_ready', refresh)
  refresh()
  loadLib()
})
onUnmounted(() => { if (offOllamaReady) offOllamaReady() })

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return t('dashboard.greet.night')
  if (h < 12) return t('dashboard.greet.morning')
  if (h < 14) return t('dashboard.greet.noon')
  if (h < 18) return t('dashboard.greet.afternoon')
  return t('dashboard.greet.evening')
})
const dateStr = computed(() => {
  const d = new Date()
  const wd = t('dashboard.weekNames').split(',')[d.getDay()]
  return t('dashboard.weekDay', { m: d.getMonth() + 1, d: d.getDate(), w: wd })
})

function fmtSize(n) {
  if (n == null) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return n.toFixed(1) + ' ' + units[i]
}

// 大数字缩写：854614 → 85 万（成熟产品的仪表盘读法，避免一长串数字）
function fmtCount(n) {
  if (n == null) return '—'
  if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, '') + ' ' + t('dashboard.wan')
  return String(n)
}

function goto(k) { window.dispatchEvent(new CustomEvent('fb-goto', { detail: k })) }
function pct(n) {
  if (!space.value || !space.value.total) return 0
  return Math.max(1, Math.round((n / space.value.total) * 100))
}
</script>

<template>
  <!-- 单根包装：页面切换过渡动画要求唯一根节点 -->
  <div class="fb-dash-root">
  <div class="fb-page" v-if="status && !status.error">
    <n-spin :show="loading">
      <!-- 问候头：紧凑单卡，成熟产品风格（不铺大渐变） -->
      <div class="fb-hello">
        <div class="fb-hello-main">
          <h2 class="fb-hello-title">
            {{ t('dashboard.hello', { g: greeting }) }}
            <span class="fb-hello-badge" v-if="status.llm?.mode === 'api'">{{ t('dashboard.badgeApi') }}</span>
            <span class="fb-hello-badge" v-else>{{ t('dashboard.badgeLocal') }}</span>
          </h2>
          <div class="fb-hello-sub">
            {{ dateStr }} ·
            {{ status.llm?.mode === 'api'
              ? t('dashboard.subApi')
              : t('dashboard.subLocal') }}
          </div>
        </div>
        <div class="fb-hello-stats">
          <div class="fb-hello-stat">
            <b>{{ fmtCount(libStats?.files) }}</b><span>{{ t('dashboard.statFiles') }}</span>
          </div>
          <div class="fb-hello-stat">
            <b>{{ report?.data?.new_count ?? '—' }}</b><span>{{ t('dashboard.statNew') }}</span>
          </div>
          <div class="fb-hello-stat">
            <b>{{ fmtCount(status.kb.chunks) }}</b><span>{{ t('dashboard.statChunks') }}</span>
          </div>
          <div class="fb-hello-stat">
            <b :class="status.llm?.chat?.ok ? 'fb-hello-online' : 'fb-hello-offline'">
              {{ status.llm?.chat?.ok ? t('dashboard.online') : t('dashboard.offline') }}
            </b><span>{{ t('dashboard.statModel') }}</span>
          </div>
        </div>
      </div>

      <n-grid :cols="2" :x-gap="16" :y-gap="16" style="margin-top:16px">
        <!-- 模型接入状态（本地 Ollama / 云端 API 自适应） -->
        <n-gi>
          <n-card class="fb-hoverable" size="medium">
            <template #header>
              {{ t('dashboard.llmTitle') }}
              <n-tag size="small" round style="margin-left:8px"
                :type="status.llm?.mode === 'api' ? 'info' : 'success'">
                {{ status.llm?.mode === 'api' ? t('dashboard.llmCloud') : t('dashboard.llmLocal') }}
              </n-tag>
            </template>
            <n-space vertical size="large">
              <n-space>
                <n-tag :type="status.llm?.chat?.ok ? 'success' : 'error'" round size="small">
                  {{ t('dashboard.chat', { v: statusText(status.llm?.chat, t('dashboard.unknown')) }) }}
                </n-tag>
                <n-tag :type="status.llm?.embed?.ok ? 'success' : 'warning'" round size="small">
                  {{ t('dashboard.embed', { v: statusText(status.llm?.embed, t('dashboard.notSet')) }) }}
                </n-tag>
              </n-space>
              <n-text depth="3" class="mono" style="font-size:12px">
                {{ status.llm?.mode === 'api' ? (status.llm.api_base || t('dashboard.noBase')) : status.ollama.host }}
              </n-text>
              <n-space>
                <n-button v-if="!status.llm?.chat?.ok || !status.llm?.embed?.ok"
                  type="primary" size="small" round @click="goto('settings')">
                  {{ t('dashboard.goFix') }}
                </n-button>
                <n-button v-else size="small" quaternary type="primary" round
                  @click="goto('settings')">
                  {{ t('dashboard.switchMode') }}
                </n-button>
              </n-space>
            </n-space>
          </n-card>
        </n-gi>

        <!-- 文件库 -->
        <n-gi>
          <n-card class="fb-hoverable" :title="t('dashboard.libTitle')" size="medium">
            <n-space vertical size="large">
              <n-space size="large">
                <div class="fb-stat-ico">
                  <n-icon :component="FolderOpenOutline" size="19" style="color:var(--fb-accent)" />
                </div>
                <n-space vertical :size="2">
                  <n-text strong style="font-size:17px">{{ t('dashboard.libFiles', { n: libStats?.files ?? 0 }) }}</n-text>
                  <n-text depth="3" style="font-size:12.5px">
                    {{ t('dashboard.libSub', { size: fmtSize(libStats?.total_size), n: status.watching }) }}
                  </n-text>
                </n-space>
              </n-space>
              <n-space :wrap="true">
                <n-tag v-for="c in (libStats?.categories || []).slice(0, 7)" :key="c.category"
                  size="small" round :bordered="false">
                  {{ catLabel(c.category) }} {{ c.n }}
                </n-tag>
              </n-space>
              <n-button size="small" quaternary type="primary" @click="goto('files')">
                {{ t('dashboard.openSearch') }}
              </n-button>
            </n-space>
          </n-card>
        </n-gi>

        <!-- 周报 -->
        <n-gi :span="2">
          <n-card class="fb-hoverable" :title="t('dashboard.weekTitle')" size="medium">
            <template #header-extra>
              <n-space size="small" align="center">
                <n-text v-if="report && report.data" depth="3" style="font-size:12px">{{ t('dashboard.weekStart', { d: report.week_start }) }}</n-text>
                <n-button v-if="report && report.data" size="tiny" type="primary" ghost :loading="savingReport" @click="saveReport">
                  {{ t('dashboard.saveHtml') }}
                </n-button>
              </n-space>
            </template>
            <n-spin v-if="loadingReport" :show="true" size="small">
              <div style="height:64px"></div>
            </n-spin>
            <n-text v-else-if="!report || !report.data" depth="3">{{ t('dashboard.weekPending') }}</n-text>
            <template v-else>
            <n-grid :cols="4" :x-gap="12">
              <n-gi><n-statistic :label="t('dashboard.newCount')" :value="report.data.new_count" /></n-gi>
              <n-gi><n-statistic :label="t('dashboard.newSize')" :value="fmtSize(report.data.new_size)" /></n-gi>
              <n-gi><n-statistic :label="t('dashboard.totalFiles')" :value="report.data.total_files" /></n-gi>
              <n-gi><n-statistic :label="t('dashboard.totalSize')" :value="fmtSize(report.data.total_size)" /></n-gi>
            </n-grid>
            <n-space v-if="report.data.by_category && Object.keys(report.data.by_category).length"
              style="margin-top:14px">
              <n-tag v-for="(v, cat) in report.data.by_category" :key="cat" size="small" round>
                {{ catLabel(cat) }} +{{ v.n }}（{{ fmtSize(v.size) }}）
              </n-tag>
            </n-space>

            <template v-if="report.data.new_sample && report.data.new_sample.length">
              <div class="fb-sub-title">{{ t('dashboard.newList') }}</div>
              <n-list :show-divider="false">
                <n-list-item v-for="f in report.data.new_sample.slice(0, 5)" :key="f.path"
                  style="padding:5px 0;cursor:pointer" @click="api('open_path', f.path)">
                  <n-space justify="space-between">
                    <n-text style="font-size:13px">{{ f.name }}</n-text>
                    <n-text depth="3" style="font-size:12px">{{ catLabel(f.category) }} · {{ fmtSize(f.size) }}</n-text>
                  </n-space>
                </n-list-item>
              </n-list>
            </template>

            <template v-if="report.data.big_files && report.data.big_files.length">
              <hr class="fb-sub-divider" />
              <div class="fb-sub-title">{{ t('dashboard.topSize') }}</div>
              <n-list :show-divider="false">
                <n-list-item v-for="f in report.data.big_files.slice(0, 3)" :key="f.path"
                  style="padding:5px 0;cursor:pointer" @click="api('open_path', f.path)">
                  <n-space justify="space-between">
                    <n-text style="font-size:13px">{{ f.name }}</n-text>
                    <n-text depth="3" style="font-size:12px">{{ catLabel(f.category) }} · {{ fmtSize(f.size) }}</n-text>
                  </n-space>
                </n-list-item>
              </n-list>
            </template>
            </template>
          </n-card>
        </n-gi>

        <!-- 空间占用 -->
        <n-gi :span="2">
          <n-card class="fb-hoverable" :title="t('dashboard.spaceTitle')" size="medium">
            <template #header-extra>
              <n-text v-if="space && space.total" depth="3" style="font-size:12px">{{ t('dashboard.spaceTotal', { size: fmtSize(space.total) }) }}</n-text>
            </template>
            <n-spin v-if="loadingSpace" :show="true" size="small">
              <div style="height:64px"></div>
            </n-spin>
            <n-text v-else-if="!space" depth="3">{{ t('dashboard.spacePending') }}</n-text>
            <n-space v-else vertical size="large">
              <div v-if="space.by_category && space.by_category.length">
                <div class="fb-sub-title">{{ t('dashboard.byCategory') }}</div>
                <div v-for="c in space.by_category" :key="c.category" class="fb-space-row">
                  <span class="fb-space-label">{{ catLabel(c.category) }}</span>
                  <div class="fb-space-bar">
                    <div class="fb-space-fill" :style="{ width: pct(c.size) + '%' }"></div>
                  </div>
                  <span class="fb-space-size">{{ fmtSize(c.size) }}</span>
                </div>
              </div>
              <div v-if="space.by_dir && space.by_dir.length">
                <div class="fb-sub-title">{{ t('dashboard.topDirs') }}</div>
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
        {{ t('dashboard.audit', { n: status.kb.operations }) }}
        {{ t('dashboard.dbLine', { p: status.kb.db_path }) }}
      </div>
    </n-spin>
  </div>
  <div class="fb-page" v-else-if="loading">
    <n-spin :show="true" size="large">
      <div class="fb-dash-loading">
        <div>{{ t('dashboard.loading') }}</div>
        <div class="fb-dash-loading-sub">{{ t('dashboard.loadingSub') }}</div>
      </div>
    </n-spin>
  </div>
  <div class="fb-page" v-else-if="status && status.error">
    <n-result status="error" :title="t('dashboard.failTitle')"
      :description="t('dashboard.failDesc', { llm: status.llm?.chat?.ok === false ? t('dashboard.ollamaOff') : t('dashboard.notReady'), err: status.error })">
      <template #footer>
        <n-space>
          <n-button type="primary" @click="retry">{{ t('dashboard.retry') }}</n-button>
          <n-button quaternary @click="goto('settings')">{{ t('dashboard.openSettings') }}</n-button>
        </n-space>
      </template>
    </n-result>
  </div>
  </div>
</template>
