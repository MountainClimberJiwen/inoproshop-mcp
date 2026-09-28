# -*- coding: utf-8 -*-
"""渲染全部模板（不启动 IDE），检查 jinja 语法和生成脚本的 Python 语法。"""
import py_compile
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inopro_mcp.engine_b.runner import EngineBRunner, TEMPLATES_DIR

runner = EngineBRunner()
templates = [f for f in os.listdir(TEMPLATES_DIR) if f.endswith(".py.j2") and not f.startswith("_")]
print(f"{len(templates)} templates to render")

CASES = {
    "project_open.py.j2": dict(path=r"D:\proj\demo.project"),
    "project_create.py.j2": dict(name="Demo", directory=r"D:\proj"),
    "project_save.py.j2": dict(project_path=r"D:\proj\demo.project"),
    "project_close.py.j2": dict(project_path=r"D:\proj\demo.project", save=True),
    "pou_list.py.j2": dict(project_path=r"D:\proj\demo.project"),
    "pou_read.py.j2": dict(project_path=r"D:\proj\demo.project", pou_name="MAIN"),
    "pou_write.py.j2": dict(project_path=r"D:\proj\demo.project", pou_name="MAIN",
                            declaration="PROGRAM MAIN\nVAR\n    c : INT;\nEND_VAR",
                            implementation="c := c + 1;"),
    "pou_create.py.j2": dict(project_path=r"D:\proj\demo.project", pou_name="FB1",
                             pou_type="functionblock", language="st"),
    "pou_delete.py.j2": dict(project_path=r"D:\proj\demo.project", pou_name="OLD"),
    "build.py.j2": dict(project_path=r"D:\proj\demo.project", rebuild=False),
    "build.py.j2#rebuild": dict(project_path=r"D:\proj\demo.project", rebuild=True),
    "online_action.py.j2#login": dict(project_path=r"D:\proj\demo.project", action="login"),
    "online_action.py.j2#download": dict(project_path=r"D:\proj\demo.project", action="download",
                                         start_after=True),
    "online_action.py.j2#force": dict(project_path=r"D:\proj\demo.project", action="force",
                                      var_name="MAIN.xValve", var_literal="True"),
    "online_action.py.j2#upload": dict(project_path=r"D:\proj\demo.project", action="upload"),
}

fail = 0
for t in templates:
    for key, params in CASES.items():
        base = key.split("#")[0]
        if base != t:
            continue
        try:
            out = runner.render(t, r"D:\out\result.txt", **params)
        except Exception as e:
            print(f"FAIL render {key}: {e}")
            fail += 1
            continue
        # 生成的 IronPython 脚本用本机 python 做语法检查（语法兼容）
        fd, tmpname = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(out)
        try:
            py_compile.compile(tmpname, doraise=True)
        except py_compile.PyCompileError as e:
            print(f"FAIL syntax {key}: {e}")
            fail += 1
        finally:
            os.unlink(tmpname)
        print(f"ok {key}")

print("RESULT:", "ALL PASS" if fail == 0 else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
