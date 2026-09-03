import webview, time

errs = []
def on_loaded():
    time.sleep(3)
    try:
        state = window.evaluate_js('document.getElementById("app").innerHTML.substring(0, 500)')
        print("INNER:", repr(state))
        n = window.evaluate_js('document.getElementById("app").children.length')
        print("CHILDREN:", n)
    except Exception as e:
        print("EVAL ERR:", e)
    window.destroy()

window = webview.create_window('Test', 'http://localhost:5173/', js_api=None, width=800, height=600)
window.events.loaded += on_loaded
webview.start()
