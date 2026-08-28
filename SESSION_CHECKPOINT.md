# Session Checkpoint

> 更新时间：2026-08-26（北京时间）
> 目的：保存当前会话上下文，便于下次 `kimi resume` 后继续工作。

## 当前状态

仓库已完成结构整理并推送到 GitHub：`git@github.com:DrypotTofu/data_alignment.git`。

**当前状态快照见 `tech_repo/VLGP_项目状态与技术备忘_20260816.md`**（2026-08-16 起为当前备忘）；详细技术背景见 `tech_repo/VLGP_项目状态与技术备忘_20260803.md`（PTP 调研、对齐策略、SRT-H 方案、DAgger 规划）。本文件只保留恢复会话所需的最小上下文。

**2026-08-16 起进入「采集就绪」冲刺阶段**：具体操作与测试由执行 Agent 承担，任务清单、验收标准与汇报规范见根目录 **`AGENT_HANDOFF.md`**（T1–T6）。项目管理 Agent 负责验收。

## 最近完成的工作

1. **T1（gripper_angle 量程与方向反推）**：`scripts/tools/analyze_gripper.py` 扫描全部 session：全局量程 ≈ [-55, +85]（arm_1 [-43.1, 83.6]、arm_3 [-54.4, 50.3]）；`gripper_signal` 恒 0 → 张开方向离线不可证，约定「角度增大=张开」待实测；归一化占位 `jaw=clip((ga+60)/150,0,1)`。详见备忘 13.5 节补充。
2. **T2（build_srth_dataset.py）**：核心开发完成并在 `test_binding_alignment_20260814_170005` 离线验证：单元自检 6/6 PASS；1156→706 网格行（15 ms 过滤 21%），646 个 60 步 chunk 样本（40 维）；6D 旋转正交归一 ✅、四元数模长 1.0 ✅、delta_pos 量级正确（静止 session ~0.05 mm）；HL 样本 20 个。`align_camera_anchor.py` 增补镜系位置列（向后兼容）。
3. **发现并上报**：PLC 无镜系四元数（`delta_rot_6d` 用基座系，角不变轴差未标定旋转）；gripper 方向待 T5/T6 实测；scipy 1.17 移除 `Rotation.slerp`（自检用自带实现）。
4. **四臂 TwinCAT 全数据采集**：`output_all_arms=true`，读取 PLC 全部 8 类符号（关节角、双坐标系位姿、四元数、器械类型、夹钳角度/信号、能量器械状态），实测 ~320–340 Hz/臂。
5. **相机锚点对齐工具**：`scripts/tools/align_camera_anchor.py`（策略二：30 Hz 相机锚点 + LERP/SLERP 位姿插值），已用 `sessions/test_20260803_104724` 验证。
6. **cam1 新摄像头结论**：MSMF + 640×480 + MJPG + fps=30 可稳定 30 fps；DSHOW 设置 FPS 会降到 ~20 fps；`usb_camera_worker.py` 已修复 `fps: null` 不再强制设置 `CAP_PROP_FPS`。
7. **PTP 调研**：已写入 tech_repo 0803 备忘第 16 节（EL6688 + 海康 GigE PTP 可行性与成本）。
8. **仓库结构整理**：旧 config/test 文件归档到各自 `archive/`，弃用采集源移入 `acquisition/sources/legacy/`，依赖文件已统一。
9. **磁盘清理（2026-08-16）**：删除 17 个被取代/无引用的旧测试 session + 派生的 `srth_episode` + 旧相机探测帧，释放约 **22.8 GB**（sessions 29.8 GB → 7.1 GB）。保留 session 均为文档引用或当前工作所需；`srth_episode` 可随时用 `build_srth_dataset.py --session ...` 重建。
10. **T0 硬件阻塞解除（2026-08-17）**：采集机重装 Windows 后 TwinCAT/ADS 链路修复完成。本机 `TcSysSrv` 恢复运行；本机 `StaticRoutes.xml` 重建指向 192.168.22.22 的静态路由；机器人侧新增 `CollectionPC` 路由（本机 NetID `192.10.100.251.1.1` → `192.168.22.100`）。确认机器人 NetID 仍为 `169.254.226.99.1.1`（`default.yaml` 无需改动），PLC 已 Run（RTIME 4132 随重装解决）；8 类符号全部读通，全符号轮询 290.7 Hz、0 错误。详见 `tech_repo/VLGP_TwinCAT_ADS链路修复说明_20260817.md`。
11. **T3 长稳测试通过（2026-08-17）**：session `sessions/20260817_124143`（10 min）。修复了两处前置问题：① **720p PNG 写盘瓶颈**——arm_2 重装后仅 ~16 fps，`writer_threads` 2→6（arm_2）、1→2（arm_1）后三路均 ≥29.99 fps（PNG L1 无损，~37 GB/30min）；② **align_camera_anchor.py 性能**——预计算重构后 18k×190k 秒级完成。实测：arm_1 30.001 / arm_2 29.990 / arm_3 30.002 fps；TwinCAT 316.8 Hz；漂移残差 std 0.286 ms；对齐覆盖率（机器人区间）100%；无掉线/堆积/异常。相机绑定修正为 arm_1=1、arm_2=0、arm_3=3（用户确认）；index 0 实测原生 720p@50 fps，原「29.7 fps 房景相机」结论已失效。
12. **T7/T8/T9 落地（2026-08-17）**：新增 `scripts/tools/collect_calibration.py`（标定板采集+内参计算，示例记录 `calibrations/calib_example_synthetic/`）、`scripts/tools/write_session_meta.py`（session 元数据工具）；规范文档 `tech_repo/VLGP_标定与元数据规范_20260817.md`、沟通文档 `tech_repo/T9_PLC四元数坐标系确认_20260817.md`。
13. **T8-集成完成（2026-08-18）**：`build_srth_dataset.py` 自动读取 `<session>/session_meta.json`，将 `task_instruction`/`task_type`/`organ_model`/`instruments`/`camera_calibration` 等透传进 episode `language.json` 与 `meta.json`；在 `sessions/20260817_124143` 填入示例 `session_meta.json` 重建 episode 验证 ✅（episode `sessions/20260817_124143/srth_episode`）。
14. **T4 三路 30 Hz 全量验证通过（2026-08-18）**：session `sessions/20260818_080808`（30 s）——arm_1 30.028 / arm_2 30.011 / arm_3 30.026 fps（≥29.9 ✅）；对齐误差 mean ~0.9 ms；机器人有效区间内覆盖率 100%。两处待 PM 决策/知悉：① arm_2 帧间隔 std 10.6 ms 超 6 ms 验收线（720p PNG L1 写盘抖动，PNG0/JPG 可降但存储 150 GB/30min）；② 30 s session 全局覆盖率 77.5% 系相机 pre-roll（~8.5 s 启动开销）所致，10 min 为 98.5%，数据集构建已按 pose_valid 过滤不受影响。
15. **T11 定稿（2026-08-18，用户接受）**：arm_1/arm_3 启用自动白平衡（`auto_wb: 1`），手动色温 5000/4300K 作 fallback；worker 修复 AWB 与手动色温互斥问题；`camera_param_tuner.py` 实时调参工具（trackbar 范围映射修复 + DRIVER REJECTED 提示）。实测 AWB 优于手动（白纸中性 6.1%、R-clip 0.76%/1.9%、帧间稳定）；20 s session `test_awb_20260818` 目检接受。
16. **T10 v1 完成（2026-08-18）**：`scripts/tools/review_session.py`（PyQt5 三路同步视图 + 运动量曲线 + IN/OUT + 批量队列）+ `build_srth_dataset.py` 集成 annotations.json（rejected 跳过 / trim 过滤 / meta 记录）。逻辑验收通过（trim 15116→10160 行、episode 10100 样本）；GUI 实机完整评审待桌面操作。
17. **T12+T13 完成（2026-08-19）**：① T13 src 包迁移——`pyproject.toml` + `src/vlgp/{acquisition,alignment,dataset,review,calibration,analysis,capture}`，`pip install -e .` 后任意 cwd 可 `import vlgp.*`；删除全部 sys.path hack；worker 子进程 `-m vlgp.acquisition...`；路径常量收口 `session_layout`、魔法数字进 `constants.py`；unify/analyze_alignment 移 archive；bat 改 `%~dp0`。② T12——相机死亡 fail-fast（worker 非零退出 + ready raise + session.json `worker_exit_codes`）、TwinCAT CSV 瘦身（常量→sidecar，行内留逐样本字段，下游兼容核对通过）、legacy deprecation、分层泄漏收口、import/编码清理、docstring 更新。回归：30s 采集（vlgp 框架）三路 30.0 fps + TwinCAT 43392 样本 + verify/align/build 全通。commit e57b91f。**迁移后命令**：`python -m vlgp.alignment.align_camera_anchor --session ...` 等（README 已更新）。
18. **T14 完成（2026-08-21）**：`build_srth_dataset.py` 将 TwinCAT 实测 `instrument_type`（episode 区间众数）写入 `meta.json['instruments']`（id + 中文名，对照 `tech_repo/器械编码对照表.md`）；超范围编号落 raw id + unknown 标签；换器械告警；`write_session_meta.py` 可选预填。合成验证 arm1=4→圆头抓取钳 ✅。commit fbc9a6f。
19. **T16 前置核对（2026-08-21）**：SRT 约定 `q_t⁻¹⊗q_{t+1}` frame-invariant（消解 T9）；当前实现 `R1@R0^T` 非 frame-invariant。不阻塞采集；改约定待 PM 定夺。
20. **发现：`test_20260821_164544/192759` robot_pose=0**（missed_cycles 444/3，ADS 未连上）——采集时 PLC/ADS 未就绪，非框架 bug；正式采集前需确认 PLC 开机 + ADS 可达。
21. **ADS 复测不可达（2026-08-21）**：pyads open 报 `ADSError 1861 timeout`——PLC/ADS 链路当前未就绪，需机器人侧处理（参考 T0 修复文档）；阻塞 T14 真实回归、T16 数据验证、T5+T6。
22. **ADS 诊断结论（2026-08-21）**：192.168.22.22 的 48898 端口不可达 + ping 无响应 → **机器人控制器当前不在线（未开机/网络断开）**，本机 StaticRoutes.xml 完好（RobotController 路由在）。属机器人侧硬件问题，需用户/工程师开机处理。
22. **T16 模块实现（2026-08-21，commit 35351c3）**：`src/vlgp/kinematics/`——MDH FK（4 臂，自检 12/12 PASS）+ Kabsch 基座系→镜系配准（单/多 session，自检恢复已知变换）。真实数据验证待 ADS 就绪。

