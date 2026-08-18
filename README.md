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
#    当前硬件绑定集中写在 default.yaml 顶层 camera_bindings：arm_1=1, arm_2=0, arm_3=3
scripts/tools/camera_assignment_setup.bat          # 双击（Windows）
python scripts/tools/camera_assignment_setup.py    # 命令行

# 2. 手动开始/停止采集（自动生成 test_YYYYMMDD_HHMMSS session）
scripts/tools/start_manual_acquisition.bat         # 双击
python scripts/run_acquisition.py --config scripts/config/default.yaml --duration 300

# 3. 时间对齐（SRT-H 风格：相机锚点 30 Hz + 位姿插值）
python scripts/tools/align_camera_anchor.py --session sessions/<session_name>
```

## 采集后的数据处理流程

每条 session 采集完成后按以下顺序处理（详细规范见 `tech_repo/VLGP_标定与元数据规范_20260817.md` 第 6 节）：

```bash
# 1. 当场补录元数据（任务指令/任务类型/操作者/标定 ID，不可回溯，先做）
python scripts/tools/write_session_meta.py --session sessions/<session_name>

# 2. 对齐预检（漂移与同步质量检查；异常就停下来排查）
python scripts/tools/verify_alignment.py --session sessions/<session_name>

# 3. 生成 30 Hz 相机锚点对齐表（关注对齐误差 p99 < 15 ms、覆盖率）
python scripts/tools/align_camera_anchor.py --session sessions/<session_name>

# 4. 人工裁剪与审核（GUI：运动量曲线定位首尾静止段，[/] 打 IN/OUT，
#    1/2/3 标 approved/rejected/pending，S 保存；决策写入 annotations.json，
#    原始数据不动。无对齐表的废品 session 会进入降级视图，只能标 rejected/pending）
python scripts/tools/review_session.py --session sessions/<session_name>

# 5. 构建 SRT-H 训练 episode（自动应用 trim、带入 session 元数据；
#    核对样本数与动作数值范围）
python scripts/tools/build_srth_dataset.py --session sessions/<session_name>

# 6. 目检视频（逐路检查画面连续性、色彩、时间戳叠加）
python scripts/tools/images_to_video.py --dir sessions/<session_name>/camera_arm_2 \
    --output sessions/<session_name>/arm2_preview.mp4 --timestamp-overlay
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

- **项目状态备忘（当前快照，2026-08-16）**：`tech_repo/VLGP_项目状态与技术备忘_20260816.md`
- **项目状态与技术备忘**（详细技术背景主文档）：`tech_repo/VLGP_项目状态与技术备忘_20260803.md`
- 执行 Agent 任务交接与验收标准：`AGENT_HANDOFF.md`
- 技术文档 / 进展报告：`tech_repo/VLGP_项目技术文档_20260719.md`、`tech_repo/VLGP_项目进展报告_20260719.md`
- 会话恢复检查点：`SESSION_CHECKPOINT.md`
- Agent 开发约定：`scripts/AGENTS.md`
