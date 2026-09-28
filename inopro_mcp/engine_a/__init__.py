# -*- coding: utf-8 -*-
"""引擎 A：运行时直连 PLC（OPC UA / Modbus TCP）。

P1 已实现。对外由 runtime.engine_a 单例提供服务。
"""
from .opcua_client import OpcUaConnection
from .modbus_client import ModbusConnection
from .runtime import EngineA, engine_a

__all__ = ["OpcUaConnection", "ModbusConnection", "EngineA", "engine_a"]
