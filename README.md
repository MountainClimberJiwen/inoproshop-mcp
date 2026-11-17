# inoproshop-mcp

用自然语言驱动 InoProShop（汇川，基于 CODESYS V3.5 OEM）的 MCP server：**单文件、零依赖、7 个工具**。

覆盖「打开工程 → 读代码 → 写代码 → 编译 → 连 PLC」这条最小闭环：

- **单文件**：所有逻辑都在 `server.py`，纯 Python 标准库，不需要 `pip install`。
- **7 个工具**：只保留基础操作，不提供会改动设备运行状态的高危动作。
- **自带自检**：`python server.py --selfcheck` 不启动 IDE 就能验证协议、工具注册和脚本模板。

## 工具清单（7 个）

| 工具 | 启动 IDE | 说明 |
|---|---|---|
| `env_check` | 否 | 检查 exe 路径、profile、可选工程文件是否存在 |
| `backup_project` | 否 | 备份 `.project` 及 `.projectbackup` / `.autosave` |
| `list_objects` | 是 | 列出全部 DUT/GVL/POU 及声明/实现长度 |
| `read_object` | 是 | 读单个对象的声明 + 实现全文 |
| `write_object` | 是 | 新建或覆盖对象并保存工程 |
| `rebuild` | 是 | 触发一次编译（**不返回编译错误**） |
| `online` | 是 | `action=status` 读连接/登录状态；`connect` 连接设备；`login` 登录应用；`logout` 退出登录 |

## 原理

这套东西本质上解决一个矛盾：**ST 代码在 CODESYS 的 `.project` 里，而 `.project` 是私有序列化格式，外部读不出来。**

`.project` 内部是压缩过的 XML/二进制对象树，没有公开 schema，纯文件层解析等于逆向，工程换个版本就废。
但 InoProShop 自己带了两样东西：命令行参数 `--runscript=`，和进程内的 `scriptengine` 模块（IronPython 2.7）。
所以路线不是"解析文件"，而是**借 IDE 自己的 API 去读写**——IDE 是唯一合法的解释器，我们只负责让它跑一段脚本。

### 一次 `write_object` 的完整链路

```
MCP 客户端（Claude / Cursor / 豆包…）
  │  stdin 一行 JSON: {"method":"tools/call","params":{"name":"write_object", ...}}
  ▼
server.py
  ① tempfile.mkdtemp() 建临时目录 stage（每调用一个，互不干扰）
  ② 参数落盘：meta.txt(名字|类型) / decl.txt / impl.txt    ← ST 代码走文件，不进脚本字符串
  ③ 渲染 runscript.py：WRAP 模板 + 注入 OUTDIR（绝对路径）
  ④ Popen：InoProShop.exe --profile=X --runscript=<stage>/runscript.py
  ▼
InoProShop.exe（管理员权限）
  --runscript 让 IDE 在自己的进程里跑这段 IronPython
  ⑤ import scriptengine as se
     se.projects.open(r"....project", NoUpdates|SilentMode)   ← 静默打开，不弹"是否升级版本"
     app = proj.active_application
  ⑥ 找到/创建对象 → obj.textual_declaration.replace(...) / textual_implementation.replace(...)
     proj.save()
  ⑦ 结果写回 stage；最后写 _done.txt 作为完成标记
  ▼
server.py
  ⑧ 轮询 _done.txt（每 1s，超时 INOPRO_TIMEOUT）
  ⑨ 读 decl.txt / impl.txt / objects.tsv / _error.txt / ide_*.log
  ⑩ 组装成 {"content":[{"type":"text","text":"<JSON>"}]} 回给客户端
```

### 几个设计取舍，以及为什么

**1. 参数走文件，不走脚本字符串。**
ST 代码里全是引号、反斜杠、中文、`%` 格式化符。拼进脚本源码要过 Windows cmd 转义 + Python 字符串转义 + IronPython 2.7 的 unicode 前缀三层，
一旦漏一层就变成"路径中具有非法字符"。改成写 UTF-8 文件让脚本 `io.open` 读，转义问题归零。
代价是每个调用多一个临时目录，但临时目录顺便成了排障现场。

**2. 完成信号用 `_done.txt`，不用 stdout 也不用 returncode。**
IDE 是 GUI 程序，退出码不代表脚本结果；它的 stdout 也不是给我们看的（现在重定向到 `ide_stdout.log`，
不重定向的话 `stdout=PIPE` 不读会把管道缓冲写满，子进程卡死）。所以只在脚本末尾写标记文件：
写了 → 脚本跑完了（成功或失败）；没写 → 卡在弹窗或超时。两种失败因此能分开报。

