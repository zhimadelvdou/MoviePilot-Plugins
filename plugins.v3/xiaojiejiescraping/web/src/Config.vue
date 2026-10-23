<script setup>
import { computed, reactive, watch } from 'vue'
import { useTheme } from 'vuetify'

const props = defineProps({ initialConfig: { type: Object, default: () => ({}) }, api: { type: Object, default: null }, pluginId: { type: [String, Number], default: '' }, sourcePluginId: { type: [String, Number], default: '' } })
const hostTheme = useTheme()
const hostThemeName = computed(() => hostTheme.global.name.value)
const emit = defineEmits(['save', 'close'])
const defaults = { enabled: false, sources: ['manko'], storage: 'local', recognize_dirs: '', image_hosts: '', discovery_enabled: false, auto_new_only: true, automatic_match_mode: 'tag_or_path', downloader: '', torrent_tag: '', actor_images: false, actress_subfolder: false, handoff_mode: 'discover', target_path: '', transfer_type: '', ownership_ack: false, auxiliary_ack: false, handoff_limit: 1 }
function configDefaults(value) {
  const config = value || {}
  return { ...defaults, ...(Object.keys(config).length && !Object.prototype.hasOwnProperty.call(config, 'automatic_match_mode') ? { automatic_match_mode: 'tag_and_path' } : {}), ...config }
}
const model = reactive(configDefaults(props.initialConfig))
watch(() => props.initialConfig, (value) => Object.assign(model, configDefaults(value)), { deep: true })
const sourceText = computed({ get: () => Array.isArray(model.sources) ? model.sources.join(', ') : '', set: value => { model.sources = value.split(',').map(item => item.trim()).filter(Boolean) } })
const boolFields = [['enabled','启用已完成的来源能力'],['discovery_enabled','启用下载完成轮询（每5分钟）'],['auto_new_only','仅处理启用后完成的下载'],['actress_subfolder','按女主演分目录'],['actor_images','启用演员缩略图'],['ownership_ack','已排除 MoviePilot 原生自动监控'],['auxiliary_ack','已理解宿主可能补充辅助字段']]
</script>

<template>
  <main class="config-page" :data-host-theme="hostThemeName"><VContainer class="pa-4 pa-md-6" fluid>
    <VRow align="center" class="mb-2"><VCol><div class="text-overline">XIAOJIEJIE SCRAPING</div><h1 class="text-h4 mb-1">插件配置</h1></VCol><VCol cols="auto"><VBtn variant="text" @click="emit('close')">关闭</VBtn></VCol></VRow>
    <VAlert class="mb-4" type="warning" variant="tonal">自动流程模式默认为只读发现；来源与真实整理仍需预览核对。保存会保留所有现有及未知配置项。</VAlert>
    <VCard variant="outlined"><VCardTitle>插件能力</VCardTitle><VCardText>
      <VRow>
        <VCol v-for="[key, title] in boolFields" :key="key" cols="12" md="6"><VSwitch v-model="model[key]" :data-testid="key === 'enabled' ? 'config-enabled' : `config-${key}`" :label="title" color="primary" hide-details /><p v-if="key === 'discovery_enabled'" class="text-body-2 mt-1">由已配置的 qBittorrent 匹配规则触发。</p><p v-else-if="key === 'auto_new_only'" class="text-body-2 mt-1">关闭后也会包含启用前已完成的旧任务。</p></VCol>
        <VCol cols="12" md="6"><VTextField v-model="sourceText" data-testid="config-sources" label="元数据来源（逗号分隔）" /></VCol>
        <VCol cols="12" md="6"><VTextField v-model="model.storage" data-testid="config-storage" label="存储" /></VCol>
        <VCol cols="12" md="6"><VTextarea v-model="model.recognize_dirs" data-testid="config-recognize-dirs" label="自动来源目录（每行一个绝对路径）" rows="3" /></VCol>
        <VCol cols="12" md="6"><VSelect v-model="model.automatic_match_mode" data-testid="config-automatic-match-mode" label="自动匹配方式" :items="[{title:'按专用标签',value:'tag'},{title:'按来源路径',value:'path'},{title:'任一满足（标签或路径）',value:'tag_or_path'},{title:'同时满足（标签且路径）',value:'tag_and_path'}]" item-title="title" item-value="value" /></VCol>
        <VCol cols="12" md="6"><VTextField v-model="model.downloader" data-testid="config-downloader" label="qBittorrent 下载器" /></VCol>
        <VCol cols="12" md="6"><VTextField v-model="model.torrent_tag" data-testid="config-torrent-tag" label="专用标签" /></VCol>
        <VCol cols="12" md="6"><VSelect v-model="model.handoff_mode" data-testid="config-handoff-mode" label="自动流程模式" :items="[{title:'只读发现',value:'discover'},{title:'自动预览',value:'preview'},{title:'自动整理并刮削',value:'execute'}]" item-title="title" item-value="value" /></VCol>
        <VCol cols="12" md="6"><VTextField v-model="model.target_path" data-testid="config-target-path" label="自动目标媒体库目录" /></VCol>
        <VCol cols="12" md="6"><VSelect v-model="model.transfer_type" data-testid="config-transfer-type" label="整理方式" :items="[{title:'未设置',value:''},{title:'复制',value:'copy'},{title:'硬链接',value:'link'}]" item-title="title" item-value="value" /></VCol>
        <VCol cols="12" md="6"><VTextField v-model.number="model.handoff_limit" data-testid="config-handoff-limit" label="每轮最多处理的新文件" type="number" min="1" max="5" /></VCol>
        <VCol cols="12"><VTextarea v-model="model.image_hosts" data-testid="config-image-hosts" label="允许的图片 HTTPS 主机（每行一个）" rows="3" /></VCol>
      </VRow>
    </VCardText><VCardActions><VSpacer/><VBtn data-testid="config-save" color="primary" @click="emit('save', { ...model, sources: [...model.sources] })">保存配置</VBtn></VCardActions></VCard>
  </VContainer></main>
</template>

<style scoped>
.config-page{min-height:100vh}.config-page h1{font-weight:500}
</style>
