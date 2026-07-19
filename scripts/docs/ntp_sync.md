# NTP 时间同步方案：PC ↔ TwinCAT

## 目标

让采集 PC 的 `time.time_ns()` 与 TwinCAT 的实时时钟对齐，误差控制在 1~10 ms（NTP 典型值）或更低（配合 PTP）。

这样所有数据（USB 相机、内窥镜、TwinCAT 位姿）都可以用 PC 时间戳直接比较，无需事后换算。

---

## 方案选择

| 方案 | 精度 | 复杂度 | 推荐度 |
|------|------|--------|--------|
| **共同 NTP 服务器** | 1~10 ms | 低 | ⭐ 推荐 |
| **TwinCAT 作 NTP 服务器，PC 作客户端** | 1~10 ms | 中 | 可行 |
| **PTP (IEEE 1588)** | <1 ms | 高 | 高精度需求 |
| **不同步，只用 TwinCAT 时间戳** | 无网络延迟 | 低 | 需事后对齐 |

---

## 方案 A：共同 NTP 服务器（最常用）

让 TwinCAT 主机和采集 PC 都同步到同一个 NTP 服务器，例如：

- 医院/实验室的内部 NTP 服务器
- 一台带 GPS 授时的 Windows/Linux 主机
- 路由器（通常支持 NTP）

### 1. 配置 TwinCAT 主机（Windows）

以管理员身份打开 PowerShell：

```powershell
# 设置 NTP 服务器
w32tm /config /manualpeerlist:"ntp.server.ip" /syncfromflags:manual /reliable:yes /update

# 重启 Windows 时间服务
net stop w32time && net start w32time

# 强制同步
w32tm /resync

# 查看同步状态
w32tm /query /status
```

### 2. 配置采集 PC（Windows）

```powershell
w32tm /config /manualpeerlist:"ntp.server.ip" /syncfromflags:manual /update
net stop w32time && net start w32time
w32tm /resync
w32tm /query /status
```

### 3. 验证

两边都显示 `Source: ntp.server.ip` 且 `Last Sync Error` 较小时，说明同步成功。

---

## 方案 B：TwinCAT 主机作 NTP 服务器

如果 TwinCAT IPC 本身已经接入了稳定时钟源（如 GPS），可以让它当 NTP 服务器。

### 1. 在 TwinCAT 主机上启用 NTP 服务

以管理员身份打开注册表编辑器，修改：

```
HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpServer
Enabled = 1
```

然后重启时间服务：

```powershell
net stop w32time && net start w32time
```

### 2. 在采集 PC 上指向 TwinCAT 主机

```powershell
w32tm /config /manualpeerlist:"192.168.1.100" /syncfromflags:manual /update
net stop w32time && net start w32time
w32tm /resync
```

> 注意：TwinCAT 实时任务 Windows NTP 服务可能共享 CPU，精度通常不如独立 NTP 服务器。

---

## 方案 C：只用 TwinCAT 时间戳（不依赖 NTP）

如果暂时不想配 NTP，可以直接使用 TwinCAT 任务周期计数器作为稳定的 PLC 端时间轴。该机器人 PLC 的标准系统信息变量 `_TaskInfo[1].CycleCount` 每个 PLC 任务周期（1 ms）递增一次，配合 `_TaskInfo[1].CycleTime` 即可换算成时间。

### 配置方式

```yaml
sources:
  - type: twincat
    name: robot_poses
    timestamp_mode: twincat
    # 以下三项通常使用默认值即可：
    # twincat_timestamp_symbol: TwinCAT_SystemInfoVarList._TaskInfo[1].CycleCount
    # twincat_timestamp_cycle_time_symbol: TwinCAT_SystemInfoVarList._TaskInfo[1].CycleTime
    # twincat_timestamp_cycles_per_ms: 1.0   # 仅在不想读取 CycleTime 时覆盖
```

### 输出字段

每个样本的 `timestamp_ns` 为：

```
timestamp_ns = CycleCount * CycleTime_ns
```

同时 metadata 中会保留：
- `twincat_cycle_count`：原始 PLC 周期计数
- `twincat_cycle_time_ns`：PLC 任务周期（实测为 1 000 000 ns，即 1 ms）
- `pc_arrival_ns`：PC 收到 ADS 响应的时间
- `pc_cycle_start_ns`：本周期开始时 PC 时间

这样即使 PC 与 TwinCAT 时钟没有对齐，位姿数据自身也有一条稳定、单调递增的 PLC 时间轴，适合与 30 FPS 视频帧做事后对齐。

---

## 同步精度检查

配置好 NTP 后，运行：

```powershell
python test\check_clock_sync.py --ams-net-id 192.168.1.100.1.1 --symbol MAIN.timestamp_ns --samples 100
```

输出示例：

```text
Mean offset: +0.847 ms (PC - TwinCAT)
Std offset : 0.312 ms
Mean RTT   : 1.234 ms
Clocks are well synchronized (< 1 ms).
```

- `offset` 接近 0 且稳定 → NTP 同步良好
- `offset` 很大（>10 ms）→ 检查 NTP 配置
- `RTT` 很大 → 检查网络/ADS 路由

---

## 配置采集框架

NTP 同步完成后，采集配置保持默认即可：

```yaml
clock_source: host_realtime
clock_offset_ns: 0

sources:
  - type: usb_camera
    name: camera_wrist_left
    # ...

  - type: twincat
    name: robot_poses
    timestamp_mode: pc_arrival  # NTP 已同步，PC 到达时间 ≈ TwinCAT 时间
    # ...
```

