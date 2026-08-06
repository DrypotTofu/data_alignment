# Session Checkpoint

> 更新时间：2026-08-05（北京时间）
> 目的：保存当前会话上下文，便于下次 `kimi resume` 后继续工作。

## 当前状态

仓库已完成结构整理并推送到 GitHub：`git@github.com:DrypotTofu/data_alignment.git`。

**最权威的项目状态文档是 `tech_repo/VLGP_项目状态与技术备忘_20260803.md`**（持续更新，含 PTP 调研、对齐策略、DAgger 规划）。本文件只保留恢复会话所需的最小上下文。

## 最近完成的工作

1. **四臂 TwinCAT 全数据采集**：`output_all_arms=true`，读取 PLC 全部 8 类符号（关节角、双坐标系位姿、四元数、器械类型、夹钳角度/信号、能量器械状态），实测 ~320–340 Hz/臂。
2. **相机锚点对齐工具**：`scripts/tools/align_camera_anchor.py`（策略二：30 Hz 相机锚点 + LERP/SLERP 位姿插值），已用 `sessions/test_20260803_104724` 验证。
3. **cam1 新摄像头结论**：MSMF + 640×480 + MJPG + fps=30 可稳定 30 fps；DSHOW 设置 FPS 会降到 ~20 fps；`usb_camera_worker.py` 已修复 `fps: null` 不再强制设置 `CAP_PROP_FPS`。
4. **PTP 调研**：已写入 tech_repo 0803 备忘第 16 节（EL6688 + 海康 GigE PTP 可行性与成本）。
5. **仓库结构整理**：旧 config/test 文件归档到各自 `archive/`，弃用采集源移入 `acquisition/sources/legacy/`，依赖文件已统一。

## 待办（详见 tech_repo 0803 备忘第 9 节）

- 10–30 分钟长时间稳定性测试。
- 与机器人工程师确认 EL6688 PTP 接入可行性。
- SRT-H 数据构建：动作空间转换（hybrid-relative delta + 6D 旋转 + jaw）与 HDF5 导出。
- 语言标注工具（任务级 + 纠正级）。

## 如何恢复会话

```bash
kimi resume
```

进入工作目录后，先读 `tech_repo/VLGP_项目状态与技术备忘_20260803.md` 和根目录 `README.md`。
