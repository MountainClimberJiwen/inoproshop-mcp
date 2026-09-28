# -*- coding: utf-8 -*-
"""InoProShop / AM600 MCP Server —— 入口。

运行：
    python server.py            # 默认 stdio 传输（MCP 客户端标准接入方式）
    python server.py --test     # 自检：列出全部工具并做一次 dry-run

P1（引擎 A，PLC 直连）已实现；P2（工程/编译）与 P3（下载/在线/强制）为接口占位，
调用会得到明确的 "未实现" 错误而不是意外行为。
"""
from __future__ import annotations

import json
import os
import sys

from fastmcp import FastMCP

from inopro_mcp import engine_b, p3
from inopro_mcp.engine_a import engine_a
from inopro_mcp.errors import McpError, translate_exception
from inopro_mcp.safety import check_write_allowed
from inopro_mcp.state import SessionState

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


config = load_config()
state = SessionState()
mcp = FastMCP("inopro-shop-mcp")


def _ok(payload: dict) -> dict:
    return {"ok": True, **payload}


# ================= P1：运行时（引擎 A，已实现） =================

@mcp.tool()
async def plc_connect(host: str, protocol: str = "opcua", port: int | None = None,
                      user: str | None = None, password: str | None = None,
                      namespace: int = 4, app_name: str | None = None,
                      unit: int = 1, set_default: bool = True) -> dict:
    """连接 PLC。protocol: opcua(默认,端口4840) / modbus(端口502)。

    - opcua: 变量按符号名访问（如 "MAIN.counter"），namespace 默认 4，
      app_name 是应用名前缀，连不上符号时可尝试指定
    - modbus: 需要在 config.json 的 name_mapping 里配置变量地址表
    返回 conn_id，后续调用可省略（用默认连接）。
    """
    try:
        kwargs: dict = {}
        if protocol.lower() == "opcua":
            kwargs = dict(user=user, password=password, namespace=namespace,
                          app_name=app_name)
        else:
            mapping = config.get("modbus_name_mapping") or {}
            kwargs = dict(unit=unit, name_mapping=mapping)
        info = await engine_a.connect(host=host, protocol=protocol, port=port, **kwargs)
        state.remember_connection(info["conn_id"], {
            "host": host, "protocol": protocol, "port": info.get("port"),
        }, make_default=set_default)
        return _ok(info)
    except Exception as e:
        raise translate_exception(e, "plc_connect 失败")


@mcp.tool()
async def plc_disconnect(conn_id: str | None = None) -> dict:
    """断开 PLC 连接。不传 conn_id 则断开默认连接。"""
    try:
        cid, _ = engine_a.resolve_conn(conn_id, state.default_connection)
        await engine_a.disconnect(cid)
        state.drop_connection(cid)
        return _ok({"disconnected": cid})
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "plc_disconnect 失败")


@mcp.tool()
async def plc_info(conn_id: str | None = None) -> dict:
    """查看连接信息：当前默认连接、活跃连接数、watch 列表。"""
    return _ok({
        "default_connection": state.default_connection,
        "active_connections": list(engine_a._conns),
        "active_watches": {
            wid: {"conn_id": w.conn_id, "variables": w.names,
                  "records": len(w.records), "error": w.error}
            for wid, w in engine_a._watches.items()
        },
        "config_file": CONFIG_PATH if os.path.exists(CONFIG_PATH) else "未找到（使用默认值）",
    })


@mcp.tool()
async def read_variables(names: list[str], conn_id: str | None = None) -> dict:
    """批量读取 PLC 变量。names 例：["MAIN.counter", "MAIN.xStart"]。"""
    try:
        values = await engine_a.read_variables(names, conn_id, state.default_connection)
        return _ok({"values": values})
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "read_variables 失败")


@mcp.tool()
async def write_variable(name: str, value, conn_id: str | None = None,
                         confirm: bool = False) -> dict:
    """写 PLC 变量（高危，需 confirm=True）。

    value 类型按 Python 值推断（bool/int/float/str），建议先 read 确认变量类型。
    """
    try:
        check_write_allowed(confirm, config.get("safety"), "写 PLC 变量")
        result = await engine_a.write_variable(name, value, conn_id,
                                               state.default_connection)
        return _ok(result)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "write_variable 失败")


