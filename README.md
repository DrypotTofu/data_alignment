# VLGP 多模态手术数据采集与对齐

面向机器人辅助手术的 Vision-Language-Guided Procedure (VLGP) 研究。本平台同步采集三路 USB 相机视频与 TwinCAT ADS 机器人运动学数据（四臂全量符号），并提供跨模态时间对齐与数据后处理工具，目标是为 SRT-H 风格的分层手术机器人模型构建训练数据集。

## 环境安装

```bash
conda env create -f environment.yml
conda activate SurgDataColl
# 或者
pip install -r scripts/requirements.txt
# T13：可安装包（任意目录可 import vlgp.*）
pip install -e . --no-deps
```

## 快速开始

```bash
# 1. 分配摄像头到各条臂（含真实 FPS benchmark，会写回 scripts/config/default.yaml）
#    当前硬件绑定集中写在 default.yaml 顶层 camera_bindings：arm_1=1, arm_2=0, arm_3=3
scripts/tools/camera_assignment_setup.bat          # 双击（Windows）
python -m vlgp.capture.camera_assignment_gui --config scripts/config/default.yaml

# 2. 手动开始/停止采集（自动生成 test_YYYYMMDD_HHMMSS session）
scripts/tools/start_manual_acquisition.bat         # 双击
python scripts/run_acquisition.py --config scripts/config/default.yaml --duration 300

# 3. 时间对齐（SRT-H 风格：相机锚点 30 Hz + 位姿插值）
python -m vlgp.alignment.align_camera_anchor --session sessions/<session_name>
```

## 采集后的数据处理流程

**一键方式（推荐）**：双击 `scripts/tools/process_session.bat`（会询问 session 名和是否补录元数据），或命令行：

```bash
# 一条命令跑完整条流水线：预检 -> 对齐 -> 人工裁剪(GUI) -> 构建 episode -> 目检视频
# 任一步骤失败即中止；review 中标 rejected 的 session 自动跳过后续构建
python -m vlgp.review.process_session --session sessions/<session_name> [--meta] [--skip-review] [--skip-video]
```