如果 TwinCAT 本身也提供了 `timestamp_ns`，可以改成：

```yaml
  - type: twincat
    timestamp_mode: twincat
```

这样即使 NTP 稍有漂移，TwinCAT 时间戳也是最准的。

### 让视频时间戳也使用 PLC 时间轴

如果希望相机/内窥镜帧的时间戳与 TwinCAT 位姿直接共享同一轴，可把 PLC 设为主时钟：

```yaml
clock_source: twincat_master
clock_offset_ns: 0

sources:
  - type: twincat
    name: robot_poses
    timestamp_mode: twincat
    twincat_master_sync_alpha: 0.05
    # ...

  - type: usb_camera
    name: camera_wrist_left
    # ...
```

工作原理：
1. TwinCAT 源每个样本计算 `instant_offset = twincat_timestamp_ns - pc_arrival_ns`。
2. 通过 EMA（默认 `alpha=0.05`）更新共享的 `clock_offset_ns`。
3. 所有其他源调用 `clock.now_ns()` 时返回 `time.time_ns() + offset_ns`，即落在 PLC 时间轴上。

结果：
- 视频 `timestamp_ns` 与位姿 `timestamp_ns` 都是 PLC 相对时间（从 PLC 启动起的纳秒数）。
- 每个样本的 metadata 仍保留原始 `pc_arrival_ns`，便于事后核对网络延迟。

> 注意：该偏移是 TwinCAT 启动后的相对时间，不是 Unix epoch 绝对时间。如果需要绝对时间，可再记录第一个样本的 PC 时间作为 epoch 偏移。

### 同时保留 PC 和 PLC 两种时间戳

如果希望每个 TwinCAT 样本的 metadata 中同时有 PC 时间戳和 PLC 时间戳，开启双时间戳输出：

```yaml
  - type: twincat
    name: robot_poses
    timestamp_mode: twincat
    output_dual_timestamps: true
```

输出字段：
- `timestamp_ns`：由 `timestamp_mode` 决定的主时间戳
- `timestamp_ns_pc`：PC wall time（`pc_cycle` 模式下为 cycle 开始时刻，其他为响应到达时刻）
- `timestamp_ns_plc`：PLC 轴时间戳（`CycleCount * CycleTime_ns`）
- `pc_arrival_ns`：原始 PC 响应到达时间

这样即使主时间戳选了 PLC 轴，事后也仍然可以直接拿到对应的 PC 时间，便于与未同步的外部数据对比。

### 提高位姿采样率：多 ADS 连接

单个 ADS 连接在本项目机器人上大约能到 260 Hz。如果需要更高的位姿密度（例如事后做运动学插值），可以开启多个并行 ADS 连接：

```yaml
  - type: twincat
    name: robot_poses
    timestamp_mode: twincat
    num_connections: 2   # 或 4
```

实测结果（3 秒 benchmark）：

| 连接数 | 总样本率 | 唯一 CycleCount | 重复数 |
|--------|---------|----------------|--------|
| 1      | ~260 Hz | ~778            | 0      |
| 2      | ~620 Hz | ~1115           | ~748   |
| 4      | ~1.3 kHz| ~1217           | ~2725  |

说明：
- PLC 每 1 ms 才更新一次 CycleCount，所以多连接的主要作用是**提高采样密度**，而不是获取更多唯一周期。
- 重复样本会进入同一个队列/CSV，事后可以通过 `twincat_cycle_count` 去重或插值。
- 对于 30 FPS 视频（33 ms/帧），**1 个连接已经足够**；只有需要高密度位姿时才提高连接数。
- 每个连接有独立的 ADS 会话，会增加 PLC 和网络的负载，不建议超过 4 个。

### 进一步降低在线 offset 抖动：RTT 校正

ADS 往返延迟每次不同，会让 `instant_offset` 估计带有短期噪声。开启 RTT 校正后，源会用 `t_recv - RTT/2` 作为 PC 到达时间：

```yaml
  - type: twincat
    name: robot_poses
    timestamp_mode: twincat
    twincat_master_sync_alpha: 0.05
    use_rtt_correction: true
```

metadata 会额外记录 `rtt_ns` 和 `pc_arrival_corrected_ns`，便于事后分析。

### 消除长期晶振漂移：事后校准

PC 与 PLC 晶振频率不完全相同，长时间运行后会有累积漂移。采集结束后，可用校准脚本把视频帧重新映射到精确的 PLC 轴：

```powershell
python scripts/tools/calibrate_temporal_drift.py `
    --twincat sessions/my_session/robot_poses/twincat_poses.csv `
    --video-metadata sessions/my_session/camera_wrist_left/metadata.csv `
    --output sessions/my_session/camera_wrist_left/metadata_calibrated.csv
```

脚本会：
1. 根据 TwinCAT CSV 拟合 `twincat_timestamp_ns = k * pc_arrival_ns + b`。
2. 用该模型重新计算每帧视频时间戳。
3. 输出新的 `metadata_calibrated.csv`，原 `timestamp_ns` 保留为 `timestamp_ns_original`。

建议：
- **短时实验（<30 min）**：`twincat_master` 已足够。
- **长时实验**：`twincat_master` + `use_rtt_correction` + 事后 `calibrate_temporal_drift.py`。
- **需要绝对时间**：再叠加 NTP。