@mcp.tool()
async def watch_start(names: list[str], interval_ms: int = 500,
                      conn_id: str | None = None) -> dict:
    """启动轮询监控，返回 watch_id。用 watch_read 增量拉取，watch_stop 停止。"""
    try:
        return _ok(await engine_a.watch_start(names, interval_ms, conn_id,
                                              state.default_connection))
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "watch_start 失败")


@mcp.tool()
async def watch_read(watch_id: str, since_seq: int = 0, max_records: int = 500) -> dict:
    """读取监控记录（增量）。since_seq 传上次返回的 latest_seq 即可只拿新数据。"""
    try:
        result = await engine_a.watch_read(watch_id, since_seq)
        if len(result["records"]) > max_records:
            result["records"] = result["records"][-max_records:]
            result["truncated"] = True
        return _ok(result)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "watch_read 失败")


@mcp.tool()
async def watch_stop(watch_id: str) -> dict:
    """停止监控。"""
    try:
        return _ok(await engine_a.watch_stop(watch_id))
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "watch_stop 失败")


@mcp.tool()
async def plc_control(action: str, conn_id: str | None = None,
                      confirm: bool = False) -> dict:
    """PLC 控制（高危，需 confirm=True）：start/stop/reset 等。

    动作与命令变量的映射在 config.json 的 control_vars 中配置，例如：
        "control_vars": {"start": "MAIN.xStartCmd", "stop": "MAIN.xStopCmd"}
    实现方式为向命令变量写脉冲（1 → 0.5s → 0），命令的具体语义由 PLC 程序决定。
    """
    try:
        check_write_allowed(confirm, config.get("safety"), f"PLC 控制 {action}")
        control_vars = config.get("control_vars") or {}
        return _ok(await engine_a.control(action, control_vars, conn_id,
                                          state.default_connection))
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "plc_control 失败")


# ================= P2：工程 / POU / 编译（引擎 B，已实现） =================
# 注意：引擎 B 的每次调用都会启动一个无界面 InoProShop 实例（约 30 秒），
# 期间可能弹出 UAC 需要点"是"。

def _require_project() -> str:
    path = state.current_project
    if not path:
        raise McpError("没有已打开的工程：请先 project_open(path) 或 project_create(name, directory)")
    return path


@mcp.tool()
def project_open(path: str) -> dict:
    """打开工程并记为当前工程（后续 pou_*/build 隐式作用于它）。

    path: 工程文件绝对路径，如 D:\\projects\\myproj.project
    """
    try:
        result = engine_b.tools.project_open(path)
        if result.get("ok"):
            state.set("current_project", result.get("path", path))
        return result
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "project_open 失败")


@mcp.tool()
def project_create(name: str, directory: str) -> dict:
    """创建新工程并记为当前工程（会关闭 IDE 里已打开的工程）。"""
    try:
        result = engine_b.tools.project_create(name, directory)
        if result.get("ok"):
            state.set("current_project", result.get("path"))
        return result
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "project_create 失败")


@mcp.tool()
def project_close(save: bool = True) -> dict:
    """关闭当前工程（可选先保存），并清除会话中的当前工程记录。"""
    try:
        result = engine_b.tools.project_close(_require_project(), save=save)
        if result.get("ok"):
            state.set("current_project", None)
        return result
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "project_close 失败")


@mcp.tool()
def project_save() -> dict:
    """保存当前工程。"""
    try:
        return engine_b.tools.project_save(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "project_save 失败")


@mcp.tool()
def pou_list() -> dict:
    """列出当前工程所有 POU（名称/类型/语言）。"""
    try:
        return engine_b.tools.pou_list(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "pou_list 失败")


@mcp.tool()
def pou_read(name: str) -> dict:
    """读取 POU 声明区+实现区全文。name 如 "MAIN"。"""
    try:
        return engine_b.tools.pou_read(_require_project(), name)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "pou_read 失败")


@mcp.tool()
def pou_write(name: str, declaration: str | None = None,
              implementation: str | None = None) -> dict:
    """增量写 POU 代码：只替换提供的部分，未提供的部分保持原样。

    declaration: 完整声明区文本（含 PROGRAM/VAR 等，如未确定格式先 pou_read 看原文）
    implementation: 完整实现区文本（ST 代码体）
    """
    try:
        return engine_b.tools.pou_write(_require_project(), name,
                                        declaration, implementation)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "pou_write 失败")


