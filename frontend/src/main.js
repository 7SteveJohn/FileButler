import { createApp } from 'vue'
import naive from 'naive-ui'
import App from './App.vue'
import { i18n } from './lib/i18n'
import './style.css'

const app = createApp(App)
app.config.errorHandler = (err, _vm, info) => {
  console.error('[FileButler UI Error]', info, err)
}
app.use(naive).use(i18n).mount('#app')
