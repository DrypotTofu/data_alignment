# TwinCAT ADS 排障手册

> 用途：VLGP 数据采集电脑的安装、迁移和 ADS 故障排查。
> 更新：2026-11-16。本文保留 2026-08-17 的链路修复经验，并新增 `AdsDll.dll` 预加载问题的完整定位与修复记录。
> 原则：先做只读检查，不随意重启 TwinCAT、不修改路由、不更改 PLC 状态；涉及机器人控制器的操作必须经现场人员确认。

## 1. 当前项目的标准连接参数

以下是 **2026-11-16 实测值**。迁移到其他电脑时，本机地址必须替换，机器人侧参数通常不变。

| 项目 | 当前值 | 说明 |
|---|---|---|
| 机器人名称 | `RobotController` | TwinCAT Target Browser 中可见 |
| 机器人 IP | `192.168.22.22` | 机器人控制器 |
| 机器人 AMS Net ID | `169.254.226.99.1.1` | 实测有效，不要改成 `192.168.22.22.1.1` |
| PLC ADS 端口 | `851` | 这是 **AMS 端口**，不是 TCP 851 监听端口 |
| 采集机 IP | `192.168.22.100` | 新电脑应设置自己的固定地址 |
| 采集机 AMS Net ID | `192.168.22.100.1.1` | 当前注册表实测值 |
| ADS Router TCP/UDP | `48898` | 网络排障应检查此端口 |
| TwinCAT | `3.1.4024.79` | 当前采集机版本 |
| Python | 64 位 Python 3.11 | 当前 conda 环境为 `SurgDataColl` |
| pyads | `3.6.0` | 当前实测版本 |

历史文档中出现过本机 Net ID `192.10.100.251.1.1` 和 TwinCAT `3.1.4024.75`，它们是 **2026-08-17 的旧配置**，迁移时不要直接照搬。

## 2. 正常状态与快速验证

### 2.1 正常状态

- `TwinCAT3 System Service`（服务名 `TcSysSrv`）正在运行。
- 本机 AMS Net ID 已配置。
- 本机与机器人具有正确的双向静态路由。
- `pyads.Connection(...).open()` 成功。
- `read_state()` 可返回控制器状态；`(5, 0)` 表示 Run。
- PLC 符号能够读取，例如 `Sir_RunRunShaw_Database.Joint_Angles` 应返回 48 个 LREAL。

Config 模式不妨碍建立 ADS 连接和读取状态；需要实时 PLC 数据时，再确认 PLC 程序和符号已加载并运行。

### 2.2 使用项目修复后的快速测试

在项目根目录打开 PowerShell：

```powershell
$env:TWINCAT3DIR = 'C:\TwinCAT\3.1\'

& 'D:\ProgramFiles\miniconda3\envs\SurgDataColl\python.exe' -c "from vlgp.acquisition.ads_compat import ensure_ads_core_loaded; ensure_ads_core_loaded(); import pyads; c=pyads.Connection('169.254.226.99.1.1',851,'192.168.22.22'); c.open(); print('OPEN OK',c.get_local_address()); print('STATE',c.read_state()); c.close()"
```

迁移后 Python 路径不同，应替换为本机解释器路径。

完整变量读取测试：

```python
from vlgp.acquisition.ads_compat import ensure_ads_core_loaded
ensure_ads_core_loaded()

import pyads

plc = pyads.Connection('169.254.226.99.1.1', 851, '192.168.22.22')
try:
    plc.open()
    print('本地地址：', plc.get_local_address())
    print('ADS 状态：', plc.read_state())
    values = plc.read_by_name(
        'Sir_RunRunShaw_Database.Joint_Angles',
        pyads.PLCTYPE_ARR_REAL(48),
    )
    print('Joint_Angles 数量：', len(values))
finally:
    plc.close()
```

## 3. 分层排查流程

按以下顺序定位，不要一开始就修改路由或重装 TwinCAT。

### 第一层：确认错误发生在哪一段

| 现象 | 故障层 |
|---|---|
| `Failed to open port on AMS router` | 本机 ADS DLL／本机 Router，尚未访问远端 |
| 能 open，但 `ADSError(7)` | 本机到目标的路由缺失或目标不可达 |
| GUI 可浏览远端，但新 Python 客户端不能 open | GUI 可能是已有连接；继续检查本机新端口注册 |
| open/read_state 正常，但符号读取失败 | PLC 符号、数据类型或 PLC 运行状态 |
| 能连接但响应超时 | 双向路由、远端回包地址或防火墙 |