23. **T14 真实数据回归 + 全链路（2026-08-26）**：ADS 恢复后首个有效 session `20260821_123455`（30s，4 臂 x10677 行）verify→align→build 全通；meta.json['instruments'] arm2=13 内窥镜 ✅（真实数据，非合成）。
24. **T16 delta_rot_6d 局部系改造（2026-08-26，commit a2bf3d0）**：按 PM 2026-08-23 决策改为 `R_rel=R(q_i)^T@R(q_{i+1})`（SRT hybrid 约定）；自检 7/7 PASS；静止段回归 median 旋转角 0.000 mdeg（尾部 ~0.05° 为 PLC 四元数量化 ~1e-4 噪声）；新旧约定共轭关系 `R0^T*old*R0=new` 精确到 7.77e-16。
25. **T16 真实数据验证启动（2026-08-26，commit a2bf3d0/f115dc5）**：新增 `vlgp/kinematics/validate.py` + `validate_kinematics.bat`（报告 `sessions/20260821_123455/t16_validation.json`）；FK vs PLC 全部映射假设失败（4 行映射 + 6144 符号组合 0 命中）→ 需单关节运动实验；Kabsch 单一刚体变换不成立（RMS 68.95mm）→ 疑逐臂安装基座；结论与协议见 `tech_repo/T16_FK与PLC对标验证_20260821.md`。

