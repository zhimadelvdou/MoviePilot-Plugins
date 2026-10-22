import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = process.env.MOVIEPILOT_FRONTEND_DEPS
if (!root) throw new Error('MOVIEPILOT_FRONTEND_DEPS must point to the installed MoviePilot frontend')
const require = createRequire(path.join(root, 'package.json'))
const vue = require('@vitejs/plugin-vue')
const federation = require('@originjs/vite-plugin-federation')
const vuetify = require.resolve('vuetify')
const vuetifyComponents = require.resolve('vuetify/components')
const vuePlugin = vue.default ?? vue
const here = path.dirname(fileURLToPath(import.meta.url))

export default {
  root: here,
  plugins: [vuePlugin(), federation({
    name: 'xiaojiejiescraping',
    filename: 'remoteEntry.js',
    exposes: {
      './AppPage': path.join(here, 'src/Page.vue'),
      './Page': path.join(here, 'src/PageAlias.js'),
      './Config': path.join(here, 'src/Config.vue'),
    },
    shared: {
      vue: { requiredVersion: false, generate: false, singleton: true },
      vuetify: { requiredVersion: false, generate: false, singleton: true },
    },
  })],
  resolve: { alias: [
    { find: /^vue$/, replacement: path.join(root, 'node_modules/vue/dist/vue.esm-bundler.js') },
    { find: /^vuetify$/, replacement: vuetify },
    { find: /^vuetify\/components$/, replacement: vuetifyComponents },
    { find: '@vue/test-utils', replacement: path.join(root, 'node_modules/@vue/test-utils') },
    { find: '@originjs/vite-plugin-federation', replacement: require.resolve('@originjs/vite-plugin-federation') },
  ], dedupe: ['vue', 'vuetify'] },
  build: { target: 'esnext', cssCodeSplit: false, outDir: path.join(here, 'dist'), assetsDir: 'assets', emptyOutDir: true },
  test: { environment: 'jsdom', css: true, server: { deps: { inline: ['vuetify'] } }, include: ['tests/**/*.test.js'] },
}
