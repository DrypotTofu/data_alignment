# VLGP TwinCAT ADS 链路修复说明（2026-08-17）

**日期**：2026-08-17
**文档性质**：专题说明。记录「采集机重装 Windows 后 TwinCAT/ADS 链路断裂」的故障现象、诊断方法、最终修复配置与验证结果。主状态文档仍为 `VLGP_项目状态与技术备忘_20260816.md`。

---

## 1. 故障背景

数据采集用 PC（本机，当前 IP `192.168.22.100`）重装 Windows 系统后，TwinCAT 安装损坏，无法读取机器人 PC（`192.168.22.22`，PLC 端口 851）中的采集变量。具体表现为：

1. 本机 `TcSysSrv`（TwinCAT3 System Service，AMS 路由器）服务**未注册**（`sc.exe query TcSysSrv` 返回 1060），`C:\TwinCAT` 只剩部分文件；
2. 本机 `C:\TwinCAT\3.1\Target\StaticRoutes.xml` 被清空（指向机器人的静态路由丢失）；
3. pyads `plc.open()` 报 `RuntimeError: Failed to open port on AMS router`。

## 2. 诊断过程与关键结论

### 2.1 网络层

- ping `192.168.22.22` 正常（~3 ms）；TCP 48898（ADS）端口开放。
- 网络层无问题，故障在 AMS 路由层。

### 2.2 本机 AMS 路由器

- pyads 在 Windows 上**必须**通过本机 AMS 路由器（TcSysSrv）转发报文；路由器不工作则 pyads 必然失败。修复 TwinCAT 安装使 `TcSysSrv` 恢复运行后，报错变为 `ADSError(7)`（target machine not found，本地缺路由）。

### 2.3 双向路由缺一不可

ADS over TCP 要求**两侧路由表互相认识**：

- 本机侧：`StaticRoutes.xml` 需要「机器人 NetID → 192.168.22.22」条目；pyads 的 `add_route()` 在 Windows 上不可用（报 "Router interface is not available on Win32 systems"），只能编辑 `StaticRoutes.xml` 后**重启 TcSysSrv 生效**。
- 机器人侧：机器人路由表按「NetID + IP」校验来源；本机重装后 NetID 未变（注册表保留：`HKLM\SOFTWARE\WOW6432Node\Beckhoff\TwinCAT3\System` 的 `AmsNetId` = **192.10.100.251.1.1**，注意与本机当前 IP 192.168.22.100 不对应），但机器人路由表中该 NetID 登记的 IP 是旧地址，导致：
  - 用错误源 NetID 连接 → 机器人**立即 RST**（路由表无此 NetID）；
  - 用正确源 NetID 连接 → 连接被接受但**响应超时**（回包按路由表发往旧 IP）。

### 2.4 机器人 NetID 确认

- 修复双向路由后实测：`169.254.226.99.1.1` 可连接（PLC Run 模式），`192.168.22.22.1.1` 被拒（`ADSError(1861)`）。
- **机器人真实 NetID 仍是 `169.254.226.99.1.1`**，与 `scripts/config/default.yaml` 中配置一致，采集配置无需修改。

### 2.5 原生 ADS/TCP 探测方法（备用手段）

当本机路由器不可用或需要区分「本机故障 / 路由故障 / PLC 故障」时，可用裸 socket 直连 `48898` 手工构造 AMS/TCP 报文（2 字节保留 + 4 字节长度 + 32 字节 AMS 头 + 数据）：

- ReadState（cmd=4）探测目标端口是否注册；
- ReadWrite（cmd=9, IndexGroup `0xF003`）按名取符号句柄；Read（cmd=2, IndexGroup `0xF005`）读值；cmd=6（`0xF006`）释放句柄。

机器人路由器对不可达目标的错误响应特征：header error=0x7、payload 为空、source 字段镜像为请求的目标地址。

## 3. 最终修复配置（已生效，勿随意改动）

### 3.1 本机（采集 PC，192.168.22.100）