26. **T15 受控词表占位符版落地 + 框架模块（2026-08-26，用户指示先搭框架）**：① `scripts/config/task_hierarchy.yaml`（v0.1，`status: placeholder`）——task=触针/抓取/缝合/待机 × subtask + 指令模板，包含 `segments` schema 契约与定稿 TODO 清单；② 新增 `src/vlgp/annotations/` 包 + `task_hierarchy.py`（加载/结构校验、路径与片段校验、自检 CLI，自检全 PASS）。词条语义待 PM/操作者定稿，不阻塞 Web 平台 v0/v1 框架搭建。

27. **T15 Web 平台后端骨架落地（2026-08-26）**：`src/vlgp/web/`（FastAPI）——会话列表/详情、运动量曲线、JPEG 预览帧服务（单帧 + batch 预取）、annotations 读/校验/原子写、task-hierarchy API；静态前端（会话列表 + 三路 canvas 帧视图 + 运动量条 + 播放/逐帧/IN-OUT/状态/保存）；启动 `python -m vlgp.web.app`（127.0.0.1:8765）或 `start_web_platform.bat`；冒烟测试 9/9 通过。依赖注记：本机 Smart App Control （强制）按信誉阻止 conda-forge 的 pyexpat.pyd，env 内 pip 不可用；fastapi/uvicorn 已用 base pip 下载 + 手动解包装入 env，后续 pip/需用户处理 SAC。v0 与 PyQt5 功能逐条对齐待办。

