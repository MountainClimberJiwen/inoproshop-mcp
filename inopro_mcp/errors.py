# -*- coding: utf-8 -*-
"""错误翻译层。

CoDeSys 脚本接口 / PLC 通信库的原始异常信息往往含糊
（如"调用的目标发生了异常"、"拒绝访问"），这里把常见失败模式
翻译成 actionable 的中文错误，并在合适时给出下一步建议。
"""
from __future__ import annotations

import re


class McpError(Exception):
    """返回给 MCP 客户端的友好错误。"""


class McpStubError(McpError):
    """P2/P3 未实现工具的占位错误。"""

    def __init__(self, tool: str, phase: str, note: str = ""):
        msg = f"[{phase} 未实现] 工具 {tool} 的接口已预留，实现尚未完成。"
        if note:
            msg += f" 实现提示：{note}"
        super().__init__(msg)


# (正则, 翻译模板) —— 按顺序匹配，第一个命中生效
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"BadNodeIdUnknown", re.I),
     "变量节点不存在：OPC UA 里找不到这个变量名。请确认 1) 变量名大小写 2) "
     "是否需要带应用名前缀（如 MyApp.MAIN.counter）3) 该变量是否已映射到 OPC UA 符号表"),
    (re.compile(r"BadConnectionRejected|ConnectionRefused|ConnectTimeout|TimeoutError|timed out", re.I),
     "连接被拒绝/超时：PLC 不可达。请确认 PLC IP、端口（OPC UA 默认 4840，Modbus TCP 默认 502）、"
     "以及 PLC 端对应服务是否已启用"),
    (re.compile(r"BadSecureChannelClosed|BadSessionClosed", re.I),
     "OPC UA 会话已断开，请重新 plc_connect"),
    (re.compile(r"BadUserAccessDenied|BadIdentityTokenInvalid", re.I),
     "OPC UA 认证失败：PLC 启用了用户认证，请在 plc_connect 传入 user/password"),
    (re.compile(r"BadTypeMismatch", re.I),
     "写入类型不匹配：value 的实际类型与 PLC 变量类型不符"),
    (re.compile(r"BadOutOfRange|BadNotWritable", re.I),
     "变量不可写或超出范围"),
    (re.compile(r"拒绝访问|Access is denied|AccessDenied", re.I),
     "Windows 拒绝访问：通常是有管理员权限的 InoProShop 实例正在运行，拦截了新实例启动。"
     "请关闭所有 InoProShop 窗口后重试"),
    (re.compile(r"不在主线程|primary thread", re.I),
     "操作需要在主线程执行（MCP 服务端应自动用 execute_on_primary_thread 包装后重试）"),
    (re.compile(r"没有一个非空的 Guid|non-empty Guid", re.I),
     "对象缺少 Guid：通常是设备树里还没有 Application，先检查设备/应用结构"),
]


def translate_exception(exc: BaseException, context: str = "") -> McpError:
    """把底层异常翻译成友好错误。未命中任何模式时透传原文。"""
    raw = str(exc) or exc.__class__.__name__
    for pat, msg in _PATTERNS:
        if pat.search(raw):
            return McpError((f"{context}：" if context else "") + msg + f"（原始信息：{raw[:200]}）")
    return McpError((f"{context}：" if context else "") + raw[:500])