@mcp.tool()
def pou_create(name: str, pou_type: str = "program", language: str = "st") -> dict:
    """新建 POU。type: program/functionblock/function；language 目前仅 st 经过验证。"""
    try:
        return engine_b.tools.pou_create(_require_project(), name, pou_type, language)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "pou_create 失败")


@mcp.tool()
def pou_delete(name: str) -> dict:
    """删除 POU。"""
    try:
        return engine_b.tools.pou_delete(_require_project(), name)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "pou_delete 失败")


@mcp.tool()
def build(rebuild: bool = False) -> dict:
    """编译当前工程，返回结构化诊断：errors[]/warnings[]。

    ok=true 表示 0 编译错误。约 30 秒（含 IDE 启动）。
    """
    try:
        return engine_b.tools.build(_require_project(), rebuild=rebuild)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "build 失败")


# ================= P3：下载 / 在线 / 强制（引擎 B online，已实现） =================

@mcp.tool()
def download_to_plc(start_after: bool = False, confirm: bool = False) -> dict:
    """编译并下载到 PLC（高危：会改变 PLC 运行内容，需 confirm=True）。

    start_after: 下载完成后启动应用。
    """
    try:
        check_write_allowed(confirm, config.get("safety"), "下载到 PLC")
        return p3.download_to_plc(_require_project(), start_after=start_after)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "download_to_plc 失败")


@mcp.tool()
def upload_from_plc() -> dict:
    """从 PLC 上载程序到当前工程（排查现场程序与工程不一致时用）。"""
    try:
        return p3.upload_from_plc(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "upload_from_plc 失败")


@mcp.tool()
def online_login() -> dict:
    """建立 IDE ↔ PLC 在线会话（下载/上载/控制前通常需要先 login）。"""
    try:
        return p3.online_login(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "online_login 失败")


@mcp.tool()
def online_logout() -> dict:
    """断开在线会话。"""
    try:
        return p3.online_logout(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "online_logout 失败")


@mcp.tool()
def online_start(confirm: bool = False) -> dict:
    """运行 PLC 应用（高危，需 confirm=True）。"""
    try:
        check_write_allowed(confirm, config.get("safety"), "启动 PLC 应用")
        return p3.online_start(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "online_start 失败")


@mcp.tool()
def online_stop(confirm: bool = False) -> dict:
    """停止 PLC 应用（高危，需 confirm=True）。"""
    try:
        check_write_allowed(confirm, config.get("safety"), "停止 PLC 应用")
        return p3.online_stop(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "online_stop 失败")


@mcp.tool()
def force_variable(name: str, value, confirm: bool = False) -> dict:
    """强制变量（最高危：绕过 PLC 逻辑直写物理量）。

    默认禁用：需在 config.json 设置 safety.force_enabled=true；
    每次调用仍需 confirm=True。
    """
    try:
        from inopro_mcp.safety import check_force_allowed
        check_force_allowed(config.get("safety"))
        check_write_allowed(confirm, config.get("safety"), "强制变量")
        return p3.force_variable(_require_project(), name, value)
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "force_variable 失败")


@mcp.tool()
def unforce_all(confirm: bool = False) -> dict:
    """解除全部强制变量（恢复程序控制，属修正性操作，但仍需确认）。"""
    try:
        check_write_allowed(confirm, config.get("safety"), "解除全部强制")
        return p3.unforce_all(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "unforce_all 失败")


@mcp.tool()
def create_boot_application() -> dict:
    """生成引导应用（写入 PLC 闪存，断电后程序保留）。"""
    try:
        return p3.create_boot_application(_require_project())
    except McpError:
        raise
    except Exception as e:
        raise translate_exception(e, "create_boot_application 失败")


# ================= 入口 =================

def self_test() -> None:
    import asyncio
    import inspect

    tools = mcp.list_tools()
    if inspect.iscoroutine(tools):
        tools = asyncio.new_event_loop().run_until_complete(tools)
    print(f"self-test: {len(tools)} tools registered")
    for t in tools:
        print(" -", t.name)
    # 状态文件读写检查
    state.set("_self_test", "ok")
    assert state.get("_self_test") == "ok"
    print("state file:", state.path)
    print("self-test PASSED")


if __name__ == "__main__":
    if "--test" in sys.argv:
        self_test()
    else:
        mcp.run()   # 默认 stdio
