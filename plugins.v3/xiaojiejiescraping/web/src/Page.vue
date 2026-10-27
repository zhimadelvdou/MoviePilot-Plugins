<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useTheme } from 'vuetify'

const props = defineProps({ api: { type: Object, required: true }, pluginId: { type: [String, Number], required: true }, sourcePluginId: { type: [String, Number], default: '' } })
const hostTheme = useTheme()
const hostThemeName = computed(() => hostTheme.global.name.value)
const options = ref(null)
const sourcePath = ref('')
const targetPath = ref('')
const mediaSource = ref('')
const actressSubfolder = ref(false)
const actressChoices = ref({})
const preview = ref(null)
const previewNeedsRefresh = ref(false)
const busy = ref(false)
const previewBusy = ref(false)
const outcome = ref('')
const error = ref('')
const browser = ref(null)
const browseKind = ref('source')
const browsePurpose = ref('manual')
const browsePath = ref('')
const parentPath = ref('')
const browseItems = ref([])
const browseSort = ref('modified-asc')
const browseSortItems = [
  { title: '名称升序', value: 'name-asc' },
  { title: '名称降序', value: 'name-desc' },
  { title: '修改时间：新到旧', value: 'modified-desc' },
  { title: '修改时间：旧到新', value: 'modified-asc' },
  { title: '文件大小：大到小', value: 'size-desc' },
  { title: '文件大小：小到大', value: 'size-asc' },
]
const browseNameCollator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const now = ref(Date.now())
const pendingPreviewId = ref('')
const automaticStatus = ref(null)
const automaticStatusBusy = ref(false)
const automaticCheckBusy = ref(false)
const automaticCheckMessage = ref('')
const automaticToggleBusy = ref(false)
const automaticToggleMessage = ref('')
const automaticRules = ref({ automatic_match_mode: 'tag_and_path', downloader: '', torrent_tag: '', recognize_dirs: '', target_path: '' })
const automaticDownloaders = ref([])
const automaticRulesBusy = ref(false)
const automaticRulesMessage = ref('')
let requestGeneration = 0
let clockTimer
const previewExpiryMs = computed(() => {
  const value = preview.value?.expires_at
  if (typeof value === 'number' || (typeof value === 'string' && /^\d+(\.\d+)?$/.test(value))) return Number(value) * 1000
  return Date.parse(value)
})
const displayItems = computed(() => {
  const recognizedItems = preview.value?.items || []
  const executionItems = preview.value?.execution?.items || []
  if (!recognizedItems.length) return executionItems
  return recognizedItems.map(item => ({
    ...item,
    transferState: executionItems.find(result => result.source === item.source && result.target === item.target)?.state,
    completionScope: executionItems.find(result => result.source === item.source && result.target === item.target)?.completion_scope,
  }))
})
const sortedBrowseItems = computed(() => {
  const sortSpecs = {
    'name-asc': { field: 'name', direction: 1 },
    'name-desc': { field: 'name', direction: -1 },
    'modified-asc': { field: 'modify_time', direction: 1 },
    'modified-desc': { field: 'modify_time', direction: -1 },
    'size-asc': { field: 'size', direction: 1 },
    'size-desc': { field: 'size', direction: -1 },
  }
  const { field, direction } = sortSpecs[browseSort.value] || sortSpecs['name-asc']
  const comparePath = (left, right) => left === right ? 0 : left < right ? -1 : 1
  const compareName = (left, right) => browseNameCollator.compare(left.name || '', right.name || '')
  const compareNameThenPath = (left, right) => compareName(left, right) || comparePath(left.path || '', right.path || '')
  const numericValue = (item) => {
    const value = item?.[field]
    if (typeof value !== 'number' || !Number.isFinite(value) || (field === 'size' && value < 0)) return null
    return value
  }
  return [...browseItems.value].sort((left, right) => {
    const leftDirectory = left.type === 'dir' || left.type === 'directory'
    const rightDirectory = right.type === 'dir' || right.type === 'directory'
    if (leftDirectory !== rightDirectory) return leftDirectory ? -1 : 1
    if (field === 'name') return (compareName(left, right) * direction) || comparePath(left.path || '', right.path || '')
    const leftValue = numericValue(left)
    const rightValue = numericValue(right)
    if (leftValue === null && rightValue !== null) return 1
    if (leftValue !== null && rightValue === null) return -1
    if (leftValue !== null && rightValue !== null && leftValue !== rightValue) {
      return (leftValue < rightValue ? -1 : 1) * direction
    }
    return compareNameThenPath(left, right)
  })
})
const previewExpired = computed(() => !preview.value || !Number.isFinite(previewExpiryMs.value) || previewExpiryMs.value <= now.value)
const executionLocked = computed(() => outcome.value === 'pendingaccepted' || outcome.value === 'unknown')
const formLocked = computed(() => busy.value || executionLocked.value)
const canExecute = computed(() => !!preview.value && !previewNeedsRefresh.value && !previewExpired.value && preview.value.summary?.failed === 0 && preview.value.summary?.success > 0 && !busy.value && !previewBusy.value && outcome.value === '')
const endpoint = (name) => `plugin/${props.pluginId}/manual/${name}`
const automaticEndpoint = (name) => `plugin/${props.pluginId}/automatic/${name}`
const pluginConfigEndpoint = () => `plugin/${props.pluginId}`
const automaticToggleDisabled = computed(() => automaticToggleBusy.value || automaticStatusBusy.value
  || automaticCheckBusy.value || automaticRulesBusy.value || typeof automaticStatus.value?.enabled !== 'boolean')
