# -*- coding: utf-8 -*-
"""会话状态管理：当前 PLC 连接、当前工程、watch 订阅等持久化到本地 JSON。

MCP server 是常驻进程，状态放在内存 + JSON 双写，server 重启后
"当前工程"等上下文不丢。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

_LOCK = threading.Lock()

DEFAULT_STATE_DIR = os.path.join(os.path.expanduser("~"), ".inopro_mcp")
DEFAULT_STATE_FILE = os.path.join(DEFAULT_STATE_DIR, "state.json")

_DEFAULTS: dict[str, Any] = {
    "current_project": None,      # 当前打开的工程绝对路径（P2 起使用）
    "default_connection": None,   # 默认 PLC 连接 ID
    "connections": {},            # conn_id -> 连接参数（不含运行时句柄）
    "safety": {"write_needs_confirm": True, "control_needs_confirm": True},
}


def _load(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return dict(_DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged
    except Exception:
        return dict(_DEFAULTS)


def _save(path: str, state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class SessionState:
    """线程安全的会话状态存取。"""

    def __init__(self, path: str = DEFAULT_STATE_FILE):
        self.path = path
        with _LOCK:
            self._state = _load(path)

    def get(self, key: str, default: Any = None) -> Any:
        with _LOCK:
            return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with _LOCK:
            self._state[key] = value
            _save(self.path, self._state)

    def update(self, mapping: dict[str, Any]) -> None:
        with _LOCK:
            self._state.update(mapping)
            _save(self.path, self._state)

    # --- 常用语义的便捷方法 ---
    @property
    def current_project(self) -> str | None:
        return self.get("current_project")

    @property
    def default_connection(self) -> str | None:
        return self.get("default_connection")

    def remember_connection(self, conn_id: str, params: dict[str, Any],
                            make_default: bool = True) -> None:
        conns = dict(self.get("connections") or {})
        conns[conn_id] = params
        self.update({"connections": conns})
        if make_default or not self.default_connection:
            self.set("default_connection", conn_id)

    def drop_connection(self, conn_id: str) -> None:
        conns = dict(self.get("connections") or {})
        conns.pop(conn_id, None)
        self.update({"connections": conns})
        if self.default_connection == conn_id:
            self.set("default_connection", next(iter(conns), None))
