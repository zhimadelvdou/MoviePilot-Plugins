import { defineComponent, h } from 'vue'
import Page from './Page.vue'

export default defineComponent({
  name: 'XiaojiejiescrapingLegacyPage',
  inheritAttrs: false,
  setup(_props, { attrs }) {
    return () => h(Page, attrs)
  },
})