28. **SAC 已关闭 + T15 v0 对齐（2026-08-26）**：用户关闭 Smart App Control（VerifiedAndReputablePolicyState=0），pyexpat/pip 恢复，fastapi/uvicorn 已用 pip 正式重装（`pip check` 通过）。v0 对齐：① PUT 服务端按 PyQt5 同格式补 `reviewed_by`/`reviewed_at`，**Web 与 PyQt5 同操作序列输出 262 字节完全一致** ✅；② 评审队列 `GET /api/review/queue` + `review_manifest.json`（N 键下一个未评审）；③ J/K/L 快进快退对齐。剩余：播放速度选择、运动量曲线图例/缩放、多 session 顺序评审整体回归。

29. **T15 v0 播放速度 + 运动量曲线打磨（2026-08-26）**：① 速度选择器 0.5x/1x/2x/4x，改为基于时间的行推进（`accum += dt×speed×30`，同 PyQt5 `_tick`），J/K/L 跳`max(1,speed×5)` 帧；② 曲线逐线各自最大值归一 + 图例 + IN/OUT 虚线标记 + 白色当前行标记；`/alignment` 增 `points` 参数（自适应降采样，长 session 轻量），NaN 行已清洗。剩余：多 session 顺序评审整体回归。

30. **T15 v0 整体回归通过（2026-08-26，`scripts/test/verification/web_regression.py` 37/37 PASS）**：会话发现/运动量/帧服务/annotations 往返+ 7 类 422 校验/四场景逐字节一致（approved/rejected/pending/含 segments）/评审队列顺序流程（评审→队列收缩→全审→队列空，manifest 逐字节一致）/task-hierarchy/index/openapi。顺带修复：① `review_queue` 只收含对齐表会话（对齐 PyQt5 `collect_sessions`）；② segments 行号越界校验。测试产物自动还原。**v0 功能基线完成**；下一步：词表语义定稿后进 v1 片段标注 UI。

31. **Web 自动对齐生成（2026-08-26，用户需求）**：选中未生成对齐表的 session 时自动生成：新增 `POST /api/sessions/{name}/align`（`vlgp/web/align_service.py`，复用 CLI 同款对齐管线，与 CLI 产出逐字节一致）；幂等（已对齐返回 `already_aligned`）；无机器人数据 422（PLC/ADS 可能未就绪提示）；前端自动触发 + 「未对齐」徽标。回归新增 G 节（幂等/404/422），**40/40 PASS**。附：修复旧 uvicorn 孤儿进程占用 8765 端口问题（job 结束后确认子进程清除）。

32. **对齐进度条 + 四臂机器人状态面板（2026-08-27，用户需求）**：① `build_camera_anchor_alignment` 增可选 `progress_cb`（五阶段），`POST /align` 改异步（立即返回 started），前端轮询 `/align/status` 显示进度条；② 新增 `/row/{row}` 四臂姿态快照 + 前端四臂切换 Tab + 状态面板（欧拉角前端换算）；③ 修复 422 错误消息提取与 `enumerate(iterrows())` 回归 bug（测试即捕获）。回归新增 H 节，**43/43 PASS**。附：再次发现并清理旧 uvicorn 孤儿占用 8765（job_kill 只杀 wrapper，子进程需手动检查）。

33. **T17 完成（2026-08-27，LeRobotDataset v3.0 导出器 + 往返验证 12/12 PASS）**：① 契约核定——pin `lerobot==0.4.4`（PyPI 最新；AGENT_HANDOFF 中 "v0.6.x" 是 HF 文档线编号），读安装包源码核定 v3 schema：`meta/tasks.parquet`（**非** tasks.jsonl）、`data/`+`videos/` 多 episode 合一 Parquet/MP4 分片、`meta/episodes/` 分块 Parquet；官方写入 API `LeRobotDataset.create(..., vcodec=)`→`add_frame`→`save_episode`→`save_episode_tasks`→`finalize`；`timestamp=frame_index/fps` 由库生成（delta_timestamps 的 1/fps 轴）。② 导出器 `src/vlgp/dataset/export_lerobot.py`：state float32[32]/instrument_type int64[4]/active_mask bool[4]/action float32[40]/三路视频，`--repeat/--vcodec/--parallel`，`--out` 已存在拒绝。③ 验证器 `scripts/test/verification/lerobot_roundtrip.py` 5 节 12 项**全 PASS**：官方类零警告加载、state/action max|diff|=0、时间戳 1/fps 轴、三路图像解码 vs 源 PNG（worst mean|diff| 2.35~3.28，纯 yuv420p crf30 噪声）、delta_timestamps 窗口采样、3 episode 分片 2031 帧。④ 修复两 bug：解码帧是 CHW float32（测试比较逻辑误判形状）；导出器 `cv2.imread` BGR 需转 RGB（lerobot 视频管线全程 RGB，修复前 R/B 互换 worst 15.96~23.23）。产物 `datasets/srth_demo_20260821`（session 20260821_123455 的 srth_episode ×3，h264）。注：视频编解码与数据集文件需 danger-full-access，导出+验证须同一权限上下文。`environment.yml` 已加 `lerobot==0.4.4`。

