"""AI 分类实测（需 Ollama 运行）。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.core import classifier

sb = tempfile.mkdtemp(prefix="fb_ai_")
# (文件名, 期望类别, 内容)
tests = [
    ("setup_xxx_v2.exe", "安装包", b"MZ\x90\x00" + b"\x00" * 100),
    ("sunset_photo_backup", "图片", b"\xff\xd8\xff\xe1exifdata"),
    ("meeting_notes_2024", "文档", "会议纪要 2024-03-15 参会人：张三，讨论项目进度".encode("utf-8")),
    ("backup_20240315", "其他", b"\x00\x01\x02binary mystery"),
]
files = []
for name, expected, content in tests:
    p = os.path.join(sb, name)
    with open(p, "wb") as f:
        f.write(content)
    files.append({"path": p, "name": name, "ext": os.path.splitext(name)[1].lstrip(".").lower(),
                  "size": len(content), "mtime": time.time(), "rel": name})

t0 = time.time()
result = classifier.ai_classify(files)
ok = 0
for name, expected, _ in tests:
    f = next(x for x in files if x["name"] == name)
    cat, reason = result[f["path"]]
    mark = "OK  " if cat == expected else "MISS"
    ok += cat == expected
    print(f"{mark} {name} -> {cat} ({reason[:50]})")
print(f"耗时 {time.time()-t0:.1f}s，{ok}/{len(tests)} 符合预期")
