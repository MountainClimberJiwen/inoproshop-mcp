# inopro-shop-mcp

用自然语言驱动汇川 AM600 系列 PLC 的 MCP 插件：日常监控读写走 **PLC 直连**（OPC UA / Modbus，无 UAC、毫秒级），开发编译走 **InoProShop 脚本引擎**（CODESYS 原生 API）。

## 架构

```
┌──────────────── MCP Server（常驻，stdio，无 UAC）───────────────┐
│  引擎 A（P1 ✅）：OPC UA / Modbus TCP 直连 PLC                  │
│    plc_connect / read_variables / write_variable /            │
│    watch_start|read|stop / plc_control                        │
│  引擎 B（P2 🔲）：生成 IronPython → InoProShop --runscript      │
│    project_* / pou_* / build（接口已就位，模板待写）              │
│  P3 🔲：download_to_plc / upload_from_plc / online_* / force   │
└───────────────────────────────────────────────────────────────┘
```

关键设计（均来自 9 轮实测结论）：

- **双引擎**：90% 的日常调用（监控、读写变量）不启动 IDE、不弹 UAC；只有改代码/编译才启动 InoProShop
- **无法注入已运行实例**：`--runscript` 只在进程启动时执行一次，引擎 B 每次调用开新实例、跑完即退
- **安全分级**：写变量 / PLC 控制需 `confirm=True`；force 默认禁用（`config.json` 的 `safety` 段）
- **错误翻译**：CoDeSys 含糊异常（"拒绝访问"→ 管理员实例在运行等）统一翻译成可行动的中文提示

## 安装与运行

```bash
pip install -r requirements.txt
copy config.example.json config.json   # 按需修改 control_vars / modbus 映射
python server.py --test                # 自检：28 个工具注册
python server.py                       # stdio 模式，接入 MCP 客户端
```

MCP 客户端（Kimi / Claude Desktop 等）配置示例：

```json
{
  "mcpServers": {
    "inopro-shop": {
      "command": "python",
      "args": ["D:\\KimiData\\kimi\\tasks\\2026-09-27\\21-39-34-46f25197\\inopro-mcp\\server.py"]
    }
  }
}
```

## 工具一览

**P1（已实现）**

| 工具 | 说明 |
| --- | --- |
| `plc_connect` | 连接 PLC（opcua 默认端口 4840 / modbus 端口 502），返回 conn_id |
| `plc_disconnect` / `plc_info` | 断开 / 查看连接与 watch 状态 |
| `read_variables` | 批量读变量，如 `["MAIN.counter"]` |
| `write_variable` | 写变量（高危，需 `confirm=True`） |
| `watch_start` / `watch_read` / `watch_stop` | 轮询监控，seq 增量拉取 |
| `plc_control` | start/stop/reset（高危；向 config 配置的命令变量写脉冲） |

**P2（已实现，引擎 B）**：`project_open/create/close/save`、`pou_list/read/write/create/delete`、`build`（返回结构化 errors/warnings）

**P3（已实现，引擎 B online 组）**：`download_to_plc`、`upload_from_plc`、`online_login/logout/start/stop`、`force_variable`、`unforce_all`、`create_boot_application`

## 使用前提

1. PLC 与电脑网络可达，且 PLC 端启用了 OPC UA 服务（或 Modbus TCP）
2. OPC UA 变量访问需要变量在符号映射中（InoProShop 工程的 OPC UA 配置里勾选）
3. P2/P3 需要本机装有 InoProShop V1.10.0.3（路径配置在 `inopro_mcp/engine_b/runner.py`）

## 路线图

- **P1** ✅ 引擎 A 全量实现（OPC UA / Modbus 直连）
- **P2** ✅ 引擎 B 全量实现：11 个 IronPython 模板（`engine_b/templates/`），
  覆盖工程生命周期 / POU 增删读写 / 编译诊断；`python test_templates.py` 可做渲染回归
- **P3** ✅ 引擎 B online 组实现：`online_action.py.j2` 按动作分发
  （login/logout/start/stop/download/upload/force/unforce/boot）
- 待实测校准（真机连 PLC 后可能需微调）：
  - `download`/`upload` 的具体入口方法（模板失败时自动反射对象方法清单辅助修正）
  - `pou_list` 的属性名探测（pou_type/language 候选属性已内置多候选回退）
- 未来：`build`/`download` 改 job 异步模式；OPC UA browse 自动发现变量；免 UAC 计划任务方案

## 目录结构

```
server.py                  MCP 入口（FastMCP，stdio）
config.example.json        安全策略 / 控制变量映射 / Modbus 地址表
test_templates.py          引擎 B 模板渲染 + 语法回归测试
inopro_mcp/
  state.py                 会话状态（当前工程/默认连接，JSON 持久化）
  errors.py                错误翻译层
  safety.py                写/控制/强制 三级安全
  engine_a/                P1：OPC UA + Modbus 客户端、连接注册表、watch
  engine_b/                P2/P3：runner（渲染+执行）+ 11 个 IronPython 模板
  p3/                      P3：online 工具（download/upload/login/force…）
```
