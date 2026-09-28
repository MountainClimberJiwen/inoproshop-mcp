# -*- coding: utf-8 -*-
"""引擎 A 运行时：连接注册表 + 轮询式 watch。

连接对象（OpcUaConnection / ModbusConnection）在同一事件循环内复用；
watch 用 asyncio 后台任务按间隔轮询，环形缓冲保留最近 N 条。
"""
from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from typing import Any

from ..errors import McpError
from .modbus_client import ModbusConnection
from .opcua_client import OpcUaConnection

_conn_ids = itertools.count(1)


class Watch:
    def __init__(self, watch_id: str, conn_id: str, names: list[str],
                 interval_ms: int, maxlen: int = 2000):
        self.watch_id = watch_id
        self.conn_id = conn_id
        self.names = names
        self.interval = interval_ms / 1000.0
        self.records: deque = deque(maxlen=maxlen)  # (seq, ts, values)
        self.seq = itertools.count(1)
        self.task: asyncio.Task | None = None
        self.error: str | None = None


class EngineA:
    """P1 核心。线程模型：单事件循环，所有方法 async。"""

    def __init__(self):
        self._conns: dict[str, Any] = {}
        self._watches: dict[str, Watch] = {}
        self._lock = asyncio.Lock()

    # ---------- 连接 ----------
    async def connect(self, host: str, protocol: str = "opcua", port: int | None = None,
                      **kwargs: Any) -> dict[str, Any]:
        protocol = protocol.lower()
        if protocol == "opcua":
            conn = OpcUaConnection(host=host, port=port or 4840, **kwargs)
        elif protocol == "modbus":
            conn = ModbusConnection(host=host, port=port or 502, **kwargs)
        else:
            raise McpError(f"未知协议 {protocol}，支持 opcua / modbus")
        info = await conn.connect()
        conn_id = f"conn{next(_conn_ids)}"
        async with self._lock:
            self._conns[conn_id] = conn
        info["conn_id"] = conn_id
        return info

    def resolve_conn(self, conn_id: str | None, default: str | None = None):
        cid = conn_id or default
        if not cid:
            raise McpError("尚未连接 PLC，请先调用 plc_connect")
        conn = self._conns.get(cid)
        if conn is None:
            raise McpError(f"连接 {cid} 不存在或已断开")
        return cid, conn

    async def disconnect(self, conn_id: str) -> dict[str, Any]:
        conn = self._conns.pop(conn_id, None)
        if conn is None:
            raise McpError(f"连接 {conn_id} 不存在")
        # 停掉该连接上的 watch
        for wid, w in list(self._watches.items()):
            if w.conn_id == conn_id:
                await self._stop_watch(wid)
        await conn.close()
        return {"disconnected": conn_id}

    async def close_all(self) -> None:
        for wid in list(self._watches):
            await self._stop_watch(wid)
        for conn in self._conns.values():
            try:
                await conn.close()
            except Exception:
                pass
        self._conns.clear()

    # ---------- 读写 ----------
    async def read_variables(self, names: list[str], conn_id: str | None,
                             default: str | None) -> dict[str, Any]:
        _, conn = self.resolve_conn(conn_id, default)
        if not names:
            raise McpError("names 不能为空")
        return await conn.read_variables(names)

    async def write_variable(self, name: str, value: Any, conn_id: str | None,
                             default: str | None) -> dict[str, Any]:
        _, conn = self.resolve_conn(conn_id, default)
        return await conn.write_variable(name, value)

    async def control(self, action: str, control_vars: dict[str, str],
                      conn_id: str | None, default: str | None) -> dict[str, Any]:
        _, conn = self.resolve_conn(conn_id, default)
        if action not in control_vars:
            raise McpError(
                f"配置中没有动作 {action} 对应的命令变量。"
                f"请在 config.json 的 control_vars 中配置，已有动作: {list(control_vars)}")
        return await conn.call_control(control_vars, action)

    # ---------- watch ----------
    async def watch_start(self, names: list[str], interval_ms: int,
                          conn_id: str | None, default: str | None) -> dict[str, Any]:
        cid, conn = self.resolve_conn(conn_id, default)
        watch_id = f"watch{next(_conn_ids)}"
        w = Watch(watch_id, cid, names, max(interval_ms, 50))
        w.task = asyncio.create_task(self._poll_loop(w, conn))
        self._watches[watch_id] = w
        return {"watch_id": watch_id, "variables": names, "interval_ms": interval_ms}

    async def _poll_loop(self, w: Watch, conn: Any) -> None:
        while True:
            try:
                values = await conn.read_variables(w.names)
                w.records.append((next(w.seq), time.time(), values))
            except asyncio.CancelledError:
                return
            except Exception as e:      # 读失败不退出，记录错误继续轮询
                w.error = str(e)[:200]
            await asyncio.sleep(w.interval)

    async def watch_read(self, watch_id: str, since_seq: int = 0) -> dict[str, Any]:
        w = self._watches.get(watch_id)
        if w is None:
            raise McpError(f"watch {watch_id} 不存在")
        records = [r for r in w.records if r[0] > since_seq]
        return {
            "watch_id": watch_id,
            "latest_seq": w.records[-1][0] if w.records else 0,
            "error": w.error,
            "records": [{"seq": s, "ts": ts, "values": v} for s, ts, v in records],
        }

    async def _stop_watch(self, watch_id: str) -> None:
        w = self._watches.pop(watch_id, None)
        if w and w.task:
            w.task.cancel()
            try:
                await w.task
            except Exception:
                pass

    async def watch_stop(self, watch_id: str) -> dict[str, Any]:
        if watch_id not in self._watches:
            raise McpError(f"watch {watch_id} 不存在")
        await self._stop_watch(watch_id)
        return {"stopped": watch_id}


engine_a = EngineA()
