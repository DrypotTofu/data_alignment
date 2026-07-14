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

如果暂时不想配 NTP，可以让 TwinCAT 在 PLC 里暴露 `MAIN.timestamp_ns`，采集框架直接用它作为样本时间戳。

### PLC 端

```iecst
PROGRAM MAIN
VAR
    timestamp_ns : ULINT;
    fbSystemTime : FB_LocalSystemTime;
END_VAR

fbSystemTime(bEnable := TRUE);
IF fbSystemTime.bValid THEN
    // 把 TwinCAT 系统时间转成 Unix-epoch ns
    timestamp_ns := ...;  // 需按实际时间格式转换
END_IF
```

### Python 端

```yaml
sources:
  - type: twincat
    timestamp_mode: twincat
    symbols:
      timestamp_ns: MAIN.timestamp_ns
```

框架会同时记录：
- `timestamp_ns`：TwinCAT 原始时间戳
- `pc_receive_ns`：PC 收到数据的时间
- `twincat_to_pc_offset_ns`：两者偏移，用于事后对齐

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
