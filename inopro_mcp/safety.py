# -*- coding: utf-8 -*-
"""安全分级：写操作与 PLC 控制需要显式确认。

工控场景下，误写变量或误停 PLC 的代价远高于多点一次确认。
默认策略：
  - 只读（read/watch/build/info）：直接执行
  - write_variable / plc_control：必须 confirm=True
  - force（P3）：单独开关 force_enabled，默认禁用
可在 config.json 中放宽（safety.level = "permissive"）。
"""
from __future__ import annotations

from .errors import McpError


def check_write_allowed(confirm: bool, safety_cfg: dict | None, what: str = "写入变量") -> None:
    cfg = safety_cfg or {}
    if cfg.get("level") == "permissive":
        return
    if not confirm:
        raise McpError(
            f"{what}属于高危操作：请显式传入 confirm=True 确认。"
            "如希望免确认（仅限测试环境），在 config.json 设置 safety.level=\"permissive\"。"
        )


def check_force_allowed(safety_cfg: dict | None) -> None:
    cfg = safety_cfg or {}
    if not cfg.get("force_enabled", False):
        raise McpError(
            "force（强制变量）会绕过 PLC 逻辑直写物理量，是最危险的操作，"
            "默认禁用。确有需要请在 config.json 设置 safety.force_enabled=true。"
        )