const automaticRulesDisabled = computed(() => automaticRulesBusy.value || automaticToggleBusy.value)
const automaticDownloaderItems = computed(() => {
  const choices = automaticDownloaders.value.map(item => ({ title: item.name, value: item.name }))
  const current = automaticRules.value.downloader
  if (current && !choices.some(item => item.value === current)) choices.unshift({ title: `${current}（当前配置）`, value: current })
  return [{ title: '请选择下载器', value: '' }, ...choices]
})
const automaticRulesCanSave = computed(() => {
  const rules = automaticRules.value
  const hasTag = !!rules.downloader && !!rules.torrent_tag.trim()
  const hasPath = !!rules.downloader && !!rules.recognize_dirs.trim()
  if (rules.automatic_match_mode === 'tag') return hasTag
  if (rules.automatic_match_mode === 'path') return hasPath
  if (rules.automatic_match_mode === 'tag_or_path') return hasTag || hasPath
  return hasTag && hasPath
})
const automaticStatusLabel = computed(() => {
  if (typeof automaticStatus.value?.enabled !== 'boolean') return '状态未知'
  return automaticStatus.value.enabled ? '已开启' : '已关闭'
})
const automaticMatchModeLabel = computed(() => ({
  tag: '按标签', path: '按路径', tag_or_path: '标签或路径', tag_and_path: '标签且路径',
}[automaticStatus.value?.match_mode] || '状态未返回'))
const storageKey = () => `xiaojiejiescraping:manual-pending:${props.pluginId}`
const automaticModeLabel = computed(() => {
  if (!automaticStatus.value) return '状态未获取'
  if (!automaticStatus.value.enabled) return '自动触发已关闭'
  if (automaticStatus.value.mode === 'discover') return '只读发现'
  if (automaticStatus.value.mode === 'preview') return '自动预览'
  if (automaticStatus.value.mode === 'execute') return '自动整理并刮削'
  return '自动模式未知'
})
const automaticCounts = computed(() => {
  const counts = automaticStatus.value?.counts
  return counts && typeof counts === 'object' ? Object.entries(counts).filter(([, value]) => typeof value === 'number') : []
})