注意：TwinCAT GUI 能打开 `RobotController`，只能证明该 GUI 连接和远端 ADS 可用，**不能证明本机所有新客户端都能正常注册 AMS 端口**。

### 第二层：只读检查本机服务和端口

```powershell
Get-Service -Name TcSysSrv
Get-NetTCPConnection -LocalPort 48898 -ErrorAction SilentlyContinue
```

正常时服务为 `Running`，48898 的监听进程应为 `TCATSysSrv.exe`。

如果服务未注册或无法运行，再检查 TwinCAT 安装。不要仅凭 `Failed to open port` 就认定服务未运行：2026-11-16 的案例中，服务和端口均正常，但 DLL 依赖加载失败。

### 第三层：检查本机 AMS Net ID

```powershell
$v = (Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Beckhoff\TwinCAT3\System').AmsNetId
$v -join '.'
```

当前应得到：

```text
192.168.22.100.1.1
```

如果更换了 IP、网卡或 AMS Net ID，必须同步检查机器人侧静态路由。

### 第四层：检查网络

```powershell
ping 192.168.22.22
Test-NetConnection 192.168.22.22 -Port 48898
```

不要用 `Test-NetConnection 192.168.22.22 -Port 851` 判断 PLC 是否在线，因为 **851 是 AMS 逻辑端口，不是 TCP 监听端口**。

## 4. 专题：AdsPortOpenEx 返回 Win32 错误 126

### 4.1 故障现象

2026-11-16 出现：

```text
RuntimeError: Failed to open port on AMS router.
```

同时满足：

- `TcSysSrv` 正常运行并监听 48898；
- 本机 AMS Net ID 正确；
- 32 位和 64 位原生 ADS 客户端都返回 `AdsPortOpenEx=0`；
- 32／64 位 `TcAdsDll.dll` 均为 `2.11.0.45`，数字签名有效；
- 重启服务和电脑均不能恢复；
- TwinCAT GUI 可以连接 RobotController。

### 4.2 定位方法

直接调用 64 位 DLL，并在调用前清除旧错误码：

```python
import ctypes

dll = ctypes.WinDLL(
    r'C:\TwinCAT\Common64\TcAdsDll.dll',
    use_last_error=True,
)
dll.AdsPortOpenEx.restype = ctypes.c_long
ctypes.set_last_error(0)
port = dll.AdsPortOpenEx()
print('port =', port)
print('Win32 error =', ctypes.get_last_error())
```

本案例得到：

```text
port = 0
Win32 error = 126
```

`126` 表示“找不到指定模块”。随后通过逐项预加载组件确认：**预先加载 `AdsDll.dll` 后，ADS 立即恢复**。

```python
import ctypes

# 必须在进程中保留该句柄，不能让它被垃圾回收。
_ads_core = ctypes.WinDLL(r'C:\TwinCAT\Common64\AdsDll.dll')

import pyads
plc = pyads.Connection('169.254.226.99.1.1', 851, '192.168.22.22')
plc.open()
print(plc.get_local_address())
print(plc.read_state())
plc.close()
```

### 4.3 项目中的正式修复

已新增：

```text
src/vlgp/acquisition/ads_compat.py
```

统一入口：

```python
from vlgp.acquisition.ads_compat import ensure_ads_core_loaded
ensure_ads_core_loaded()
```

该函数根据 `pyads.pyads_ex.dll_path` 找到同目录的 `AdsDll.dll`，每个进程只加载一次。已接入：

- `src/vlgp/acquisition/sources/twincat.py`
- `src/vlgp/web/camera_service.py` 的机器人链路自检

优点：

- 不修改系统 PATH 或 DLL 搜索路径；
- 不复制、覆盖系统 DLL；
- 不修改 TwinCAT 服务和 ADS 路由；
- 后续迁移时随项目代码一起迁移。

### 4.4 本案例的验证结果

- 项目 ADS open 成功；
- 本地地址为 `192.168.22.100.1.1:<动态 AMS 端口>`；
- `read_state() = (5, 0)`；
- 成功读取 `Joint_Angles` 的 48 个数值；
- Web 机器人链路自检通过，读取约 9 ms。

### 4.5 明确排除的方向

本案例不需要：

- 关闭 Hyper-V；已有历史测试证明 Hyper-V 开关不影响此 ADS 连接；
- 重装或降级 `pyads`；
- 更换 32／64 位 Python；
- 修改机器人 AMS Net ID；
- 重建远端路由；
- 仅因 Real-time 日志中的 Hyper-V 报错而修复 ADS。

