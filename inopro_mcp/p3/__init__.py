# -*- coding: utf-8 -*-
"""P3 工具组：下载 / 上载 / 在线控制 / 强制。

实现方式：engine_b 的 online_action.py.j2 模板按 action 分发，
走 IScriptOnlineApplication 的 login/start/stop/source_download/
upload_source/force 等方法（方法名来自官方 API dump）。

每个调用都是全新 IDE 实例，project_path 指定的工程会先由 _prelude 打开。
注意：在线语义（特别是 download 的入口方法）在汇川定制版上可能与预期不同，
模板失败时会自动附加对象的方法清单，便于快速修正。
"""
from __future__ import annotations

from typing import Any

from ..engine_b.runner import EngineBRunner

runner = EngineBRunner()


def run(action: str, project_path: str, **params: Any) -> dict[str, Any]:
    return runner.execute("online_action.py.j2", f"online_{action}",
                          project_path=project_path, action=action, **params)


def download_to_plc(project_path: str, start_after: bool = False) -> dict[str, Any]:
    """编译产物下载到 PLC。"""
    return run("download", project_path, start_after=start_after)


def upload_from_plc(project_path: str) -> dict[str, Any]:
    """从 PLC 上载程序到工程。"""
    return run("upload", project_path)


def online_login(project_path: str) -> dict[str, Any]:
    """在线登录（建立 IDE ↔ PLC 会话）。"""
    return run("login", project_path)


def online_logout(project_path: str) -> dict[str, Any]:
    """在线登出。"""
    return run("logout", project_path)


def online_start(project_path: str) -> dict[str, Any]:
    """运行 PLC 应用（高危，server 层已做 confirm 检查）。"""
    return run("start", project_path)


def online_stop(project_path: str) -> dict[str, Any]:
    """停止 PLC 应用（高危，server 层已做 confirm 检查）。"""
    return run("stop", project_path)


def force_variable(project_path: str, name: str, value: Any) -> dict[str, Any]:
    """强制变量（最高危，server 层已做安全开关检查）。"""
    import json as _json
    if isinstance(value, bool):
        literal = "True" if value else "False"
    elif isinstance(value, (int, float)):
        literal = repr(value)
    else:
        literal = _json.dumps(str(value))
    return run("force", project_path, var_name=name, var_literal=literal)


def unforce_all(project_path: str) -> dict[str, Any]:
    """解除全部强制。"""
    return run("unforce_all", project_path)


def create_boot_application(project_path: str) -> dict[str, Any]:
    """生成引导应用（断电保持）。"""
    return run("boot", project_path)