function invalidatePreview() {
  if (formLocked.value) return
  requestGeneration += 1
  preview.value = null
  previewNeedsRefresh.value = false
  outcome.value = ''
  clearPendingPreview()
  error.value = ''
}
function setPath(kind, path) {
  if (formLocked.value) return
  if (kind === 'source') { sourcePath.value = path; actressChoices.value = {} }
  else { targetPath.value = path; actressChoices.value = {} }
  invalidatePreview()
}
function setMediaSource(value) {
  if (formLocked.value) return
  mediaSource.value = value
  actressChoices.value = {}
  invalidatePreview()
}
function setActressSubfolder(value) {
  if (formLocked.value || actressSubfolder.value === value) return
  actressSubfolder.value = value === true
  invalidatePreview()
  actressChoices.value = {}
}
function setActressChoice(path, value) {
  if (formLocked.value) return
  requestGeneration += 1
  actressChoices.value = { ...actressChoices.value, [path]: value }
  previewNeedsRefresh.value = true
  outcome.value = ''
  clearPendingPreview()
  error.value = ''
}
function executionOutcome(state) {
  if (state === 'completed') return 'completed'
  if (state === 'failed') return 'failed'
  if (state === 'accepted' || state === 'submitting') return 'pendingaccepted'
  return 'unknown'
}
function itemStatus(item) {
  const state = item.transferState || item.state
  if (state === 'accepted' || state === 'submitting') return '已受理'
  if (state === 'completed') return item.completionScope === 'file_transfer' || item.completion_scope === 'file_transfer' ? '硬链接已核验' : '整理已完成'
  if (state === 'failed') return item.message || '失败'
  if (state === 'unknown' || state === 'uncertain') return '结果未知'
  if (item.success === true) return '可整理'
  if (item.success === false) return item.message || '失败'
  return '状态未知'
}
function persistPendingPreview(id) {
  pendingPreviewId.value = id || ''
  if (!id) return
  try { sessionStorage.setItem(storageKey(), id) } catch { /* session persistence is best-effort */ }
}
function clearPendingPreview() {
  pendingPreviewId.value = ''
  try { sessionStorage.removeItem(storageKey()) } catch { /* session storage may be unavailable */ }
}
function applyOutcome(state, id) {
  outcome.value = executionOutcome(state)
  if (outcome.value === 'pendingaccepted' || outcome.value === 'unknown') persistPendingPreview(id)
  else clearPendingPreview()
}
async function loadOptions() {
  if (formLocked.value) return
  error.value = ''
  try {
    options.value = await props.api.get(endpoint('options'))
    if (typeof options.value.actress_subfolder === 'boolean') actressSubfolder.value = options.value.actress_subfolder
    if (!targetPath.value) targetPath.value = options.value.default_target_path || ''
    if (!mediaSource.value) mediaSource.value = options.value.sources?.[0]?.value || ''
  } catch {
    error.value = '无法加载可用来源和目录。'
  }
}
async function openBrowser(kind, purpose = 'manual') {
  if (formLocked.value) return
  browseKind.value = kind
  browsePurpose.value = purpose
  if (purpose === 'automatic') {
    const roots = automaticRules.value.recognize_dirs.split(/\r?\n/).map(path => path.trim()).filter(Boolean)
    browsePath.value = kind === 'source' ? (roots.at(-1) || options.value?.roots?.[0] || '/') : (automaticRules.value.target_path || options.value?.default_target_path || '/')
  } else {
    browsePath.value = kind === 'source' ? (sourcePath.value || options.value?.roots?.[0] || '/') : (targetPath.value || options.value?.default_target_path || '/')
  }
  browser.value = true
  await loadBrowse()
}
async function loadBrowse(path = browsePath.value) {
  if (formLocked.value) return
  error.value = ''
  try {
    const result = await props.api.get(endpoint('browse'), { params: { path, kind: browseKind.value } })
    browsePath.value = result.path || path
    parentPath.value = result.parent || ''
    browseItems.value = result.items || []
  } catch {
    error.value = '无法读取所选目录。'
  }
}
function chooseItem(item) {
  if (formLocked.value) return
  if (item.type === 'dir' || item.type === 'directory') { void loadBrowse(item.path); return }
  if (browsePurpose.value === 'automatic') return
  setPath(browseKind.value, item.path)
  browser.value = false
}
function chooseCurrentDirectory() {
  if (formLocked.value) return
  if (browsePurpose.value === 'automatic' && browseKind.value === 'source') {
    const roots = automaticRules.value.recognize_dirs.split(/\r?\n/).map(path => path.trim()).filter(Boolean)
    if (!roots.includes(browsePath.value)) automaticRules.value.recognize_dirs = [...roots, browsePath.value].join('\n')
  } else if (browsePurpose.value === 'automatic') automaticRules.value.target_path = browsePath.value
  else setPath(browseKind.value, browsePath.value)
  browser.value = false
}
async function createPreview() {
  if (!options.value?.enabled || !sourcePath.value || !targetPath.value || !mediaSource.value || formLocked.value || previewBusy.value) return
  invalidatePreview()
  const generation = ++requestGeneration
  const snapshot = { path: sourcePath.value, target_path: targetPath.value, media_source: mediaSource.value,
    actress_subfolder: actressSubfolder.value, actress_choices: { ...actressChoices.value } }
  previewBusy.value = true
  error.value = ''
  try {
    const result = await props.api.post(endpoint('preview'), { ...snapshot, transfer_type: 'link', library_category_folder: false })
    if (requestGeneration === generation && snapshot.path === sourcePath.value && snapshot.target_path === targetPath.value && snapshot.media_source === mediaSource.value && !formLocked.value) {
      preview.value = result
      previewNeedsRefresh.value = false
    }
  } catch {
    if (requestGeneration === generation) error.value = '预览失败，请检查所选路径和来源。'
  } finally { previewBusy.value = false }
}
async function execute() {
  if (!canExecute.value) return
  const id = preview.value.preview_id
  busy.value = true
  applyOutcome('submitting', id)
  error.value = ''
  try {
    const result = await props.api.post(endpoint('execute'), { preview_id: id })
    applyOutcome(result.state, id)
    preview.value = { ...preview.value, execution: result }
  } catch {
    applyOutcome('unknown', id)
    error.value = '执行请求结果未知。请先刷新状态，勿重复执行。'
  } finally { busy.value = false }
}
async function refreshStatus() {
  const id = preview.value?.preview_id || pendingPreviewId.value
  if (!id || busy.value) return
  busy.value = true
  try {
    const result = await props.api.get(endpoint('status'), { params: { preview_id: id } })
    preview.value = { ...(preview.value || { preview_id: id, expires_at: 0 }), summary: result.summary || preview.value?.summary || { total: 0, success: 0, failed: 0 }, execution: result }
    applyOutcome(result.state, id)
    error.value = ''
  } catch { applyOutcome('unknown', id); error.value = '状态查询失败；请稍后只读重试。' }
  finally { busy.value = false }
}
async function restorePendingPreview() {
  try { pendingPreviewId.value = sessionStorage.getItem(storageKey()) || '' } catch { pendingPreviewId.value = '' }
  if (!pendingPreviewId.value) return
  preview.value = { preview_id: pendingPreviewId.value, expires_at: 0, items: [], summary: { total: 0, success: 0, failed: 0 } }
  outcome.value = 'unknown'
  await refreshStatus()
}
function formatAutomaticTime(value) {
  if (value === null || value === undefined || value === '') return '尚未记录'
  const timestamp = typeof value === 'number' ? value * 1000 : Date.parse(value)
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : '时间未知'
}
async function loadAutomaticStatus() {
  if (automaticToggleBusy.value) return
  automaticStatusBusy.value = true
  try { automaticStatus.value = await props.api.get(automaticEndpoint('status')) }
  catch { automaticStatus.value = null }
  finally { automaticStatusBusy.value = false }
}
async function checkAutomaticConditions() {
  if (automaticToggleBusy.value) return
  automaticCheckBusy.value = true
  automaticCheckMessage.value = ''
  try {
    const result = await props.api.post(automaticEndpoint('check'))
    if (result?.ready === true) automaticCheckMessage.value = '触发条件当前就绪。'
    else if (result?.ready === false) automaticCheckMessage.value = '触发条件当前未就绪。'
    else automaticCheckMessage.value = '检查已完成；条件结果未返回。'
  } catch { automaticCheckMessage.value = '触发条件检查暂不可用。' }
  finally { automaticCheckBusy.value = false }
}
async function readAutomaticStatus() {
  const status = await props.api.get(automaticEndpoint('status'))
  if (typeof status?.enabled !== 'boolean') throw new Error('automatic status unavailable')
  automaticStatus.value = status
  return status
}
function readPluginConfigResponse(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('plugin config unavailable')
  const owns = (key) => Object.prototype.hasOwnProperty.call(payload, key)
  const isEnvelope = owns('success') || owns('message') || owns('data')
  if (isEnvelope) {
    if (payload.success !== true) throw new Error('plugin config request failed')
    payload = payload.data
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)
      || typeof payload.enabled !== 'boolean' || typeof payload.discovery_enabled !== 'boolean') {
    throw new Error('plugin config unavailable')
  }
  return payload
}
function pluginConfigEnvelope(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || payload.success !== true
      || !payload.data || typeof payload.data !== 'object' || Array.isArray(payload.data)) {
    throw new Error('plugin config unavailable')
  }
  return payload.data
}
function downloaderEnvelope(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || payload.success !== true || !Array.isArray(payload.data)) {
    throw new Error('download clients unavailable')
  }
  return payload.data.filter(item => item && item.type === 'qbittorrent' && typeof item.name === 'string' && item.name.trim())
    .map(item => ({ name: item.name, type: item.type }))
}
async function loadAutomaticRules() {
  try {
    const config = pluginConfigEnvelope(await props.api.get(pluginConfigEndpoint()))
    automaticRules.value = {
      automatic_match_mode: ['tag', 'path', 'tag_or_path', 'tag_and_path'].includes(config.automatic_match_mode) ? config.automatic_match_mode : 'tag_and_path',
      downloader: typeof config.downloader === 'string' ? config.downloader : '',
      torrent_tag: typeof config.torrent_tag === 'string' ? config.torrent_tag : '',
      recognize_dirs: typeof config.recognize_dirs === 'string' ? config.recognize_dirs : '',
      target_path: typeof config.target_path === 'string' ? config.target_path : '',
    }
    automaticDownloaders.value = downloaderEnvelope(await props.api.get('download/clients'))
    automaticRulesMessage.value = ''
  } catch {
    automaticRulesMessage.value = '自动规则未能读取；请检查插件配置和下载器列表。'
  }
}
async function saveAutomaticRules() {
  if (!automaticRulesCanSave.value || automaticRulesDisabled.value) return
  automaticRulesBusy.value = true
  automaticRulesMessage.value = ''
  try {
    const current = pluginConfigEnvelope(await props.api.get(pluginConfigEndpoint()))
    const updated = {
      ...current,
      automatic_match_mode: automaticRules.value.automatic_match_mode,
      downloader: automaticRules.value.downloader,
      torrent_tag: automaticRules.value.torrent_tag,
      recognize_dirs: automaticRules.value.recognize_dirs,
      target_path: automaticRules.value.target_path,
    }
    const response = await props.api.put(pluginConfigEndpoint(), updated)
    if (!response || response.success !== true) throw new Error('plugin config update failed')
    const saved = pluginConfigEnvelope(await props.api.get(pluginConfigEndpoint()))
    if (saved.automatic_match_mode !== updated.automatic_match_mode || saved.downloader !== updated.downloader
      || saved.torrent_tag !== updated.torrent_tag || saved.recognize_dirs !== updated.recognize_dirs
      || saved.target_path !== updated.target_path) throw new Error('plugin config readback mismatch')
    automaticRulesMessage.value = '自动规则已保存；自动触发开关保持原状态。'
    await loadAutomaticStatus()
  } catch {
    automaticRulesMessage.value = '自动规则保存未确认；请刷新配置后核对。'
  } finally { automaticRulesBusy.value = false }
}
async function setAutomaticEnabled(event) {
  const desired = typeof event === 'boolean' ? event : event?.target?.checked === true
  if (event?.target) event.target.checked = automaticStatus.value?.enabled === true
  if (automaticToggleDisabled.value || desired === automaticStatus.value?.enabled) return
  automaticToggleBusy.value = true
  automaticToggleMessage.value = ''
  let readbackAttempted = false
  try {
    const config = readPluginConfigResponse(await props.api.get(pluginConfigEndpoint()))
    const response = await props.api.put(pluginConfigEndpoint(), { ...config, discovery_enabled: desired })
    if (response?.success !== true) throw new Error('plugin config update failed')
    readbackAttempted = true
    const status = await readAutomaticStatus()
    if (status.enabled !== desired) {
      automaticToggleMessage.value = '配置已提交，但服务端状态未达到所选状态；请检查插件总开关和本地存储设置。'
    } else {
      automaticToggleMessage.value = desired
        ? (status.new_only === true ? '自动触发已开启；只处理重新开启后完成的下载。' : status.new_only === false ? '自动触发已开启；会包含启用前已完成的旧任务。' : '自动触发已开启；任务边界状态未返回。')
        : '自动触发已关闭；已受理的整理不会取消。'
    }
  } catch {
    if (readbackAttempted) automaticStatus.value = null
    else {
      try { await readAutomaticStatus() }
      catch { automaticStatus.value = null }
    }
    automaticToggleMessage.value = typeof automaticStatus.value?.enabled === 'boolean'
      ? `保存未确认；回读状态为${automaticStatus.value.enabled ? '已开启' : '已关闭'}。`
      : '保存未确认；状态未知，请稍后刷新状态。'
  } finally {
    automaticToggleBusy.value = false
  }
}
onMounted(async () => {
  clockTimer = setInterval(() => { now.value = Date.now() }, 500)
  await Promise.all([loadOptions(), loadAutomaticRules()])
  await restorePendingPreview()
  await loadAutomaticStatus()
})
onUnmounted(() => { clearInterval(clockTimer); requestGeneration += 1 })
</script>