## 5. 迁移到新电脑的步骤

### 5.1 安装和基础配置

1. 安装与现有系统兼容的 TwinCAT 3.1。
2. 安装 64 位 Python 和项目依赖。
3. 设置采集机固定 IP，避免 DHCP 改变地址。
4. 确认 `TcSysSrv` 已注册并能正常运行。
5. 确认本机 AMS Net ID，记录下来。
6. 保留项目中的 `ads_compat.py` 修复。

### 5.2 配置双向静态路由

本机文件：

```text
C:\TwinCAT\3.1\Target\StaticRoutes.xml
```

本机到机器人条目模板：

```xml
<Route>
    <Name>RobotController</Name>
    <Address>192.168.22.22</Address>
    <NetId>169.254.226.99.1.1</NetId>
    <Type>TCP_IP</Type>
    <Flags>0</Flags>
</Route>
```

机器人侧必须有指向采集机的条目：

```xml
<Route>
    <Name>CollectionPC</Name>
    <Address>新采集机IP</Address>
    <NetId>新采集机AMS Net ID</NetId>
    <Type>TCP_IP</Type>
    <Flags>0</Flags>
</Route>
```

注意：

- 两侧路由缺一不可；
- `NetId + IP` 必须一致；
- Windows 上不要依赖 `pyads.add_route()`，历史实测会提示 Router interface 不可用；
- 修改静态路由后是否重启 TwinCAT，应按现场维护流程执行；
- 机器人侧操作必须经机器人工程师确认，并确保机械臂安全。

### 5.3 防火墙

确保 TwinCAT Router 的 ADS 通信未被拦截，重点为 TCP/UDP 48898。不要为了排障长期关闭防火墙；应添加精确规则或由现场 IT 处理。

## 6. 常见错误速查

| 报错／现象 | 优先检查 |
|---|---|
| `Failed to open port on AMS router` | 先查本机服务、48898 监听；若正常，再测 Win32 错误 126 和 `AdsDll.dll` 预加载 |
| `AdsPortOpenEx=0`，`GetLastError=126` | ADS 组件加载问题；测试预加载 `Common64\AdsDll.dll` |
| `ADSError(7)` target machine not found | 本机到机器人的静态路由 |
| `ADSError(18)` port disabled | 对端是否认可本机 Net ID＋IP，路由是否冲突 |
| `ADSError(1861)` / `0x745` | 目标 Net ID 错误或对端拒绝 |
| TCP 连接被 RST | 对端路由表可能没有本机 Net ID |
| 连接接受但响应超时 | 对端回包路由中的本机 IP 可能过期 |
| open/read_state 正常，符号失败 | 符号名、数组长度、PLC 数据类型和运行状态 |
| GUI 能连但 Python 不能连 | 检查新客户端端口注册；不要直接修改远端路由 |

## 7. 验收清单

迁移或修复完成后，按顺序验收：

- [ ] `TcSysSrv` 正常运行；
- [ ] 本机 AMS Net ID 已记录；
- [ ] 双向静态路由与当前 IP 一致；
- [ ] 项目 ADS 兼容修复已加载；
- [ ] `pyads.open()` 成功；
- [ ] `read_state()` 正常；
- [ ] 8 类机器人采集符号均可读；
- [ ] `Joint_Angles` 返回 48 个数值；
- [ ] `CycleCount`／`CycleTime` 可读，时间轴正常；
- [ ] Web 机器人链路自检通过；
- [ ] 短时 TwinCAT＋相机联合采集无 ADS 错误；
- [ ] 长时采集无掉线、零样本或时间戳异常。

## 8. 历史案例摘要

### 2026-08-17：重装 Windows 后的链路断裂

当时故障包括：`TcSysSrv` 未注册、`StaticRoutes.xml` 丢失、双向路由失效。修复 TwinCAT 并重建双向路由后，ADS 恢复，8 类符号全部读通，全符号轮询约 290.7 Hz、0 错误。

该案例说明：IP 或 Net ID 变化后，必须同步机器人侧路由。

### 2026-11-16：服务正常但新客户端无法打开端口

本次服务、端口、AMS Net ID、DLL 版本和远端 GUI 连接均正常，但所有新原生客户端均无法注册。最终通过 Win32 错误 126 和逐项预加载定位到 `AdsDll.dll`，并在项目中加入进程级兼容修复。无需修改 Hyper-V、PLC 或远端路由。
