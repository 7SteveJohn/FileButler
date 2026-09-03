// pywebview 桥接：API 调用封装 + 后端事件订阅
const listeners = {}

window.__fbEvent = (data) => {
  const arr = listeners[data.name]
  if (arr) arr.forEach((fn) => fn(data.payload))
}

export function on(name, fn) {
  ;(listeners[name] = listeners[name] || []).push(fn)
  return () => {
    listeners[name] = (listeners[name] || []).filter((f) => f !== fn)
  }
}

// 实时汇报桥接状态（白屏防御：让顶层 UI 在后端未就绪时显示"等待/重试"）
let ready = false
let lastError = null
export function bridgeStatus() { return { ready, lastError } }
export function resetBridge() {
  // 允许调用方清空 apiPromise，下次 api() 重新等待 pywebview
  apiPromise = null
  ready = false
  lastError = null
}

function waitApi(timeout = 15000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now()
    const check = () => {
      if (window.pywebview && window.pywebview.api) {
        ready = true
        return resolve(window.pywebview.api)
      }
      if (Date.now() - t0 > timeout) {
        const e = new Error('pywebview API 未就绪（后端启动超时）')
        lastError = e
        return reject(e)
      }
      setTimeout(check, 100)
    }
    check()
  })
}

let apiPromise = null
export function api(method, ...args) {
  if (!apiPromise) apiPromise = waitApi()
  // 关键防御：任一次 api() 失败时，丢弃 apiPromise 让下次自动重连
  // 否则首次 waitApi 超时后整个应用将永久瘫痪（白屏的真正成因）
  return apiPromise.then((a) => a[method](...args), (err) => {
    apiPromise = null
    lastError = err
    throw err
  })
}