<template>
  <main class="manual-page" :data-host-theme="hostThemeName">
    <VContainer class="pa-4 pa-md-6" fluid>
    <VRow align="center" class="mb-2"><VCol><h1 class="text-h4 mb-1">Manko 整理</h1><p class="text-body-2 mb-0">自动整理下载完成的影片，也可手动选择目录；预览后再确认整理。源文件会保留。</p></VCol><VCol cols="auto"><VBtn data-testid="reload-options" variant="tonal" :disabled="formLocked" @click="loadOptions">重新加载</VBtn></VCol></VRow>
    <VCard data-testid="automatic-status" class="mb-4" variant="outlined">
      <VCardTitle class="d-flex flex-wrap align-center justify-space-between ga-3"><span>自动触发与匹配规则</span><div class="d-flex align-center ga-3"><VSwitch data-testid="automatic-toggle" label="自动触发" color="primary" hide-details :model-value="automaticStatus?.enabled === true" :disabled="automaticToggleDisabled" @update:model-value="setAutomaticEnabled"/><VChip data-testid="automatic-toggle-state" size="small" variant="tonal">{{ automaticStatusLabel }}</VChip></div></VCardTitle><VCardText>
        <VAlert class="mb-3" density="compact" type="info" variant="tonal">关闭自动触发不会取消已受理的整理。<template v-if="automaticStatus?.new_only === true">重新开启后只处理之后完成的下载。</template><template v-else-if="automaticStatus?.new_only === false">重新开启后也会处理启用前已完成的旧任务。</template></VAlert>
        <VAlert v-if="!automaticStatus" class="mb-3" density="compact" type="warning" variant="tonal">自动触发状态未获取；手动识别与预览仍可使用。</VAlert>
        <template v-else>
          <VAlert class="mb-2" density="compact" type="info" variant="tonal">{{ automaticModeLabel }} · {{ automaticStatus.armed ? '已启动' : '未启动' }}<span v-if="automaticStatus.started_at"> · 启动于 {{ formatAutomaticTime(automaticStatus.started_at) }}</span></VAlert>
          <p>{{ automaticStatus.new_only ? '仅处理启用后完成的下载；历史完成任务跳过。' : '会包含启用前已完成的旧任务。' }}</p>
          <dl class="automatic-details"><dt>匹配方式</dt><dd>{{ automaticMatchModeLabel }}</dd><dt>下载器 / 标签</dt><dd>{{ automaticStatus.downloader || '未设置' }} / {{ automaticStatus.torrent_tag || '未设置' }}</dd><dt>轮询</dt><dd>每 {{ automaticStatus.interval_seconds || 300 }} 秒；上次检查 {{ formatAutomaticTime(automaticStatus.last_poll_at) }}</dd><dt>来源目录</dt><dd>{{ automaticStatus.roots?.join('、') || '未设置' }}</dd><dt>目标目录</dt><dd>{{ automaticStatus.target_path || '未设置' }}</dd></dl>
          <p v-if="automaticCounts.length">计数：<VChip v-for="([key, value]) in automaticCounts" :key="key" class="ma-1" size="x-small" variant="tonal">{{ key }} {{ value }}</VChip></p>
          <VAlert v-for="pending in (automaticStatus.pending_actress_choices || [])" :key="pending.path" density="compact" type="warning" variant="tonal">待人工选择女主演：{{ pending.path }}。请在下方手动整理中选择该源并重新预览；自动流程不会重试。</VAlert>
          <VAlert v-if="automaticStatus.error" density="compact" type="error" variant="tonal">{{ automaticStatus.error }}</VAlert>
        </template>
        <div class="d-flex flex-wrap align-center ga-2"><VBtn data-testid="automatic-refresh" variant="tonal" :loading="automaticStatusBusy" :disabled="automaticToggleBusy" @click="loadAutomaticStatus">只读刷新状态</VBtn><VBtn data-testid="automatic-check" variant="text" :loading="automaticCheckBusy" :disabled="automaticToggleBusy" @click="checkAutomaticConditions">检查触发条件（只读）</VBtn></div>
        <VAlert v-if="automaticCheckMessage" class="mt-2" density="compact" type="info" variant="tonal">{{ automaticCheckMessage }}</VAlert><VAlert v-if="automaticToggleMessage" data-testid="automatic-toggle-message" class="mt-2" density="compact" type="info" variant="tonal">{{ automaticToggleMessage }}</VAlert>
        <VDivider class="my-4" />
        <h2 class="text-h6 mb-2">自动匹配规则</h2><p class="text-body-2">来源路径使用 MoviePilot 文件管理中可见的本地路径。硬链接要求来源与目标位于同一文件系统；不满足时会明确报错，不会改为复制。</p>
        <div class="automatic-rule-groups">
          <div class="automatic-rule-match-grid">
            <VSelect data-testid="automatic-match-mode" label="匹配方式" :items="[{title:'按专用标签',value:'tag'},{title:'按来源路径',value:'path'},{title:'任一满足（标签或路径）',value:'tag_or_path'},{title:'同时满足（标签且路径）',value:'tag_and_path'}]" item-title="title" item-value="value" :model-value="automaticRules.automatic_match_mode" :disabled="automaticRulesDisabled" @update:model-value="automaticRules.automatic_match_mode = $event" />
            <VSelect data-testid="automatic-downloader" label="qBittorrent 下载器" :items="automaticDownloaderItems" item-title="title" item-value="value" :model-value="automaticRules.downloader" :disabled="automaticRulesDisabled" @update:model-value="automaticRules.downloader = $event" />
            <VTextField data-testid="automatic-tag" label="专用标签" placeholder="如 Manko" :model-value="automaticRules.torrent_tag" :disabled="automaticRulesDisabled" @update:model-value="automaticRules.torrent_tag = $event" />
          </div>
          <div class="automatic-rule-path-grid">
            <div class="automatic-rule-path-control">
              <VTextarea data-testid="automatic-roots" label="来源目录（每行一个绝对路径）" rows="3" auto-grow :model-value="automaticRules.recognize_dirs" :disabled="automaticRulesDisabled" @update:model-value="automaticRules.recognize_dirs = $event" />
              <VBtn data-testid="browse-automatic-source" class="automatic-rule-browse" variant="tonal" :disabled="automaticRulesDisabled" @click="openBrowser('source','automatic')">浏览并追加来源目录</VBtn>
            </div>
            <div class="automatic-rule-path-control">
              <VTextField data-testid="automatic-target" label="目标媒体库目录" placeholder="/library" :model-value="automaticRules.target_path" :title="automaticRules.target_path || ''" :disabled="automaticRulesDisabled" @update:model-value="automaticRules.target_path = $event" />
              <VBtn data-testid="browse-automatic-target" class="automatic-rule-browse" variant="tonal" :disabled="automaticRulesDisabled" @click="openBrowser('target','automatic')">浏览目标目录</VBtn>
            </div>
          </div>
        </div>
        <VAlert v-if="automaticRulesMessage" data-testid="automatic-rules-message" class="mt-2" density="compact" :type="automaticRulesMessage.includes('未确认') || automaticRulesMessage.includes('未能') ? 'warning' : 'success'" variant="tonal">{{ automaticRulesMessage }}</VAlert>
        <div class="d-flex flex-wrap ga-2 mt-3"><VBtn data-testid="automatic-save-rules" color="primary" :loading="automaticRulesBusy" :disabled="automaticRulesDisabled || !automaticRulesCanSave" @click="saveAutomaticRules">保存自动规则</VBtn><VBtn data-testid="automatic-reload-rules" variant="tonal" :disabled="automaticRulesDisabled" @click="loadAutomaticRules">重新读取规则</VBtn></div>
      </VCardText>
    </VCard>
    <VAlert v-if="!options?.enabled && options" class="mb-3" type="warning" variant="tonal">此功能当前不可用。</VAlert>
    <VAlert v-if="error" class="mb-3" type="error" variant="tonal" role="alert">{{ error }}</VAlert>
    <VCard class="mb-4" variant="outlined"><VCardTitle>手动整理</VCardTitle><VCardText>
      <VRow>
        <VCol cols="12" md="6"><VTextField data-testid="source-path" label="来源目录或文件" placeholder="/downloads/..." :model-value="sourcePath" :disabled="formLocked" @update:model-value="setPath('source',$event)"><template #append><VBtn data-testid="browse-source" variant="tonal" :disabled="formLocked" @click="openBrowser('source')">浏览</VBtn></template></VTextField></VCol>
        <VCol cols="12" md="6"><VTextField data-testid="target-path" label="目标媒体库目录" placeholder="/library/..." :model-value="targetPath" :disabled="formLocked" @update:model-value="setPath('target',$event)"><template #append><VBtn data-testid="browse-target" variant="tonal" :disabled="formLocked" @click="openBrowser('target')">浏览</VBtn></template></VTextField></VCol>
        <VCol cols="12"><p class="text-body-2">路径使用 MoviePilot 文件管理中可见的本地路径。整理方式为硬链接，要求来源与目标在同一文件系统；条件不满足时会显示错误，不会改为复制。目标影片目录会交给 MoviePilot 完成后续刮削。</p></VCol>
        <VCol cols="12" md="6"><VSelect data-testid="media-source" label="识别来源" :items="options?.sources || []" item-title="title" item-value="value" :model-value="mediaSource" :disabled="formLocked" @update:model-value="setMediaSource" /></VCol>
        <VCol cols="12"><VSwitch data-testid="actress-subfolder" label="按女主演分目录" color="primary" hide-details :model-value="actressSubfolder" :disabled="formLocked" @update:model-value="setActressSubfolder" /><p class="text-body-2 mt-1">仅本次来源识别中标记 avdbs_actor=true 的演员可作为女主演；默认使用来源顺序第一位，也可在预览中选择其他可信候选并重新预览。没有可信标记时需等待来源数据补齐，不可从其他演员中选择。</p></VCol>
      </VRow>
      <div class="d-flex flex-wrap align-center justify-space-between ga-3"><VChip variant="tonal">整理方式：硬链接（保留来源文件）</VChip><VBtn data-testid="preview" color="primary" :loading="previewBusy" :disabled="previewBusy || formLocked || !options?.enabled || !sourcePath || !targetPath || !mediaSource" @click="createPreview">识别并预览</VBtn></div>
    </VCardText></VCard>
    <VCard v-if="preview" class="mb-4" variant="outlined">
          <VCardTitle class="d-flex flex-wrap justify-space-between ga-3"><span>预览结果</span><VChip :color="previewExpired || previewNeedsRefresh ? 'error' : 'success'" variant="tonal">{{ previewNeedsRefresh ? '选择已变更，请重新预览' : previewExpired ? '预览已过期' : `有效至 ${new Date(previewExpiryMs).toLocaleString()}` }}</VChip></VCardTitle>
      <VCardText><p>{{ preview.summary?.success || 0 }} 项可整理，{{ preview.summary?.failed || 0 }} 项失败</p>
        <VTable density="comfortable"><thead><tr><th>项目</th><th>来源路径</th><th>完整来源 ID</th><th>目标路径</th><th>结果</th></tr></thead><tbody><tr v-for="(item, index) in displayItems" :key="`${item.source}-${item.target}-${index}`"><td>{{ item.title || '—' }}<VSelect v-if="item.needs_actress_choice" :data-testid="`actress-choice-${index}`" class="mt-2" density="compact" label="演员候选（选择女主演）" :items="item.actor_candidates || []" :model-value="actressChoices[item.source] || item.selected_actress || null" :disabled="formLocked" @update:model-value="setActressChoice(item.source, $event)" /></td><td>{{ item.source }}</td><td>{{ item.media_id || '—' }}</td><td>{{ item.target || '—' }}</td><td>{{ itemStatus(item) }}</td></tr></tbody></VTable>
        <div class="d-flex flex-wrap align-center ga-2"><VBtn data-testid="execute" color="primary" :disabled="!canExecute" :loading="busy && outcome === 'pendingaccepted'" @click="execute">确认并整理</VBtn><VBtn v-if="outcome" data-testid="refresh-status" variant="tonal" :disabled="busy" :loading="busy" @click="refreshStatus">只读刷新状态</VBtn></div>
        <VAlert v-if="outcome === 'pendingaccepted'" class="mt-3" type="info" variant="tonal">请求已受理，正在等待结果。</VAlert><VAlert v-else-if="outcome === 'completed'" class="mt-3" type="success" variant="tonal">硬链接整理已核实；刮削结果请查看 MoviePilot 整理历史</VAlert><VAlert v-else-if="outcome === 'failed'" class="mt-3" type="error" variant="tonal">整理失败，原任务已结束。</VAlert><VAlert v-else-if="outcome === 'unknown'" class="mt-3" type="error" variant="tonal">结果未知，不能重复提交。</VAlert>
        <p v-if="preview.execution" class="text-body-2">任务 {{ preview.execution.state }}：{{ preview.execution.summary?.success || 0 }} 项成功，{{ preview.execution.summary?.failed || 0 }} 项失败</p>
      </VCardText>
    </VCard>
    <VDialog v-model="browser" max-width="720" scrollable aria-label="浏览目录"><VCard><VCardTitle class="d-flex align-center justify-space-between">选择{{ browsePurpose === 'automatic' && browseKind === 'source' ? '自动来源' : browseKind === 'source' ? '来源' : '目标' }}路径<VBtn icon="mdi-close" variant="text" aria-label="关闭目录浏览" @click="browser = false" /></VCardTitle><VCardText>
      <div v-if="browseKind === 'source' && options?.roots?.length" class="d-flex flex-wrap ga-2 mb-3"><VChip>常用来源目录</VChip><VBtn v-for="root in options.roots" :key="root" size="small" variant="tonal" :disabled="formLocked || automaticRulesBusy" @click="loadBrowse(root)">{{ root }}</VBtn></div>
      <VTextField label="当前路径" :model-value="browsePath" readonly />
      <div class="browse-toolbar">
        <VSelect data-testid="browse-sort" class="browse-sort" label="排序方式" density="compact" hide-details :items="browseSortItems" item-title="title" item-value="value" :model-value="browseSort" :disabled="formLocked || (browsePurpose === 'automatic' && automaticRulesBusy)" @update:model-value="browseSort = $event" />
        <div class="browse-navigation"><VBtn variant="tonal" :disabled="formLocked || automaticRulesBusy || !parentPath" @click="loadBrowse(parentPath)">返回上级目录</VBtn><VBtn variant="tonal" :disabled="formLocked || automaticRulesBusy" @click="loadBrowse('/')">从根目录浏览</VBtn><VBtn variant="tonal" :disabled="formLocked || automaticRulesBusy || !browsePath" @click="loadBrowse()">刷新当前目录</VBtn></div>
      </div>
      <VList density="compact"><VListItem v-for="item in sortedBrowseItems" :key="item.path" class="browse-item" :title="item.name" :subtitle="item.path" :disabled="browsePurpose === 'automatic' && browseKind === 'source' && item.type !== 'dir' && item.type !== 'directory'" data-testid="browse-item" @click="chooseItem(item)" /></VList>
    </VCardText><VCardActions><VSpacer/><VBtn variant="text" @click="browser = false">取消</VBtn><VBtn data-testid="choose-browse-directory" color="primary" :disabled="formLocked || automaticRulesBusy || !browsePath" @click="chooseCurrentDirectory">{{ browsePurpose === 'automatic' && browseKind === 'source' ? '追加此来源目录' : '选择此目录' }}</VBtn></VCardActions></VCard></VDialog>
    </VContainer>
  </main>