- TwinCAT 3.1.4024.75，`TcSysSrv` 服务运行中（Automatic）。
- 本机 AMS NetID：`192.10.100.251.1.1`（重装系统后注册表保留，未变）。
- `C:\TwinCAT\3.1\Target\StaticRoutes.xml`：

```xml
<RemoteConnections>
	<Route>
		<Name>RobotController</Name>
		<Address>192.168.22.22</Address>
		<NetId>169.254.226.99.1.1</NetId>
		<Type>TCP_IP</Type>
		<Flags>0</Flags>
	</Route>
	<Route>
		<Name>RobotController_Alt</Name>   <!-- 备用候选，实测无效，可删除 -->
		<Address>192.168.22.22</Address>
		<NetId>192.168.22.22.1.1</NetId>
		<Type>TCP_IP</Type>
		<Flags>0</Flags>
	</Route>
</RemoteConnections>
```

### 3.2 机器人侧（192.168.22.22）

`C:\TwinCAT\3.1\Target\StaticRoutes.xml` 新增（机器人工程师操作，改后需重启机器人侧 TcSysSrv / TwinCAT）：

```xml
<Route>
	<Name>CollectionPC</Name>
	<Address>192.168.22.100</Address>
	<NetId>192.10.100.251.1.1</NetId>
	<Type>TCP_IP</Type>
	<Flags>0</Flags>
</Route>
```

**注意**：若采集机 IP 或 NetID 以后再次变化（重装、换网卡、DHCP 变动），必须同步更新机器人侧这一条目，否则会出现「连接被接受但读不到数据」的症状。

## 4. 验证结果（2026-08-17 实测）

- `pyads.Connection('169.254.226.99.1.1', 851, '192.168.22.22')` open 成功，`read_state() = (5, 0)`（**PLC Run 模式**，原 T0 阻塞的 RTIME 4132 问题随系统重装一并解决）。
- 8 类采集符号（`Sir_RunRunShaw_Database.*`）全部读通，数值量级合理（关节角度数、位姿 mm、四元数模长≈1、`instrument_types=[4,13,2,7]`、gripper 近 0、能量全 False）。
- `TwinCAT_SystemInfoVarList._TaskInfo[1].CycleCount/CycleTime` 可读，CycleTime=10000（×100 ns = 1 ms，1 kHz 任务），`timestamp_mode: twincat` 依赖的时间轴符号正常。
- 10 秒全符号连续轮询：**290.7 Hz，0 错误**，轮询间隔均值 3.44 ms / 最大 5.91 ms，每次读取值均变化（实时数据）。与文档记载的 320–340 Hz 同一量级，满足 `default.yaml` 采集需求。

## 5. 常见错误码速查（本次实测遇到）

| 报错 | 含义 | 排查方向 |
|---|---|---|
| `Failed to open port on AMS router` | 本机 AMS 路由器不工作 | TcSysSrv 服务是否注册/运行 |
| `ADSError(7)` target machine not found | 本机缺到目标的路由 | 本机 StaticRoutes.xml + 重启 TcSysSrv |
| `ADSError(18)` port disabled | 本机路由器到对端连接失败/被禁用 | 对端路由表是否认可本机 NetID+IP；必要时清理重复路由并重启本机 TcSysSrv |
| `ADSError(1861)` (0x745) | 对端路由器拒绝：NetID 不存在 | 目标 NetID 写错；用正确 NetID |
| 原生 TCP 连接被 RST | 对端路由表无此源 NetID | 机器人侧加/改路由 |
| 原生 TCP 超时无响应 | 源 NetID 被认可但回包发往旧 IP | 机器人侧路由条目 IP 是否为本机当前 IP |

## 6. 后续事项

1. T0 硬件阻塞已解除：按 `AGENT_HANDOFF.md` 先做 **45 秒预检**（短时采集 + `verify_alignment.py`），通过后进入 T3 长稳测试。
2. 可清理本机 `StaticRoutes.xml` 中无效的备用路由 `RobotController_Alt`。
3. 相机侧遗留问题不变：index 0 房景相机 ~29.7 fps 不达标，正式三路验证前需替换（见 0816 备忘第 4 节）。
