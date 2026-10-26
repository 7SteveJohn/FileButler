"""Ollama 模型目录（免 Ollama，沙盒数据目录）：
候选目录扫描只认「真装着模型」的目录 / 模型目录设置会作为 OLLAMA_MODELS 传给
应用自己拉起的 ollama serve / 用户手动开的 Ollama 不在管辖内（无 PID 时不重启）。

运行方式（必须给一次性 APPDATA，目录名需含 fb_，否则拒绝运行防止写真实库）：
  APPDATA=<临时目录> python tests/test_ollama_models_dir.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db, ollama_client  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def _seed_models_dir(root, n):
    """造一个「装着 n 个模型」的目录：manifests 下每个模型一个文件。"""
    man = os.path.join(root, "manifests", "registry.ollama.ai", "library")
    os.makedirs(man, exist_ok=True)
    for i in range(n):
        with open(os.path.join(man, f"model{i}.json"), "w", encoding="utf-8") as f:
            f.write("{}")
    return root


def main():
    appdata = os.environ.get("APPDATA", "")
    if "fb_" not in appdata.lower():
        print("拒绝运行：请设置一次性 APPDATA（路径含 fb_），防止写进真实库。")
        print('  例：APPDATA=C:\\tmp\\fb_modelsdir python tests/test_ollama_models_dir.py')
        return 1
    db.init_db()

    base = tempfile.mkdtemp(prefix="fb_modelsdir_", dir=appdata)
    with_models = _seed_models_dir(os.path.join(base, "with_models"), 3)
    empty = os.path.join(base, "empty_dir")
    os.makedirs(os.path.join(empty, "manifests"), exist_ok=True)
    missing = os.path.join(base, "no_such_dir")

    check("装着模型的目录能数出来", ollama_client.count_manifests(with_models) == 3,
          str(ollama_client.count_manifests(with_models)))
    check("空 manifests 算 0 个", ollama_client.count_manifests(empty) == 0)
    check("目录不存在算 0 个", ollama_client.count_manifests(missing) == 0)

    # 扫描只返回装了模型的目录，空的/不存在的都不出现
    ollama_client.candidate_model_dirs = lambda: [with_models, empty, missing, with_models]
    found = ollama_client.installed_model_dirs()
    check("只返回装了模型的目录", len(found) == 1 and
          os.path.normcase(found[0]["path"]) == os.path.normcase(with_models), str(found))
    check("返回模型数量", found and found[0]["n_models"] == 3, str(found))

    # 拉起 ollama serve 时把设置带进子进程环境
    from backend.api import Api  # 延迟导入：api 会拉起 pywebview 依赖的模块
    db.set_setting("ollama_models_dir", "")
    env0 = Api._ollama_serve_env()
    check("没设置时不塞 OLLAMA_MODELS", "OLLAMA_MODELS" not in env0, str(env0.get("OLLAMA_MODELS")))
    check("没设置时继承原环境", env0.get("PATH") == os.environ.get("PATH"))

    db.set_setting("ollama_models_dir", with_models)
    env1 = Api._ollama_serve_env()
    check("设置了就带上 OLLAMA_MODELS", env1.get("OLLAMA_MODELS") == with_models,
          str(env1.get("OLLAMA_MODELS")))

    # 没有自管进程时不重启（用户手动开的 Ollama 不能碰）
    api = Api.__new__(Api)
    api._ollama_pid = None
    api._ollama_proc = None
    check("没有自管进程时不重启", api._restart_ollama() is False)

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