34. **Web 曲线按臂选择 + 播放按钮修复（2026-08-27，用户需求）**：① 运动量曲线新增选臂条「全部/arm1-4」（`#curve-bar`），单臂模式只画该臂 dpos 曲线，arm1 模式附带 gripper 曲线；「全部」模式补画 gripper arm1 蓝线（此前图例有但未画，PyQt5 `_redraw_curve` 口径），图例随选择自适应。② 播放控制器按钮改**内联 SVG 图标**（play/pause/prev/next，固定 34px 宽）——原 `▶/⏸` 字形在中文 Windows 可能渲染为 tofu/emoji，且字形宽度变化导致按钮在 mousedown/mouseup 之间位移、click 丢失 → **播放后点暂停无效**；固定尺寸 + SVG 后无布局跳动，暂停可靠。样式统一：按钮统一高度 28px、hover 态、播放键主题色、select 同风格。静态文件由 FastAPI 磁盘直供，浏览器刷新即生效（已验证 200）。

## 待办（执行顺序与验收标准见 `AGENT_HANDOFF.md`）

- T1 ✅（2026-08-16）：gripper 量程/方向反推完成，方向待实测确认。
- T2 ✅（2026-08-16）：`build_srth_dataset.py` 实现 + 离线验证通过；待真实任务 demo 验证动作数值与 mask 约定。
- T3 ✅（2026-08-17）：10 分钟长稳测试通过（session `20260817_124143`）；30 min 版本可随 T6 一并复验。
- T4 ✅（2026-08-18）：三路 30 Hz 全量验证通过（session `20260818_080808`）。
- T5+T6：端到端试跑（⏸️ 暂缓解除，T9 答复已回收；**需机器人可动 + 现场**：已知方向平移 → gripper 开合实测 → 触针/抓针演示 + 30 min 长稳复验；里程碑验收，建议与 T7-现场同场）。
- T7：标定流程与工具已落地；真实标定板采集待现场执行（需棋盘格；手眼标定降级为可选交叉验证）。
- T8 ✅（2026-08-18）：工具 + 集成完成。
- T9 ✅（2026-08-20）：答复回收关闭（四元数=基坐标系）。
- T10 ✅（2026-08-18）：GUI 实现 + 集成完成；**实机完整评审待桌面操作**。
- T11 ✅（2026-08-18）：腕部相机成像质量修复完成（AWB）。
- T12 ✅（2026-08-19）：框架健壮性（fail-fast / CSV 瘦身 / 清理 / 收口）。
- T13 ✅（2026-08-19）：src/vlgp 包迁移完成，命令改为 `python -m vlgp.*`。
- T14 ✅（2026-08-26）：instrument_type 透传完成（合成验证 + 真实回归通过：arm2=内窥镜）。
- T15：片段级任务层级与指令标注（⏸️ 待 PM 定义受控词表 `task_hierarchy.yaml`；建议 T10-评审后实施）。
- T16：MDH FK + R_bs 解算（优先级已下调，不阻塞；delta_rot_6d 已按 PM 决策改为局部系约定并回归 ✅；FK 映射与 R_bs 待单关节运动数据 + 工程师确认，协议见 T16 备忘第四节）。
- T17 ✅（2026-08-27）：LeRobotDataset v3.0 导出器 + 往返验证 12/12 PASS（`src/vlgp/dataset/export_lerobot.py` + `scripts/test/verification/lerobot_roundtrip.py`；lerobot==0.4.4 已入 environment.yml；产物 `datasets/srth_demo_20260821`）。
- 已确认（2026-08-16）：四元数顺序 wxyz；PLC 输出基座系 + 镜系**位置**两套，动作标签 delta_pos 用镜系；**PLC 无镜系四元数**（delta_rot_6d 用基座系四元数按局部系约定计算，对常值旋转不变）。
- 暂缓：EL6688 PTP、DAgger GUI、存储格式迁移（本阶段范围外）。

## 如何恢复会话

```bash
kimi resume
```

进入工作目录后，先读 `tech_repo/VLGP_项目状态与技术备忘_20260816.md`、`AGENT_HANDOFF.md` 和根目录 `README.md`。
