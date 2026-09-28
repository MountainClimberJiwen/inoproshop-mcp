# -*- coding: utf-8 -*-
"""引擎 A：Modbus TCP 客户端（pymodbus）。

Modbus 没有符号名概念，变量必须通过地址映射表访问：
config 中 name_mapping 示例：
    {"counter": {"address": 0, "type": "int16", "writable": true},
     "speed":   {"address": 2, "type": "float32"}}
TODO(P1 增强)：从 InoProShop 工程导出变量地址表自动生成映射。
"""
from __future__ import annotations

import struct
from typing import Any

from pymodbus.client import AsyncModbusTcpClient

_TYPE_FMT = {
    "int16": ("h", 1), "uint16": ("H", 1),
    "int32": ("i", 2), "uint32": ("I", 2),
    "float32": ("f", 2), "float64": ("d", 4),
    "bool": ("?", 1),
}


class ModbusConnection:
    def __init__(self, host: str, port: int = 502, unit: int = 1,
                 name_mapping: dict[str, dict] | None = None,
                 timeout: float = 5.0):
        self.host = host
        self.port = port
        self.unit = unit
        self.name_mapping = name_mapping or {}
        self._client = AsyncModbusTcpClient(host=host, port=port, timeout=timeout)

    # ---------- 生命周期 ----------
    async def connect(self) -> dict[str, Any]:
        ok = await self._client.connect()
        if not ok:
            raise ConnectionError(f"无法连接 {self.host}:{self.port}")
        return {"protocol": "modbus", "host": self.host, "port": self.port,
                "unit": self.unit, "mapped_variables": len(self.name_mapping)}

    async def close(self) -> None:
        self._client.close()

    # ---------- 读写 ----------
    def _spec(self, name: str) -> dict[str, Any]:
        spec = self.name_mapping.get(name)
        if not spec:
            raise KeyError(
                f"Modbus 模式下变量 {name} 没有地址映射，请在 config 的 name_mapping 中配置")
        return spec

    async def read_variables(self, names: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in names:
            spec = self._spec(name)
            addr, (fmt, words) = int(spec["address"]), _TYPE_FMT[spec.get("type", "int16")]
            rr = await self._client.read_holding_registers(address=addr, count=words,
                                                           slave=self.unit)
            if rr.isError():
                raise RuntimeError(f"读 {name} 失败: {rr}")
            raw = struct.pack(">" + "H" * words, *rr.registers)
            result[name] = struct.unpack(">" + fmt, raw)[0]
        return result

    async def write_variable(self, name: str, value: Any) -> dict[str, Any]:
        spec = self._spec(name)
        if not spec.get("writable", False):
            raise PermissionError(f"变量 {name} 在映射表中未标记 writable")
        addr, (fmt, words) = int(spec["address"]), _TYPE_FMT[spec.get("type", "int16")]
        raw = struct.pack(">" + fmt, value)
        registers = list(struct.unpack(">" + "H" * words, raw))
        wr = await self._client.write_registers(address=addr, values=registers,
                                                slave=self.unit)
        if wr.isError():
            raise RuntimeError(f"写 {name} 失败: {wr}")
        verified = await self.read_variables([name])
        return {"name": name, "written": value, "verified": verified[name]}

    async def call_control(self, control_vars: dict[str, str], action: str) -> dict[str, Any]:
        var = control_vars.get(action)
        if not var:
            raise KeyError(f"配置中没有动作 {action} 对应的命令变量")
        await self.write_variable(var, True)
        await self.write_variable(var, False)
        return {"action": action, "via": var}
