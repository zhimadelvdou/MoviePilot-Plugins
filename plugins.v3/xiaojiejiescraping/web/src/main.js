import { createApp } from 'vue'
import Page from './Page.vue'

const unavailableApi = {
  async get() { throw new Error('请在 MoviePilot 插件页面中打开此界面。') },
  async post() { throw new Error('请在 MoviePilot 插件页面中打开此界面。') },
}

createApp(Page, { api: unavailableApi, pluginId: 'xiaojiejiescraping', sourcePluginId: '' }).mount('#app')
