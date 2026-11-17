#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inoproshop-mcp — InoProShop (汇川, 基于 CODESYS V3.5) MCP server。

零依赖（纯标准库）、stdio JSON-RPC。只保留与 InoProShop 交互的基础能力：
    环境自检 / 工程备份 / 读对象 / 写对象 / 编译 / 在线连接状态。

所有需要 IDE 的操作都通过 InoProShop.exe --runscript=<脚本> 在 IDE 进程内执行
（IronPython 2.7 + scriptengine）。宿主进程必须以管理员身份运行，否则 IDE 类工具
会因 requires elevation 失败；env_check 等不启动 IDE 的工具不受影响。

用法：
    python server.py               # 作为 stdio MCP server 运行
    python server.py --selfcheck   # 离线自检（不启动 IDE）
    python server.py --list-tools  # 打印工具清单
"""

from __future__ import print_function

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

VERSION = "0.1.0"
SERVER_NAME = "inoproshop-mcp"

DEFAULT_EXE = r"D:\Inovance Control\InoProShop\CODESYS\Common\InoProShop.exe"
DEFAULT_PROFILE = "InoProShop(V1.10.0.3)"
DONE_MARKER = "_done.txt"
DEFAULT_TIMEOUT = 180


# --------------------------------------------------------------------------- #
# 配置（全部可用环境变量覆盖）
# --------------------------------------------------------------------------- #
def _exe_path():
    return os.environ.get("INOPRO_EXE") or DEFAULT_EXE


def _profile_name():
    return os.environ.get("INOPRO_PROFILE") or DEFAULT_PROFILE


def _timeout():
    try:
        return int(os.environ.get("INOPRO_TIMEOUT") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT


# --------------------------------------------------------------------------- #
# IDE 脚本模板（在 IDE 进程内被执行）
# --------------------------------------------------------------------------- #
WRAP = u'''# -*- coding: utf-8 -*-
import io, os, traceback
import scriptengine as se
OUTDIR = r"__OUTDIR__"
def wdone(msg=u"ok"):
    try:
        io.open(os.path.join(OUTDIR, "_done.txt"), "w", encoding="utf-8").write(msg)
    except Exception:
        pass
try:
%(body)s
except Exception:
    try:
        io.open(os.path.join(OUTDIR, "_error.txt"), "w", encoding="utf-8").write(traceback.format_exc())
    except Exception:
        pass
    wdone(u"error")
'''

_OPEN = (
    u'proj = se.projects.open(r"__PROJECT__", '
    u'update_flags=se.VersionUpdateFlags.NoUpdates | se.VersionUpdateFlags.SilentMode)\n'
    u'app = proj.active_application\n'
    u'if not app:\n'
    u'    app = proj.find("Application", True)[0]\n'
)


def _indent(body):
    """把脚本体整体缩进 4 空格，塞进模板的 try 块。"""
    lines = []
    for line in body.splitlines():
        lines.append(("    " + line) if line.strip() else "")
    return "\n".join(lines)


def _render(body, project=None):
    script = WRAP % {"body": _indent(body)}
    if project is not None:
        script = script.replace("__PROJECT__", project)
    return script


def body_list_objects(project):
    return _OPEN + u'''
rows = []

def walk(node):
    try:
        name = node.get_name()
    except Exception:
        name = u"?"
    decl = u""
    impl = u""
    try:
        h = node.textual_declaration
        if h:
            try: decl = h.get_text() or u""
            except Exception: decl = u""
    except Exception: pass
    try:
        h = node.textual_implementation
        if h:
            try: impl = h.get_text() or u""
            except Exception: impl = u""
    except Exception: pass
    if decl or impl:
        rows.append((name, len(decl), len(impl)))
    try:
        for c in node.get_children(False):
            walk(c)
    except Exception: pass

walk(app)
with io.open(os.path.join(OUTDIR, "objects.tsv"), "w", encoding="utf-8") as f:
    for n, d, i in rows:
        f.write(u"%s\\t%d\\t%d\\n" % (n, d, i))
wdone()
'''


def body_read_object(project):
    return _OPEN + u'''
name = io.open(os.path.join(OUTDIR, "meta.txt"), encoding="utf-8").read().strip()
found = app.find(name, True)
if not found:
    io.open(os.path.join(OUTDIR, "_error.txt"), "w", encoding="utf-8").write(u"object not found: " + name)
    wdone(u"error")
else:
    obj = found[0]
    decl = u""
    impl = u""
    try:
        h = obj.textual_declaration
        if h:
            try: decl = h.get_text() or u""
            except Exception: decl = u""
    except Exception: pass
    try:
        h = obj.textual_implementation
        if h:
            try: impl = h.get_text() or u""
            except Exception: impl = u""
    except Exception: pass
    io.open(os.path.join(OUTDIR, "decl.txt"), "w", encoding="utf-8").write(decl)
    io.open(os.path.join(OUTDIR, "impl.txt"), "w", encoding="utf-8").write(impl)
    wdone()
'''


def body_write_object(project):
    return _OPEN + u'''
meta = io.open(os.path.join(OUTDIR, "meta.txt"), encoding="utf-8").read().strip().split("|")
name = meta[0]
kind = meta[1] if len(meta) > 1 and meta[1] else u"auto"
decl = io.open(os.path.join(OUTDIR, "decl.txt"), encoding="utf-8").read()
impl = u""
impl_path = os.path.join(OUTDIR, "impl.txt")
if os.path.exists(impl_path):
    impl = io.open(impl_path, encoding="utf-8").read()

created = False
found = app.find(name, True)
obj = found[0] if found else None
if obj is None:
    created = True
    if kind == u"dut":
        obj = app.create_dut(name=name, type=se.DutType.Structure)
    elif kind == u"gvl":
        obj = app.create_gvl(name=name)
    elif kind == u"pou":
        obj = app.create_pou(name=name, type=se.PouType.Function, language=None, return_type=u"VOID")
    else:
        head = decl.lstrip().upper()
        if head.startswith(u"FUNCTION_BLOCK"):
            obj = app.create_pou(name=name, type=se.PouType.FunctionBlock, language=None)
        elif head.startswith(u"FUNCTION"):
            obj = app.create_pou(name=name, type=se.PouType.Function, language=None, return_type=u"VOID")
        elif head.startswith(u"PROGRAM"):
            obj = app.create_pou(name=name, type=se.PouType.Program, language=None)
        else:
            obj = app.create_pou(name=name, type=se.PouType.Function, language=None, return_type=u"VOID")

if decl:
    try:
        obj.textual_declaration.replace(decl)
    except Exception as e:
        io.open(os.path.join(OUTDIR, "_warn.txt"), "a", encoding="utf-8").write(u"decl replace: " + unicode(e) + u"\\n")
if impl:
    try:
        obj.textual_implementation.replace(impl)
    except Exception as e:
        io.open(os.path.join(OUTDIR, "_warn.txt"), "a", encoding="utf-8").write(u"impl replace: " + unicode(e) + u"\\n")
proj.save()
wdone(u"created" if created else u"updated")
'''


def body_rebuild(project):
    return _OPEN + u'''
app.rebuild()
wdone()
'''


def body_online(project):
    # meta.txt: action=status|connect|login|logout / user= / password= / change_option= / delete_foreign=
    return _OPEN + u'''
meta = {}
for line in io.open(os.path.join(OUTDIR, "meta.txt"), encoding="utf-8"):
    line = line.strip()
    if "=" in line:
        k, v = line.split("=", 1)
        meta[k] = v
action = meta.get("action", u"status")

def note(msg):
    io.open(os.path.join(OUTDIR, "note.txt"), "a", encoding="utf-8").write(msg + u"\\n")

if meta.get("user"):
    try:
        se.online.set_default_credentials(meta["user"], meta.get("password", u""))
    except Exception as e:
        note(u"set_default_credentials: " + unicode(e))

od = None
oa = None
try:
    od = se.online.create_online_device()
except Exception as e:
    note(u"create_online_device: " + unicode(e))
try:
    oa = se.online.create_online_application(app)
except Exception as e:
    note(u"create_online_application: " + unicode(e))

def call(obj, method):
    try:
        return unicode(getattr(obj, method)())
    except Exception as e:
        return u"error: " + unicode(e)

lines = []
if action == u"status":
    if od is not None:
        lines.append(u"connected=" + call(od, "get_connected"))
    if oa is not None:
        lines.append(u"logged_in=" + call(oa, "get_is_logged_in"))
        lines.append(u"app_state=" + call(oa, "get_application_state"))
        lines.append(u"op_state=" + call(oa, "get_operation_state"))
    io.open(os.path.join(OUTDIR, "status.tsv"), "w", encoding="utf-8").write(u"\\n".join(lines))
elif action == u"connect":
    if od is None:
        raise RuntimeError(u"no online device")
    od.connect()
elif action == u"login":
    if oa is None:
        raise RuntimeError(u"no online application")
    oa.login(int(meta.get("change_option", u"0")), meta.get("delete_foreign", u"false").lower() == u"true")
elif action == u"logout":
    if oa is None:
        raise RuntimeError(u"no online application")
    oa.logout()
else:
    raise RuntimeError(u"unknown action: " + action)
wdone()
'''


# --------------------------------------------------------------------------- #
# IDE 启动与结果收集
# --------------------------------------------------------------------------- #
def _launch(script_text, stage):
    """写脚本 -> 启 IDE -> 等 _done.txt -> 返回结果。"""
    script_path = os.path.join(stage, "runscript.py")
    with io.open(script_path, "w", encoding="utf-8") as f:
        f.write(script_text)

    exe = _exe_path()
    if os.name != "nt":
        return {"ok": False, "error": "InoProShop 只能在 Windows 上运行（当前系统: %s）" % os.name}
    if not os.path.exists(exe):
        return {"ok": False, "error": "InoProShop.exe 不存在: %s（可用 INOPRO_EXE 指定）" % exe}

    # 不内嵌引号：Windows subprocess 会自己加引号，内嵌引号会被二次转义
    cmd = [exe, "--profile=" + _profile_name(), "--runscript=" + script_path]
    # stdout/stderr 落文件：既不占用管道缓冲（不读 PIPE 会死锁），又留下排障现场
    log_out = open(os.path.join(stage, "ide_stdout.log"), "wb")
    log_err = open(os.path.join(stage, "ide_stderr.log"), "wb")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(exe),
            stdout=log_out,
            stderr=log_err,
            creationflags=0,  # 保留窗口，用户需要点掉 profile 选择 / 插件缺失弹窗
        )
    except Exception as e:
        hint = ""
        if "elevation" in str(e).lower() or getattr(e, "winerror", 0) == 740:
            hint = "（请以管理员身份启动 MCP 宿主）"
        return {"ok": False, "error": "启动 InoProShop 失败: %s%s" % (e, hint)}
    finally:
        log_out.close()
        log_err.close()

    marker = os.path.join(stage, DONE_MARKER)
    deadline = time.time() + _timeout()
    while time.time() < deadline:
        if os.path.exists(marker):
            return {"ok": True, "error": None}
        if proc.poll() is not None:
            time.sleep(1.0)
            if os.path.exists(marker):
                return {"ok": True, "error": None}
            return {"ok": False, "error": "IDE 退出但没有产出结果（returncode=%s）" % proc.returncode}
        time.sleep(1.0)

    try:
        proc.terminate()
    except Exception:
        pass
    return {"ok": False, "error": "超时 %ds：IDE 未产出 %s（弹窗没点掉？）" % (_timeout(), DONE_MARKER)}


def _safe_read(path):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _run(body, project, files=None, tag="call"):
    stage = tempfile.mkdtemp(prefix="inopromcp_")
    for name, content in (files or {}).items():
        with io.open(os.path.join(stage, name), "w", encoding="utf-8", newline="") as f:
            f.write(content)
    script = _render(body, project).replace("__OUTDIR__", stage)
    res = _launch(script, stage)
    out = {"ok": res["ok"]}
    if res["error"]:
        out["error"] = res["error"]
    err = _safe_read(os.path.join(stage, "_error.txt"))
    if err:
        out["ok"] = False
        out["script_error"] = err.strip()
    warn = _safe_read(os.path.join(stage, "_warn.txt"))
    if warn:
        out["warnings"] = warn.strip()
    note = _safe_read(os.path.join(stage, "note.txt"))
    if note:
        out["notes"] = note.strip()
    out["_stage"] = stage
    return out


def _project_ok(path):
    if not isinstance(path, str) or not path:
        return "project_path 必填"
    if not path.lower().endswith(".project"):
        return "project_path 应指向 .project 文件: %s" % path
    if not os.path.exists(path):
        return "工程文件不存在: %s" % path
    return None


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #
def tool_env_check(args):
    exe = _exe_path()
    project = args.get("project_path")
    profiles_dir = os.path.join(os.path.dirname(os.path.dirname(exe)), "Profiles")
    profiles = []
    if os.path.isdir(profiles_dir):
        profiles = sorted([n for n in os.listdir(profiles_dir) if n.lower().endswith(".profile")])
    return {
        "ok": True,
        "platform": os.name,
        "python": sys.version.split()[0],
        "exe": exe,
        "exe_exists": os.path.exists(exe),
        "profile": _profile_name(),
        "available_profiles": profiles,
        "timeout_seconds": _timeout(),
        "project_path": project,
        "project_exists": os.path.exists(project) if project else None,
        "hint": "IDE 类工具需要 MCP 宿主以管理员身份运行；profile 名以 Profiles\\*.profile 文件名为准。",
    }


def tool_backup_project(args):
    src = args.get("project_path")
    err = _project_ok(src)
    if err:
        return {"ok": False, "error": err}
    backup_dir = args.get("backup_dir") or os.path.join(os.path.dirname(src), "backup")
    os.makedirs(backup_dir, exist_ok=True)
    folder = os.path.dirname(src)
    base = os.path.splitext(os.path.basename(src))[0]
    copied = []
    for name in os.listdir(folder):
        if name == os.path.basename(src) or name.startswith(base + ".project") or name.startswith(base + ".autosave"):
            dst = os.path.join(backup_dir, name)
            shutil.copy2(os.path.join(folder, name), dst)
            copied.append(dst)
    return {"ok": True, "backup_dir": backup_dir, "count": len(copied), "copied": copied}


def tool_list_objects(args):
    project = args.get("project_path")
    err = _project_ok(project)
    if err:
        return {"ok": False, "error": err}
    out = _run(body_list_objects(project), project, tag="list")
    rows = []
    for line in _safe_read(os.path.join(out.pop("_stage"), "objects.tsv")).splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append({"name": parts[0], "decl_len": int(parts[1]), "impl_len": int(parts[2])})
    out["count"] = len(rows)
    out["objects"] = rows
    return out


def tool_read_object(args):
    project = args.get("project_path")
    err = _project_ok(project)
    if err:
        return {"ok": False, "error": err}
    name = args.get("name")
    if not name:
        return {"ok": False, "error": "name 必填（对象名，如 PLC_PRG / GVL_Main / ST_State）"}
    out = _run(body_read_object(project), project, {"meta.txt": name}, tag="readobj")
    stage = out.pop("_stage")
    out["name"] = name
    out["declaration"] = _safe_read(os.path.join(stage, "decl.txt"))
    out["implementation"] = _safe_read(os.path.join(stage, "impl.txt"))
    return out


def tool_write_object(args):
    project = args.get("project_path")
    err = _project_ok(project)
    if err:
        return {"ok": False, "error": err}
    name = args.get("name")
    declaration = args.get("declaration")
    if not name:
        return {"ok": False, "error": "name 必填"}
    if not declaration:
        return {"ok": False, "error": "declaration 必填（声明行 + VAR..END_VAR；主体逻辑放 implementation）"}
    kind = args.get("kind") or "auto"
    if kind not in ("auto", "dut", "gvl", "pou"):
        return {"ok": False, "error": "kind 只能是 auto/dut/gvl/pou"}
    files = {"meta.txt": name + "|" + kind, "decl.txt": declaration}
    impl = args.get("implementation")
    if impl:
        files["impl.txt"] = impl
    out = _run(body_write_object(project), project, files, tag="write")
    out.pop("_stage")
    out["name"] = name
    return out


def tool_rebuild(args):
    project = args.get("project_path")
    err = _project_ok(project)
    if err:
        return {"ok": False, "error": err}
    out = _run(body_rebuild(project), project, tag="rebuild")
    out.pop("_stage")
    out["note"] = "已触发 rebuild；脚本引擎不暴露编译错误/警告，需在 IDE 消息窗口查看。"
    return out


def tool_online(args):
    project = args.get("project_path")
    err = _project_ok(project)
    if err:
        return {"ok": False, "error": err}
    action = args.get("action") or "status"
    if action not in ("status", "connect", "login", "logout"):
        return {"ok": False, "error": "action 只能是 status/connect/login/logout"}

    lines = ["action=" + action]
    for key in ("user", "password"):
        if args.get(key):
            lines.append("%s=%s" % (key, args[key]))
    if action == "login":
        lines.append("change_option=%d" % int(args.get("change_option") or 0))
        lines.append("delete_foreign=%s" % ("true" if args.get("delete_foreign") else "false"))

    out = _run(body_online(project), project, {"meta.txt": "\n".join(lines)}, tag="online")
    stage = out.pop("_stage")
    out["action"] = action
    status = _safe_read(os.path.join(stage, "status.tsv"))
    if status:
        out["status"] = dict(
            line.split("=", 1) for line in status.splitlines() if "=" in line
        )
    return out


# --------------------------------------------------------------------------- #
# 工具注册
# --------------------------------------------------------------------------- #
TOOLS = [
    {
        "name": "env_check",
        "description": "检查 InoProShop 安装路径、profile、可选工程文件是否存在（不启动 IDE）。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_path": {"type": "string", "description": "可选：要检查的 .project 路径"}},
        },
        "fn": tool_env_check,
    },
    {
        "name": "backup_project",
        "description": "备份 .project 及其 .projectbackup/.autosave 到指定目录（写工程前建议先做）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": ".project 文件绝对路径"},
                "backup_dir": {"type": "string", "description": "备份目录，默认工程目录下 backup/"},
            },
            "required": ["project_path"],
        },
        "fn": tool_backup_project,
    },
    {
        "name": "list_objects",
        "description": "列出工程中所有 DUT/GVL/POU 及声明、实现长度（启动 IDE）。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_path": {"type": "string"}},
            "required": ["project_path"],
        },
        "fn": tool_list_objects,
    },
    {
        "name": "read_object",
        "description": "读单个对象的声明 + 实现全文（启动 IDE）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string"},
                "name": {"type": "string", "description": "对象名，如 PLC_PRG"},
            },
            "required": ["project_path", "name"],
        },
        "fn": tool_read_object,
    },
    {
        "name": "write_object",
        "description": "创建或覆盖一个对象的声明/实现并保存工程（启动 IDE）。声明放 declaration，BEGIN 之后的主体逻辑放 implementation。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string"},
                "name": {"type": "string"},
                "declaration": {"type": "string", "description": "POU 声明行 + VAR..END_VAR；或 DUT/GVL 全文"},
                "implementation": {"type": "string", "description": "可选：BEGIN 之后的主体逻辑"},
                "kind": {"type": "string", "enum": ["auto", "dut", "gvl", "pou"], "default": "auto"},
            },
            "required": ["project_path", "name", "declaration"],
        },
        "fn": tool_write_object,
    },
    {
        "name": "rebuild",
        "description": "触发一次 rebuild（启动 IDE）。不返回编译错误，错误需在 IDE 消息窗口看。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_path": {"type": "string"}},
            "required": ["project_path"],
        },
        "fn": tool_rebuild,
    },
    {
        "name": "online",
        "description": "与 PLC 的在线连接：action=status 读连接/登录状态，connect 连接设备，login 登录应用，logout 退出登录。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string"},
                "action": {"type": "string", "enum": ["status", "connect", "login", "logout"], "default": "status"},
                "user": {"type": "string", "description": "可选：PLC 登录用户名"},
                "password": {"type": "string", "description": "可选：PLC 登录密码"},
                "change_option": {"type": "integer", "enum": [0, 1, 2], "default": 0,
                                  "description": "OnlineChangeOption：0 Never / 1 Try / 2 Force"},
                "delete_foreign": {"type": "boolean", "default": False},
            },
            "required": ["project_path"],
        },
        "fn": tool_online,
    },
]

TOOL_MAP = dict((t["name"], t["fn"]) for t in TOOLS)


# --------------------------------------------------------------------------- #
# JSON-RPC stdio 循环
# --------------------------------------------------------------------------- #
def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(rid, payload, is_error=False):
    return {
        "jsonrpc": "2.0", "id": rid,
        "result": {
            "isError": is_error,
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        },
    }


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in TOOLS
        ]}}
    if method == "tools/call":
        name = params.get("name")
        fn = TOOL_MAP.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": "unknown tool: %s" % name}}
        try:
            return _result(rid, fn(params.get("arguments") or {}))
        except Exception as e:
            return _result(rid, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, is_error=True)
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "unknown method: %s" % method}}


def serve():
    for attr in ("stdin", "stdout"):
        stream = getattr(sys, attr)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle(req)
        if resp is not None:
            _send(resp)


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def selfcheck():
    checks = []

    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    checks.append(("initialize", init["result"]["serverInfo"]["name"] == SERVER_NAME))

    listing = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    checks.append(("tools registered (%d)" % len(listing), len(listing) == len(TOOLS)))

    call = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "env_check", "arguments": {}}})
    payload = json.loads(call["result"]["content"][0]["text"])
    checks.append(("env_check runs", payload.get("ok") is True))

    demo = "C:/demo/Test.project"
    rendered = _render(body_write_object(demo), demo).replace("__OUTDIR__", "C:/tmp/stage")
    checks.append(("template rendered", "__OUTDIR__" not in rendered and "se.projects.open" in rendered))
    checks.append(("indent preserved", "\n    proj = se.projects.open" in rendered))

    bad = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "read_object", "arguments": {"project_path": "x.txt"}}})
    bad_payload = json.loads(bad["result"]["content"][0]["text"])
    checks.append(("invalid project rejected", bad_payload.get("ok") is False))

    failed = 0
    for name, ok in checks:
        print("%-32s %s" % (name, "OK" if ok else "FAIL"))
        failed += 0 if ok else 1

    print("\nSELF-CHECK PASSED" if failed == 0 else "\nSELF-CHECK FAILED (%d)" % failed)
    return 0 if failed == 0 else 1


def main(argv):
    if "--selfcheck" in argv:
        return selfcheck()
    if "--list-tools" in argv:
        for t in TOOLS:
            print("%-16s %s" % (t["name"], t["description"]))
        return 0
    if "--version" in argv:
        print("%s %s" % (SERVER_NAME, VERSION))
        return 0
    serve()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
