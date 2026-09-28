# -*- coding: utf-8 -*-
"""引擎 B 运行器：渲染 IronPython 模板 → 启动 InoProShop(--runscript) → 收集结果。

启动命令已经过 5 轮实测验证：
    InoProShop.exe --profile="InoProShop(V1.10.0.3)" --runscript="<script.py>" --noUI

已知约束（模板里已做防御处理）：
  1. 每次启动可能弹 UAC（需要用户点"是"），或"拒绝访问"（有管理员实例在运行时）；
     runner 负责检测这两种失败并给出 actionable 报错。
  2. projects.create 会抛"保存不在主线程"异常但工程其实已建好（模板内 catch 后继续）。
  3. save/create 等操作模板内优先用 system.execute_on_primary_thread 包装。

脚本协议（见 templates/_prelude.py.j2）：
  脚本把执行日志逐行写入 <script>.result.txt；结束时追加 ##RESULT## + JSON，
  runner 解析该 JSON 返回给 MCP 工具。
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import time
from typing import Any

INO_EXE = r"D:\Inovance Control\InoProShop\CODESYS\Common\InoProShop.exe"
INO_PROFILE = "InoProShop(V1.10.0.3)"
RESULT_MARKER = "##RESULT##"

WORK_DIR = os.path.join(os.path.dirname(__file__), "_work")
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
SCRIPT_TIMEOUT = 240   # 秒；编译大工程可能较慢

_tags = itertools.count(1)


class EngineBRunner:
    """P2/P3 工具共用：渲染模板 → 无界面 InoProShop 执行 → 返回结构化结果。"""

    def __init__(self, exe: str = INO_EXE, profile: str = INO_PROFILE):
        self.exe = exe
        self.profile = profile
        os.makedirs(WORK_DIR, exist_ok=True)
        from jinja2 import Environment, FileSystemLoader
        self._env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                                keep_trailing_newline=True)
        self._env.filters["pystr"] = lambda s: json.dumps(str(s))

    # ---------- 渲染 ----------
    def render(self, template_name: str, result_path: str, **params: Any) -> str:
        return self._env.get_template(template_name).render(
            result_path=result_path.replace("\\", "/"), **params)

    # ---------- 执行 ----------
    def execute(self, template_name: str, tag: str, **params: Any) -> dict[str, Any]:
        """渲染模板并执行，返回脚本写入的结果 dict（含 ok/error 字段）。"""
        script_path = os.path.join(WORK_DIR, f"{tag}_{int(time.time())}_{next(_tags)}.py")
        result_path = script_path + ".result.txt"
        script = self.render(template_name, result_path, **params)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)
        self._launch(script_path)
        return self._read_result(result_path)

    def _launch(self, script_path: str) -> None:
        from ..errors import McpError, translate_exception
        cmd = [self.exe, f"--profile={self.profile}",
               f"--runscript={script_path}", "--noUI"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=SCRIPT_TIMEOUT,
                           text=True, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise McpError(f"InoProShop 脚本执行超时（{SCRIPT_TIMEOUT}s）：可能是 UAC 未点击，"
                           "或有管理员权限的 InoProShop 实例在运行（请先关闭所有 InoProShop 窗口）")
        except PermissionError as e:
            raise translate_exception(e, "启动 InoProShop 失败")

    def _read_result(self, result_path: str) -> dict[str, Any]:
        from ..errors import McpError
        if not os.path.exists(result_path):
            raise McpError(
                "脚本未产生结果文件：脚本引擎没有运行起来。通常原因："
                "1) UAC 弹窗未点'是'  2) 有管理员权限的 InoProShop 实例在运行（请先关闭）")
        with open(result_path, "r", encoding="utf-8") as f:
            text = f.read()
        if RESULT_MARKER not in text:
            raise McpError("脚本未返回结构化结果（可能中途崩溃）。日志：\n" + text[-800:])
        payload = text.split(RESULT_MARKER, 1)[1].strip()
        try:
            return json.loads(payload)
        except Exception as e:
            raise McpError(f"结果 JSON 解析失败: {e}; 原文: {payload[:500]}")