**Windows 启动器（scripts/tools/*.bat，逐步方式）**：每个处理步骤都有对应 .bat（双击即可，会自动询问 session 名并写日志到 scripts/tools/）：

| .bat | 对应命令 | 用途 |
|---|---|---|
| `start_manual_acquisition.bat` | `vlgp.capture.manual_acquisition` | 手动开始/停止采集 |
| `camera_assignment_setup.bat` | `vlgp.capture.camera_assignment_gui` | 摄像头分配设置（GUI） |
| `preview_single_camera.bat` | `vlgp.capture.preview_single_camera` | 单相机预览/对焦 |
| `preview_connected_cameras.bat` | `vlgp.capture.preview_connected_cameras` | 多相机批量预览 |
| `write_session_meta.bat` | `vlgp.review.write_session_meta` | 当场补录任务元数据（不可回溯，先做） |
| `verify_alignment.bat` | `vlgp.alignment.verify_alignment` | 对齐预检（漂移/同步质量） |
| `align_camera_anchor.bat` | `vlgp.alignment.align_camera_anchor` | 生成 30 Hz 相机锚点对齐表 |
| `start_web_platform.bat` | `vlgp.web.app` | 启动 Web 评审平台（裁剪/标注/状态） |
| `build_srth_dataset.bat` | `vlgp.dataset.build_srth_dataset` | 构建 SRT-H 训练 episode |
| `images_to_video.bat` | `vlgp.dataset.images_to_video` | 目检视频生成 |
| `analyze_session.bat` | `vlgp.analysis.analyze_session` | Session 综合分析（fps/RTT/漂移） |
| `analyze_gripper.bat` | `vlgp.analysis.analyze_gripper` | gripper 量程/方向分析 |
| `collect_calibration.bat` | `vlgp.calibration.collect_calibration` | 标定板内参采集 |
| `camera_param_tuner.bat` | `vlgp.calibration.camera_param_tuner` | 相机曝光/白平衡实时调参 |
| `process_session.bat` | `vlgp.review.process_session` | 一键完整流水线 |
| `start_web_platform.bat` | `vlgp.web.app` | Web 标注平台（FastAPI + JS 前端，127.0.0.1:8765） |
| `validate_kinematics.bat` | `vlgp.kinematics.validate` | MDH FK 与 PLC 对标验证（T16） |
| `validate_task_hierarchy.bat` | `vlgp.annotations.task_hierarchy` | 任务层级词表结构/片段校验（T15） |

注：`export_lerobot`（LeRobotDataset v3.0 导出，T17）暂无 .bat，用 `python -m vlgp.dataset.export_lerobot --episode <srth_episode_dir> --out <dataset_dir>` 运行。

**逐步方式（命令行）**（需要单独控制某一步时使用；详细规范见 `tech_repo/VLGP_标定与元数据规范_20260817.md` 第 6 节）：

```bash
# 1. 当场补录元数据（任务指令/任务类型/操作者/标定 ID，不可回溯，先做）
python -m vlgp.review.write_session_meta --session sessions/<session_name>

# 2. 对齐预检（漂移与同步质量检查；异常就停下来排查）
python -m vlgp.alignment.verify_alignment --session sessions/<session_name>

# 3. 生成 30 Hz 相机锚点对齐表（关注对齐误差 p99 < 15 ms、覆盖率）
python -m vlgp.alignment.align_camera_anchor --session sessions/<session_name>

 # 4. 人工裁剪与审核（Web 平台：运动量曲线定位首尾静止段，打 IN/OUT，
 #    标 approved/rejected/pending，保存；决策写入 annotations.json，
 #    原始数据不动。无对齐表的废品 session 会进入降级视图，只能标 rejected/pending）
 python -m vlgp.web.app --host 127.0.0.1 --port 8765
```

然后在浏览器打开 `http://127.0.0.1:8765`，选择 session 进行 IN/OUT 裁剪、approved/rejected/pending 标记与片段标注，保存后 `annotations.json` 即写入 session 目录。

会话数据不限于仓库 `sessions/`：侧栏**「数据目录」**按钮可把平台指向任意文件夹（其他磁盘/外置盘/NAS），选择会持久化到 `var/web_settings.json`；预览缓存与流水线日志跟随所选目录（`<数据根>/_web_preview`、`<数据根>/_web_logs`），同一弹窗里可查看/修改**采集输出目录**（两份采集 yaml 的 `base_dir`）并清理已删除会话的失效缓存。有后台任务（预加载/流水线）运行时不允许切换目录。

侧栏**「采集」**按钮把相机绑定与数据采集也搬进了 Web（与 `start_manual_acquisition.bat` 同一入口）：逐 index **抓帧预览**辨认相机 → 下拉分配 `arm_n` → 保存绑定（写回 `scripts/config/*.yaml`，保留注释）；录制前**自检**（相机能否取帧 / TwinCAT ADS 链路 / 磁盘余量按 20 分钟 ≈64 GB 预留 / 会话目录是否重名）；录制中实时显示时长、已写 GB、MB/s 与日志，**停止为优雅停止**（刷盘 + 写 `session.json`），结束后自动刷新会话列表并提示生成对齐表。录制期间相机预览、修改绑定、切换目录都会被拒绝（设备被采集进程独占）。命令行等价用法：`python -m vlgp.capture.manual_acquisition --config scripts/config/default.yaml --name <name> --stop-file <flag>`（`--stop-file` = 自动开始、出现该文件即优雅停止）。

python -m vlgp.review.process_session --session sessions/<session_name> --skip-review

 # 5. 一键管线（或在 Web 评审后运行；会校验 annotations.json 状态，
 #    rejected 的 session 会停止）
 python -m vlgp.review.process_session --session sessions/<session_name> --skip-review

 # 6. 构建 SRT-H 训练 episode（自动应用 trim、带入 session 元数据；
 #    核对样本数与动作数值范围）
python -m vlgp.dataset.build_srth_dataset --session sessions/<session_name>

 # 7. 目检视频（逐路检查画面连续性、色彩、时间戳叠加）
python -m vlgp.dataset.images_to_video --dir sessions/<session_name>/camera_arm_2 \
    --output sessions/<session_name>/arm2_preview.mp4 --timestamp-overlay
```

## 目录结构

```text
├── pyproject.toml                # 可安装包（pip install -e . 后 vlgp 可导入）
├── src/vlgp/                     # 功能分包（T13）
│   ├── acquisition/              # 采集框架（Session + DataSource + registry/config/layout）
│   │   ├── sources/              # twincat / usb_camera_process / reserved_arm (+ legacy/)
│   │   └── process_workers/      # 相机子进程 worker
│   ├── alignment/                # align_camera_anchor / verify_alignment（calibrate_temporal_drift 已归档）
│   ├── dataset/                  # build_srth_dataset / images_to_video / export_lerobot（pack_session 已归档）
│   ├── review/                   # process_session / write_session_meta / common
│   ├── calibration/              # collect_calibration / camera_param_tuner
│   ├── analysis/                 # analyze_session / analyze_gripper
│   ├── capture/                  # manual_acquisition / camera_assignment_* / preview_*
│   ├── kinematics/               # MDH 正运动学与基座系→镜系变换（validate.py，T16）
│   ├── annotations/              # T15 任务层级词表框架（task_hierarchy.py）
│   ├── web/                      # Web 标注平台（FastAPI 后端 + static/ 前端，T15/T17）
│   └── constants.py              # 共享魔法数字（30 Hz / 15 ms / 60 步等）
├── scripts/
│   ├── run_acquisition.py        # 采集入口
│   ├── config/                   # default / single_arm / example; historical configs are in archive/
│   ├── tools/*.bat               # Windows 启动器（%~dp0 相对路径推导）
│   └── test/                     # TwinCAT benchmark 与诊断脚本
├── sessions/                     # 采集数据（git 忽略）
├── calibrations/                 # 标定结果（git 忽略）
├── var/camera_test_frames/       # 相机探测测试帧（git 忽略）
└── tech_repo/                    # 技术文档
    ├── VLGP_项目状态与技术备忘_20260803.md   # 详细技术背景主文档
    ├── VLGP_项目状态与技术备忘_20260816.md   # 当前快照
    └── archive/                  # 已被覆盖的旧文档（0713/0717/0719）
```

## 更多文档

- **项目状态备忘（当前快照，2026-08-16）**：`tech_repo/VLGP_项目状态与技术备忘_20260816.md`
- **项目状态与技术备忘**（详细技术背景主文档）：`tech_repo/VLGP_项目状态与技术备忘_20260803.md`
- 执行 Agent 任务交接与验收标准：`AGENT_HANDOFF.md`
- 技术文档 / 进展报告（已归档）：`tech_repo/archive/VLGP_项目技术文档_20260719.md`、`tech_repo/archive/VLGP_项目进展报告_20260719.md`
- 会话恢复检查点：`SESSION_CHECKPOINT.md`
- Agent 开发约定：`scripts/AGENTS.md`
