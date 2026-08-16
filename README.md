# VLGP 多模态手术数据采集与对齐

面向机器人辅助手术的 Vision-Language-Guided Procedure (VLGP) 研究。本平台同步采集三路 USB 相机视频与 TwinCAT ADS 机器人运动学数据（四臂全量符号），并提供跨模态时间对齐与数据后处理工具，目标是为 SRT-H 风格的分层手术机器人模型构建训练数据集。

## 环境安装

```bash
conda env create -f environment.yml
conda activate SurgDataColl
# 或者
pip install -r scripts/requirements.txt
```

## 快速开始

```bash
# 1. 分配摄像头到各条臂（含真实 FPS benchmark，会写回 scripts/config/default.yaml）
#    当前硬件绑定集中写在 default.yaml 顶层 camera_bindings：arm_1=2, arm_2=0, arm_3=3
scripts/tools/camera_assignment_setup.bat          # 双击（Windows）
python scripts/tools/camera_assignment_setup.py    # 命令行

# 2. 手动开始/停止采集（自动生成 test_YYYYMMDD_HHMMSS session）
scripts/tools/start_manual_acquisition.bat         # 双击
python scripts/run_acquisition.py --config scripts/config/default.yaml --duration 300

# 3. 时间对齐（SRT-H 风格：相机锚点 30 Hz + 位姿插值）
python scripts/tools/align_camera_anchor.py --session sessions/<session_name>
```

## 目录结构

```text
├── scripts/
│   ├── run_acquisition.py        # 采集入口
│   ├── acquisition/              # 采集框架（Session + DataSource + registry/config/layout）
│   │   ├── sources/              # twincat / usb_camera_process / reserved_arm
│   │   │   └── legacy/           # 已弃用的线程版相机源（保留兼容）
│   │   └── process_workers/      # 相机子进程 worker
│   ├── config/                   # default / single_arm / example; historical configs are in archive/
│   │   └── archive/              # 历史测试配置（不再使用）
│   ├── tools/                    # 对齐、分析、相机分配、手动采集工具
│   └── test/                     # TwinCAT benchmark 与诊断脚本
│       └── archive/              # 历史验证脚本（不再使用）
├── sessions/                     # 采集数据（git 忽略）
├── var/camera_test_frames/       # 相机探测测试帧（git 忽略）
└── tech_repo/                    # 技术文档
    ├── VLGP_项目状态与技术备忘_20260803.md   # 主文档，持续更新
    └── archive/                  # 已被覆盖的旧文档
```

## 更多文档

- **项目状态与技术备忘**（主文档）：`tech_repo/VLGP_项目状态与技术备忘_20260803.md`
- 技术文档 / 进展报告：`tech_repo/VLGP_项目技术文档_20260719.md`、`tech_repo/VLGP_项目进展报告_20260719.md`
- 会话恢复检查点：`SESSION_CHECKPOINT.md`
- Agent 开发约定：`scripts/AGENTS.md`