</template>

<style scoped>
.manual-page{min-height:100vh}.automatic-details{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 14px;margin:12px 0}.automatic-details dt{font-weight:600}.automatic-details dd{margin:0;overflow-wrap:anywhere}
.automatic-rule-groups{display:grid;gap:16px;margin-top:20px}.automatic-rule-match-grid,.automatic-rule-path-grid{display:grid;align-items:stretch;gap:12px 16px;min-width:0}.automatic-rule-match-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.automatic-rule-path-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.automatic-rule-path-control{display:flex;gap:8px;min-width:0;flex-direction:column;align-items:flex-start}.automatic-rule-path-control>.v-input{align-self:stretch;flex:0 0 auto}.automatic-rule-browse{margin-top:auto}
.browse-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:12px;min-width:0;margin-top:16px;margin-bottom:12px}.browse-sort{flex:0 1 300px;min-width:min(100%,220px)}.browse-navigation{display:flex;flex:1 1 420px;flex-wrap:wrap;gap:8px;min-width:0}.browse-item{min-width:0}:deep(.browse-item .v-list-item__content){min-width:0}:deep(.browse-item .v-list-item-title),:deep(.browse-item .v-list-item-subtitle){white-space:normal;overflow-wrap:anywhere}
@media(max-width:600px){.automatic-details{grid-template-columns:1fr;gap:2px}.automatic-details dd{margin-bottom:8px}}
@media(max-width:960px){.automatic-rule-match-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.automatic-rule-match-grid>:first-child{grid-column:1/-1}}
@media(max-width:600px){.automatic-rule-match-grid,.automatic-rule-path-grid{grid-template-columns:minmax(0,1fr)}.automatic-rule-match-grid>:first-child{grid-column:auto}.automatic-rule-path-control>.automatic-rule-browse{margin-top:0}}
</style>
