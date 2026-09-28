# -*- coding: utf-8 -*-
"""引擎 A：OPC UA 客户端（asyncua）。

CODESYS 系 PLC（汇川 AM600 等）的 OPC UA 符号寻址惯例：
    ns=<namespace>;s=|var|<Application>.<POU>.<变量名>
变量解析策略：按候选 NodeId 依次尝试读取，首个成功即缓存。
TODO(P1 增强)：browse 模式——连上后枚举整个符号表，自动发现变量。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from asyncua import Client, ua

DEFAULT_NAMESPACE = 4  # CODESYS OPC UA 符号默认在 ns=4


def _guess_variant(value: Any) -> ua.Variant:
    """按 Python 值猜测 OPC UA 数据类型。"""
    if isinstance(value, bool):
        return ua.Variant(value, ua.VariantType.Boolean)
    if isinstance(value, int):
        # 32 位有符号整型是 IEC INT/DINT 最常见的情况
        return ua.Variant(value, ua.VariantType.Int32)
    if isinstance(value, float):
        return ua.Variant(value, ua.VariantType.Double)
    return ua.Variant(str(value), ua.VariantType.String)


class OpcUaConnection:
    def __init__(self, host: str, port: int = 4840, namespace: int = DEFAULT_NAMESPACE,
                 user: str | None = None, password: str | None = None,
                 app_name: str | None = None, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.namespace = namespace
        self.app_name = app_name          # 应用名前缀，如 "MyApp"；None 时尝试无前缀
        self.timeout = timeout
        self._client = Client(url=f"opc.tcp://{host}:{port}/", timeout=timeout)
        if user:
            self._client.set_user(user)
            self._client.set_password(password)
        self._node_cache: dict[str, Any] = {}   # 变量名 -> node
        self._connected = False

    # ---------- 生命周期 ----------
    async def connect(self) -> dict[str, Any]:
        await self._client.connect()
        self._connected = True
        # 读 ServerStatus 验证通信并顺带报告版本
        status = await self._client.get_node("i=2258").read_value()
        return {
            "protocol": "opcua",
            "host": self.host,
            "port": self.port,
            "server_state": str(status.State),
            "build_info": str(status.BuildInfo.ProductName),
        }

    async def close(self) -> None:
        if self._connected:
            try:
                await self._client.disconnect()
            finally:
                self._connected = False
                self._node_cache.clear()

    # ---------- 变量解析 ----------
    def _candidate_nodeids(self, name: str) -> list[str]:
        cands = [f"ns={self.namespace};s=|var|{name}"]
        if self.app_name and not name.startswith(self.app_name + "."):
            cands.insert(0, f"ns={self.namespace};s=|var|{self.app_name}.{name}")
        return cands

    async def _resolve(self, name: str):
        node = self._node_cache.get(name)
        if node is not None:
            return node
        last_exc = None
        for nid in self._candidate_nodeids(name):
            node = self._client.get_node(nid)
            try:
                await node.read_value()   # 探测存在性
                self._node_cache[name] = node
                return node
            except Exception as e:        # BadNodeIdUnknown 等，换下一个候选
                last_exc = e
        raise last_exc

    # ---------- 读写 ----------
    async def read_variables(self, names: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in names:
            node = await self._resolve(name)
            result[name] = await node.read_value()
        return result

    async def write_variable(self, name: str, value: Any) -> dict[str, Any]:
        node = await self._resolve(name)
        await node.write_value(_guess_variant(value))
        return {"name": name, "written": value, "verified": await node.read_value()}

    # ---------- 控制 ----------
    async def call_control(self, control_vars: dict[str, str], action: str) -> dict[str, Any]:
        """通过配置的命令变量执行控制。

        control_vars: 动作名 -> 变量名，来自 config，例如
        {"start": "MAIN.xStartCmd", "stop": "MAIN.xStopCmd"}
        实现方式：把对应变量写 1，延时后写回 0（脉冲命令）。
        TODO(P1 增强)：若 PLC 暴露 OPC UA Method，优先调 Method。
        """
        var = control_vars.get(action)
        if not var:
            raise KeyError(f"配置中没有动作 {action} 对应的命令变量")
        await self.write_variable(var, True)
        await asyncio.sleep(0.5)
        await self.write_variable(var, False)
        return {"action": action, "via": var, "ts": time.time()}