**3. WRAP 用 try/except 兜住整个脚本体，异常照旧写 `_done.txt`。**
这样"超时"和"业务报错"两种失败不会混在一起：超时说明 IDE 根本没跑起来，`_error.txt` 有内容说明
IDE 跑了但 `se.projects.open` 或 `replace` 抛了异常，原始 traceback 会原样回给调用方。

**4. 为什么 `rebuild` 拿不到编译错误。**
`scriptengine` 没有暴露消息窗口的 API。`textual_declaration.replace()` 即使 ST 语法是错的也不抛异常
（它只做文本替换，不编译），所以编译结果只能事后回 IDE 的 GUI 里看。工具里那句 note 是真诚的，
不是偷懒。

**5. 在线层（`online`）走的是同一套机制，只是对象不同。**
`se.online.create_online_device()` 拿设备对象，`create_online_application(app)` 拿应用对象，
然后 `get_connected()` / `get_is_logged_in()` / `get_application_state()` / `get_operation_state()` 读状态，
`connect()` / `login(change_option, delete_foreign)` / `logout()` 改状态。
这部分方法签名来自 `se.system.dump_scripting_api()` 的 API 转储，**未在真实 PLC 上验证过**，
所以只提供连接侧的四个动作，下载/启停这类会动设备的高风险动作不做。

## 前置要求

- **Windows** + 已安装 InoProShop（默认 `D:\Inovance Control\InoProShop\CODESYS\Common\InoProShop.exe`，
  profile `InoProShop(V1.10.0.3)`）。
- Python 3.9+，纯标准库，无需 pip。
- **MCP 宿主必须以管理员身份启动**（InoProShop.exe 需要提权，宿主不提权则 IDE 类工具会失败）。
- 环境变量可覆盖：
  - `INOPRO_EXE`：InoProShop.exe 绝对路径
  - `INOPRO_PROFILE`：profile 名（以 `Profiles\*.profile` 文件名为准）
  - `INOPRO_TIMEOUT`：单次 IDE 调用超时秒数，默认 180

## 接入

```json
{
  "mcpServers": {
    "inoproshop": {
      "command": "py",
      "args": ["C:/绝对路径/inoproshop-mcp/server.py"],
      "env": {
        "INOPRO_EXE": "D:\\Inovance Control\\InoProShop\\CODESYS\\Common\\InoProShop.exe",
        "INOPRO_PROFILE": "InoProShop(V1.10.0.3)"
      }
    }
  }
}
```

改完配置后**以管理员身份重启客户端**。

## 自检

```
python server.py --selfcheck   # 不启动 IDE，应打印 SELF-CHECK PASSED
python server.py --list-tools  # 打印工具清单
```

## 使用要点（都是踩出来的坑）

1. **命令行参数不内嵌引号**：用 `--profile=Name --runscript=Path`，加引号会被 Windows 二次转义报“非法字符”。
2. **不要隐藏 IDE 窗口**：启动会弹“版本选择”和“插件缺失”两个窗，无窗口模式用户点不到，脚本会一直卡到超时。
3. **一次调用只做一件事**：每个 IDE 工具都会重启 IDE 并弹两次窗。要批量写多个对象，请把多个对象
   合并进一次 IronPython 脚本里执行，不要逐个对象调用（那样每个对象都要等一次 IDE 冷启动）。
4. **declaration 与 implementation 必须拆开**：声明行 + `VAR..END_VAR` 放 `declaration`，`BEGIN` 之后的
   赋值/CASE/IF 主体放 `implementation`。全塞进 declaration 写进去不会生效。
5. **看不到编译错误**：脚本引擎不暴露消息窗口，`rebuild` 只能触发，错误要回 IDE 人工看。
6. **在线层按 API 签名封装，未在真实 PLC 上验证**：`online` 的各 action 请先用 `status` 确认设备与网关配置。
7. **失败先看现场**：每次调用都会留一个临时目录 `%TEMP%\inopromcp_xxxx\`，里面有
   `runscript.py`（实际执行的脚本）、`_error.txt`（traceback）、`_warn.txt`、`ide_stdout.log` / `ide_stderr.log`。
   报错时把 `_error.txt` 贴出来基本就能定位。目录不会自动清理，记得定期清 `%TEMP%`。

## Author & Contact

**MountainClimberJiwen**

- Email: ljwscu@gmail.com
- WeChat: 扫码添加好友

  <img src="assets/wechat-contact-qr.jpg" width="200" alt="WeChat QR Code">

- GitHub: [@MountainClimberJiwen](https://github.com/MountainClimberJiwen)

## Support This Project

如果这个项目对你有帮助，欢迎请我喝杯咖啡 ☕

<img src="assets/payment-qr.jpg" width="200" alt="Support QR Code">

> "每一杯咖啡，都是对一个工程师深夜写代码的温柔慰藉。"
