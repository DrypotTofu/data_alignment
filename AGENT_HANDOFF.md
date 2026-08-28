# AGENT 任务交接：开始采集标注/训练数据前的就绪工作

> 创建日期：2026-08-16；最近更新：2026-08-26（执行 Agent：T14 真实数据回归完成（instruments=内窥镜）；T16 delta_rot_6d 局部系改造完成 + 真实数据验证启动（FK/Kabsch 待单关节运动）；剩余 T5+T6 / T7-现场 / T10-评审 / T15 / T16（部分））。
> 2026-08-23 PM 更新：① T15 定稿 Web 架构（FastAPI + JS 前端），v0 先迁移 T10 裁剪功能进 Web 平台（迁移完成前 PyQt5 版继续可用）；② PM 决策：`delta_rot_6d` 改为 SRT 局部系约定（见 T16 决策注记）。
> 2026-08-26 PM 更新：图像工程师答复——内窥镜畸变由主机内置矫正（出厂标定存镜头 EEPROM），T7-现场标定范围收缩为腕部双相机；内窥镜双目两路输出可行性待现场确认。
> 2026-08-26 PM 更新 2：数据集格式决策——采用 **LeRobotDataset v3.0** 规范，新增 T17（格式对齐与导出器）；内窥镜双目两路可同时输出已确认（见 T7-现场）。
> 角色分工：项目管理 Agent 负责规划与验收；本文档面向执行 Agent，覆盖具体操作、测试与汇报要求。
> 阶段性目标（里程碑）：**达到「可以开始正式采集用于标注和模型训练的数据」的状态**。

---

## 1. 背景与必读材料

开始前请按顺序阅读：

1. 根目录 `README.md`：环境安装、采集/对齐快速开始命令。
2. `tech_repo/VLGP_项目状态与技术备忘_20260816.md`：当前阶段快照。
3. `tech_repo/VLGP_标定与元数据规范_20260817.md`：**T7/T8 规范与操作者流程（第 6 节一页流程是正式采集的操作依据）**。
4. `tech_repo/VLGP_TwinCAT_ADS链路修复说明_20260817.md`：T0 修复记录（ADS 路由、符号清单）。
5. `tech_repo/VLGP_项目状态与技术备忘_20260803.md`：详细技术背景（13.3/13.5 节 SRT-H 数据构建方案、第 11 节标注规划）。
6. `tech_repo/VLGP_数据清洗与标注工具设计_20260818.md`：**T10 设计规范（做 T10 前必读）**。
7. `scripts/AGENTS.md`：开发约定。

环境：conda 环境 `SurgDataColl`（**重装系统后路径已变为 `D:\miniconda3\envs\SurgDataColl\python.exe`**），Windows + PowerShell。PyQt5 已于 2026-08-18 装入该环境并写入 `environment.yml` / `scripts/requirements.txt`。

## 2. 里程碑验收标准（Definition of Done）

以下全部满足，里程碑即达成：

- 三路相机 + TwinCAT 四臂在 `default.yaml` 下达到精确 30 Hz（每路 >= 29.9 fps），**30 分钟**连续采集无掉线、无写盘堆积（T3 已通过 10 分钟版本，30 分钟随 T6 复验）。
- 一条规范演示 episode 完整跑通：采集 -> `verify_alignment.py` -> `align_camera_anchor.py` -> `build_srth_dataset.py` -> `images_to_video.py` 目检。
- 产出的数据集样本通过数值合理性检查（动作幅度、符号、坐标系正确；对齐误差达标）。
- 标定与元数据规范落地：正式采集前完成一次真实标定板标定（T7 现场部分），episode 的 `language.json` 能从 session 元数据自动带入任务指令（T8）。
- 腕部相机成像质量达标：白平衡/曝光手动锁定，白纸中性、直射区红通道无 clipping、帧间亮度稳定（T11 验收线）。
- 所有结果已写入备忘文档并提交 git。

## 3. 任务清单

### 已完成（归档，供查阅；**无需执行 Agent 进一步处理**）

- **T1 ✅（2026-08-16）**：gripper_angle 量程与方向反推。全局量程约 [-55, +85]；`gripper_signal` 恒 0，张开方向离线不可证，暂约定「角度增大 = 张开」待 T5 实测确认。工具 `scripts/tools/analyze_gripper.py`。
- **T2 ✅（2026-08-16）**：`scripts/tools/build_srth_dataset.py` 实现 + 离线验证（`test_binding_alignment_20260814_170005`）。单元自检 6/6 PASS；646 个 60 步 chunk 样本（40 维）；静止 session delta_pos ~0.05 mm 量级正确。
- **T0 ✅（2026-08-17）**：硬件阻塞解除。采集机重装 Windows 后 RTIME 4132 解决；本机 `StaticRoutes.xml` 重建指向 192.168.22.22；机器人侧新增 `CollectionPC` 路由；机器人 NetID 仍为 `169.254.226.99.1.1`（`default.yaml` 无需改动）；8 类符号全读通，全符号轮询 290.7 Hz、0 错误。详见 `VLGP_TwinCAT_ADS链路修复说明_20260817.md`。
- **T3 ✅（2026-08-17，10 分钟版本）**：session `sessions/20260817_124143`。前置修复两处：720p PNG 写盘瓶颈（`writer_threads` arm_2 2→6、arm_1 1→2）；`align_camera_anchor.py` 预计算重构（18k×190k 秒级）。实测：arm_1 30.001 / arm_2 29.990 / arm_3 30.002 fps；TwinCAT 316.8 Hz；漂移残差 std 0.286 ms；对齐覆盖率 100%；无掉线/堆积。**相机绑定已确认为 arm_1=1、arm_2=0、arm_3=3；index 0 实测原生 720p@50 fps，旧「29.7 fps 房景相机必须替换」结论已失效。**30 分钟版本随 T6 复验。
- **T4 ✅（2026-08-18）**：三路 30 Hz 全量验证。session `sessions/20260818_080808`（30 s）：arm_1 30.028 / arm_2 30.011 / arm_3 30.026 fps（≥29.9 ✅）；丢帧 0（日志无 `Writer queue full, dropping frame`）；帧间隔 p99 < 66.7 ms ✅；对齐误差 mean ~0.9 ms / p99 < 15 ms ✅；机器人有效区间内覆盖率 **100%**（全局 77.5% 系相机 pre-roll ~8.5 s 启动开销，10 min session 为 98.5%，数据集构建已按 pose_valid 过滤，不受影响）。详细数据见 0803 备忘 13.5 节。
- **T7 工具与规范 ✅（2026-08-17，现场采集待做）**：`scripts/tools/collect_calibration.py` 已就绪（示例 `calibrations/calib_example_synthetic/`）；规范见 `VLGP_标定与元数据规范_20260817.md`。剩余：真实标定板现场采集（见下方 T7-现场）。
- **T8 工具与集成 ✅（2026-08-17 工具 / 2026-08-18 集成）**：`scripts/tools/write_session_meta.py` 已就绪；`build_srth_dataset.py` 已集成——自动读取 `<session>/session_meta.json`，将 `task_instruction`/`task_type`/`organ_model` 等透传进 episode 的 `language.json` 与 `meta.json`；已在 `sessions/20260817_124143` 上重建 episode 验证字段透传 ✅。
- **T9 ✅（2026-08-17 沟通文档 / 2026-08-20 答复回收关闭）**：工程师确认 `EndEffector_Orientation` 为**机械臂基坐标系**下的器械姿态四元数（wxyz）。`delta_rot_6d` 与镜系相差常值旋转，标定并入 T7-现场；TwinCAT `instrument_type` 字段派生 T14。详见 `tech_repo/T9_PLC四元数坐标系确认_20260817.md` 答复记录。
- **T10 实现 ✅（2026-08-18，实机评审待桌面操作）**：`scripts/tools/review_session.py`（PyQt5 单文件：三路同步视图 + 运动量曲线 + IN/OUT 裁剪 + approved/rejected/pending + 批量队列）+ `build_srth_dataset.py` 集成（rejected 跳过 / trim 过滤 / meta 记录）。逻辑验收通过（trim [3000,15000] → 15116→10160 行、episode 10100 样本；rejected 跳过正常；GUI 冒烟通过）。**实机完整评审（设计文档第 5 节）待用户桌面操作**（见下方 T10-评审）。
- **T11 ✅（2026-08-18）**：腕部相机成像质量修复。根因：DSHOW 白平衡锁定 4600K（暖）vs 内窥镜冷白 LED → 蓝偏。方案：worker 新增手动 exposure/wb-temperature/auto-wb/brightness/contrast/saturation 支持（含回读验证）；`camera_param_tuner.py` 实时调参工具。**定稿：arm_1/arm_3 启用自动白平衡（`auto_wb: 1`），手动色温 5000/4300K 作 fallback**；实测 AWB 优于手动（白纸中性 arm_1 6.1%、R-clip 0.76%/1.9%、帧间稳定 <1%）；20 s 目检 session `test_awb_20260818` 用户接受。详见 0803 备忘 13.5 节。

### T4. 三路 30 Hz 全量验证 ✅（2026-08-18 完成，详见归档）

- session `sessions/20260818_080808`（30 s）：arm_1 30.028 / arm_2 30.011 / arm_3 30.026 fps（≥29.9 ✅）；丢帧 0；帧间隔 p99 < 66.7 ms ✅；对齐误差 p99 < 15 ms ✅；覆盖率（机器人区间内）100% ✅。

> **PM 决策（2026-08-18）：帧间隔 std 不再作为验收线。** 背景：arm_2 720p PNG L1 写盘带来 ~10 ms 级帧间隔 std（T3 已见 9.4 ms）。代码确认时间戳在 `cap.read()` 返回时即打（`usb_camera_worker.py` 134-137 行），PNG 编码/写盘在独立 writer 线程且队列缓冲约 2 秒，抖动不进时间戳；对齐管线全程使用真实时间戳（锚点网格 + 最近帧匹配 + 位姿按真实时刻插值），帧间隔不均匀已被架构消化。因此验收改为卡「丢帧数 = 0 + 帧间隔 p99 < 66.7 ms + 对齐误差/覆盖率」这组直接反映数据质量的指标。存储策略维持 PNG L1 无损（PNG0 150 GB/30 min 不可接受，JPG 有损不可用于训练数据）。

### T7-现场. 真实标定板内参采集（正式采集前必须就位）

- **标定范围修订（2026-08-26，图像工程师答复）**：内窥镜（arm_2）的畸变矫正**在主机内完成**——出厂标定参数存于镜头 EEPROM/FLASH，主机上电读取并内置算法矫正，采集卡拿到的 720p 图像已是**矫正+靶面裁剪后的成品**，无需也不应再做现场去畸变。因此现场标定范围收缩为：
  - **arm_1 / arm_3（腕部 USB 相机）**：仍按原计划做完整棋盘格内参标定（含畸变系数）；
  - **arm_2（内窥镜）**：不做畸变标定。仅当未来需要度量级视觉（手眼 PnP、深度）时，在**输出图像**上自标一次等效 K（畸变系数固定为 0，只标内参矩阵）——出厂参数存在镜头里且主机做了靶面裁剪，直接用不上。
  - **双目事项（2026-08-26 已确认）**：内窥镜为**双目镜子**，出厂已做两路时钟帧同步 + 垂直偏差校正（用于 3D 合成）；**主机可同时输出两路图像**（用户现场确认）。当前采集卡只取一路 720p（index 0）。后续可选项（非阻塞，SRT 深度靠腕部相机）：接入第二路采集通道获得同步双目流，解锁内窥镜双目深度匹配；实施前需确认当前采集的是左目还是右目、第二路的接口形态（采集卡/接口带宽）。
- 操作：打印棋盘格（内角点 9×6，方格 20–25 mm）并**实测**边长；腕部相机逐台用 `collect_calibration.py` 采 15–25 张合格帧；运行内参计算。
- 验收：各相机 RMS < 0.5 px；产出 `calibrations/calib_<ts>/`（含 `calibration.json` 与各相机 `intrinsics.yaml`）；记录标定 ID。
- 注意：对焦/位置变动后必须重标（腕部相机）；内窥镜改动主机设置或换镜后需重新评估等效 K。
- **手眼标定扩展（2026-08-20 并入，源自 T9 答复）**：T9 已确认 `EndEffector_Orientation` 四元数定义在**机械臂基坐标系**，而 `delta_rot_6d` 期望镜系动作轴，两者相差一个常值旋转 `R_arm`。内窥镜固定时该误差为常值、短期可接受（不阻塞 T5/T6 与试采集），但**正式训练数据采集前必须标定**。方案见 `T9_PLC四元数坐标系确认_20260817.md` 第 3 节：内窥镜固定、臂端标记物摆 ≥8 组位姿，与内参标定同场完成；产出 `calibrations/<id>/hand_eye/`，`build_srth_dataset.py` 读取并应用。
  - **2026-08-20 更新：相机手眼方案降级为可选交叉验证。** 收到 MDH 参数表后，基座系→镜系变换可用 **T16** 的离线数据路线解算（Kabsch 配准成对位置点，无需标定板）。若 T16 闭合，本项不再阻塞正式采集；相机方案仅作为误差交叉验证手段保留。

### T5+T6 合并. 端到端试跑（里程碑验收，半天）—— ⏸️ 暂缓（2026-08-19 PM 决定）

> 暂缓原因：机器人工程师尚未答复标定方法等内容（见 T9-跟进），里程碑验收等答复后再安排。暂缓期间优先执行 T12+T13。
> **2026-08-20 更新：T9 答复已回收（四元数=基坐标系），暂缓的技术原因已解除。** 手眼标定已并入 T7-现场，不阻塞本任务。是否启动由 PM 排期（建议与 T7-现场同场安排，一次上机完成演示 episode + 标定板采集）。

采一条 1–3 分钟演示 session，**同一条 session 内编排三段**：

1. 某臂沿已知方向纯平移约 2 cm（如朝镜头方向），其余臂静止 → 验 `delta_pos` 符号与轴向；
2. 该臂开→闭→开 → 实测 gripper 张开方向，固化 jaw 归一化（是否启用 `jaw_invert`、per-arm 参数），写回备忘 13.5 节；
3. 正常触针或抓针演示。

然后完整跑通：采集 -> `verify_alignment.py` -> `align_camera_anchor.py` -> `build_srth_dataset.py` -> `images_to_video.py` 目检视频。同时：

- 固化 `arm_active_mask` 阈值约定（当前为 0.05 mm/步 或 0.1°/步 启发式，按真实任务数据定稿）；
- 补一条 **30 分钟**连续采集，闭合 DoD 的长稳要求（T3 的 10 分钟版本之上复验）。
- **观察 AWB 稳定性（PM 验收附加项，2026-08-18）**：T11 定稿用自动白平衡，但验证只有 20 秒。本次 1–3 分钟含器械运动和组织颜色变化的演示中，检查腕部相机白平衡是否中途漂移（抽首尾帧对比 RGB 均值）。若漂移，回退到手动锁定 5000/4300K（`auto_wb: 0`）后重验。

汇报：完整指标表（fps、对齐覆盖率与误差、样本数、动作数值范围、gripper 方向结论、目检结论）、发现的问题清单。通过后由项目管理 Agent 宣告里程碑达成。

### T9-跟进. PLC 四元数坐标系确认 ✅（2026-08-20 答复回收，关闭）

- **Q1 = B**：`EndEffector_Orientation` 是器械在**机械臂基坐标系**下的四元数姿态（工程师确认）。
- **Q3 = wxyz**：已于 2026-08-16 实测确认，无需改动。
- 结论：`delta_rot_6d` 当前用基座系四元数计算，与镜系动作轴相差常值旋转 `R_arm`；内窥镜固定时误差为常值，短期可接受。固定旋转标定已并入 **T7-现场**（见上），正式训练数据采集前闭环。
- 附带收获：TwinCAT 数据含 `instrument_type` 逐样本字段，对照表见 `tech_repo/器械编码对照表.md`（0=无器械，1=大号持针钳 … 13=内窥镜）→ 派生 **T14**。
- **2026-08-20 补充**：工程师另提供四臂 MDH 参数表（`tech_repo/MDH参数表.md`），坐标系转换可改走数据侧解析路线 → 派生 **T16**（FK 验证 + Kabsch 解算基座系→镜系变换），相机手眼标定降级为交叉验证。

### T14. instrument_type 透传进数据集与元数据 ✅（2026-08-21 完成，commit fbc9a6f）

- `build_srth_dataset.py`：从对齐表读取每臂 `instrument_type`（episode 区间众数），写入 `meta.json['instruments']`（`{armN: {present, id, name, all_ids, all_names, swapped_within_episode}}`）；编号→名称对照表内置于代码（来源 `tech_repo/器械编码对照表.md` 2026-08-20）；超出对照表落 raw id + `unknown-N` 标签并告警，不中断构建；episode 内换器械（多编号）打印告警并记入 meta。
- `write_session_meta.py`：可选预填 `instruments`（采集时已知可手填；构建时以 TwinCAT 实测值为准）。
- **验证**：当前可用 session 无 robot_pose 数据（见下「robot_pose=0 发现」），改用合成对齐表验证——arm1=4→圆头抓取钳 ✅、arm2=13→内窥镜 ✅、unknown-999/NaN 安全 ✅、单元自检 6/6 PASS。
- **遗留**：真实 session 端到端回归待 PLC/ADS 就绪后补（届时重新构建一条含器械的 episode 核对 meta）。

### T16 前置核对（2026-08-21 完成，结论汇报）

按 PM 调研要求，先核对 `build_srth_dataset.py` 的 `delta_rot_6d` 约定，**结论**：

- SRT hybrid 约定的 delta 旋转 = **当前末端系相对旋转** `q_t⁻¹ ⊗ q_{t+1}`：数学验证 **frame-invariant**（与基座系/镜系选择无关）——若采用，T9 遗留风险**直接消解**。
- **当前实现**为 `q_1 ⊗ q_0⁻¹`（`R1 @ R0^T`，世界系相对旋转）：**非 frame-invariant**（随姿态变化的轴误差，正是 T9 报告的问题）。
- 按 PM 立场（SRT 无标定、delta 用局部系约定即可），当前实现**不阻塞 T5/T6 与正式采集**。
- **待 PM 定夺**：是否在 T16 实施时将 `delta_rot_6d` 改为 `q_t⁻¹ ⊗ q_{t+1}`（改动影响训练数据语义与既有 episode，需确认后再动）。

### 发现：robot_pose=0 的新 session（2026-08-21，正式采集前需排查）

`test_20260821_164544`（37 min）/ `test_20260821_192759` 的 `samples_read=0`、`missed_cycles=444/3`——TwinCAT ADS 连接从未成功。**非框架 bug**（worker 正常启动、`worker_exit_codes={}`），疑似采集时 PLC/ADS 链路未就绪。**正式采集前必须确认 PLC 开机 + ADS 可达**（检查步骤见 `VLGP_TwinCAT_ADS链路修复说明_20260817.md`）。

### T10-评审. 数据清洗/裁剪 GUI — 实机完整评审（实现已完成 ✅，待桌面操作）

**实现已完成（2026-08-18，commit 8bef0a3 / d54eea7）**：
- `scripts/tools/review_session.py`（PyQt5 单文件）：三路同步视图 + 运动量曲线点击跳转 + IN/OUT 裁剪 + approved/rejected 状态 + 批量队列模式，已实现。
- 非破坏式：人工决策只写 `<session>/annotations.json`（schema 见设计文档 2.1 节），原始数据不动。
- `build_srth_dataset.py` 已集成：自动检测 annotations.json，按 trim 过滤网格行、跳过 rejected session、trim 信息记入 episode `meta.json`。
- 逻辑验收已通过（trim [3000,15000] → 15116→10160 行、episode 10100 样本；rejected 跳过正常；GUI 冒烟通过）。

**剩余（需桌面操作）**：在 `sessions/20260817_124143` 上完整评审一遍（打 IN/OUT → 保存 → 重进状态恢复 → 4x 快放流畅），汇报工具截图/录屏说明、验收结果、annotations.json 实例。

> 用法：`python scripts/tools/review_session.py --session sessions/20260817_124143`（支持任意 cwd，`--sessions sessions/` 批量模式）。

### T11. 腕部相机成像质量修复 ✅（2026-08-18 完成，详见归档）

- worker 手动曝光/白平衡/画质参数支持 + `camera_param_tuner.py`；**定稿启用 AWB（`auto_wb: 1`）**，手动 5000/4300K 作 fallback；20 s 目检 session `test_awb_20260818` 用户接受。
- 正式采集照明下如需进一步压 R-clip，可配合补光 + 微调曝光（非阻塞）。

背景与诊断（PM 已确认，2026-08-18）：session `20260818_083049` 实际帧对比显示 arm_1/arm_3（480p USB 腕部相机）存在三个叠加问题：① 周边大面积**青蓝色偏色**——白平衡按暖色光源（~3200K）设定，而主照明是内窥镜冷白 LED（6000K+）；② 内窥镜直射区**橙红过曝**、红通道 clipping；③ 四角暗角（物理镜头特性，不处理）。偏色在 session 首尾完全一致，说明参数固定/收敛在错误值上，非漂移问题。arm_2 内窥镜自带 ISP 校准，无此问题。

任务内容（按顺序）：

1. **相机 worker 增加手动参数支持**：在 `usb_camera_worker.py` / 相机源配置中加入手动曝光与白平衡设置（`CAP_PROP_EXPOSURE`、`CAP_PROP_WB_TEMPERATURE` 或 `CAP_PROP_WHITE_BALANCE_BLUE_U/RED_V`），参数进 `default.yaml` 的 per-camera 配置。注意 MSMF 与 DSHOW 支持的属性集不同，需逐个实测并记录哪些属性真正生效（打印 set 后 get 的回读值）。`scripts/acquisition/` 核心代码改动需先提交方案给 PM 确认；worker/config 层改动可直接做。
2. **白纸标定流程（现场）**：用户会放置一张**白纸**（替代灰卡）。在实际工作照明下（内窥镜光源开到工作档位、器械就位）：对 arm_1/arm_3 分别调整白平衡至白纸呈中性白色后**锁定**；调整曝光至内窥镜直射区红通道不 clipping（检查直方图，R 通道峰值 < 250）后**锁定**。
3. **补光（用户侧配合）**：建议加一盏带柔光的 LED 补光灯打亮周边，压缩场景动态范围并统一色温；避免混用不同色温光源。
4. **验证采集**：调定后采一条 30 秒 session（含器械运动），用 T10 的 `review_session.py` 或抽帧检查确认验收线（见下）。

验收线：

- 白纸区域在各相机画面中呈中性（R/G/B 通道均值相互偏差 < 10%）；
- 内窥镜直射区红通道无 clipping（帧级 R 通道 255 像素占比 < 0.1%）；
- 连续 30 秒帧间亮度/色温稳定（整帧亮度均值波动 < 5%，确认自动曝光/白平衡已真正锁定、无帧间闪烁）；
- 锁定参数与回读值记录到 `default.yaml` 与备忘文档。

兜底方案（仅当驱动完全不吃手动属性时启用）：标定流程中加拍一次白纸/色卡，算逐通道增益，在 `build_srth_dataset.py` 前做离线颜色校正。**已 clipping 的区域离线救不回来，优先争取采集时解决。**

汇报：生效属性清单（含回读值）、调定后的参数值、验证 session 的抽帧对比图与帧间稳定性数据。若驱动不支持手动控制，停下来汇报，转入兜底方案评估。

### T12. 采集框架健壮性与存储优化 ✅（2026-08-19 完成，与 T13 合并回归）

- **P1**：① 相机死亡 fail-fast（worker 读帧失败非零退出；`_wait_for_ready` 超时/早退 raise；`session.json` 记 `worker_exit_codes`）；② TwinCAT CSV 瘦身（常量→`robot_pose/meta.json` sidecar，行内留逐样本字段；下游 `verify/align/analyze/calibrate_drift` 列名核对通过）；③ `orientation_order` 字符串修正。
- **P2**：重复 `--topic` 移除；legacy deprecation warning + `usb_multi_camera` 校验分支删除；分层泄漏收口（`main_thread_sources`/`attach_session`/`run_in_main_thread`）；`import csv` 移顶、CSV 统一 `utf-8-sig`。
- **P3**：`session.py` docstring 目录布局、PNG 注释更新。
- **回归**：30 s 采集 `20260819_051024` 三路 30.0 fps、TwinCAT 43392 样本、verify/align/build 全通；`worker_exit_codes={}`。commit e57b91f。
- **遗留**：可观测性增强（worker 周期统计）可选未做。

### T13. 仓库布局迁移：src 包 + 按功能分包 ✅（2026-08-19 完成，与 T12 合并回归）

- `pyproject.toml`（`pip install -e .` → `import vlgp.*`），删除全部 sys.path hack。
- `src/vlgp/{acquisition,alignment,dataset,review,calibration,analysis,capture}`；`constants.py` 集中魔法数字；路径常量收口 `session_layout`。
- worker 子进程 `-m vlgp.acquisition.process_workers...`（无手工 PYTHONPATH）。
- unify_alignment / analyze_alignment 移入 `archive/`（被锚点方案取代）。
- bat 启动器改 `%~dp0` 相对路径 + conda python fallback；README/AGENTS.md 引用更新。
- **迁移后调用**：`python -m vlgp.alignment.align_camera_anchor --session ...` 等；`scripts/run_acquisition.py` 为采集入口。
- **验收**：任意 cwd `import vlgp.acquisition` ✅；全链路回归（采集→verify→align→build）✅。commit e57b91f。

### T15. 片段级任务层级与指令标注（SRT-H 数据语义层，新立项 2026-08-20）

> **架构决策（2026-08-23 PM 定稿）：本任务及 T10 的裁剪/评审功能统一以 Web 架构实现**——FastAPI 后端（直接复用 `vlgp` 包：session_layout/metadata/annotations 读写 + JPEG 预览帧服务）+ 静态 JS 前端（canvas 按帧索引渲染 + 预取滑动窗口；**不用 `<video>` seek**，帧精确性不可靠）。理由：① T15 的 UI（片段时间轴、层级词表级联选择、指令表单）是 Web 组件生态的强项；② 浏览器即客户端，标注工作可分发到多人/多机；③ 与 PyQt5 版共用同一份 `annotations.json` schema，裁剪与标注互不冲突。**PyQt5 版 `review_session.py` 在 Web 版达到功能对齐前继续可用，不删除。**
>
> 实施顺序：**v0＝Web 平台骨架 + T10 裁剪功能迁移**（会话浏览、三路同步帧视图、IN/OUT 裁剪、approved/rejected、批量队列——与 PyQt5 版功能逐条对齐并回归 `annotations.json` 兼容）；**v1**＝片段划分 + 单层 task + 指令文本 + 构建集成；**v2（里程碑后）**＝层级树 + corrective 联动 + H policy 训练对导出。

背景：一条 session 通常包含**多个 task / subtask（甚至 subsubtask）**——我们采用操作分级制度。SRT-H 的分层结构里，H policy 输出**任务级语言指令**（如「抓起缝针」「递线」），L policy 把指令执行为关节级动作；因此训练数据必须带「每个片段是什么操作 + 对应指令」的语义层。当前 `review_session.py` 只解决「留哪段」（整段 IN/OUT 裁剪 + approved/rejected），无法表达片段级语义，数据缺语言条件层，高层策略无从训练。

预期实现效果：

1. **片段划分**（review_session.py 扩展）：在 trim 后的有效区间内，人工划分若干**连续、不重叠、全覆盖**的片段（播放中打 segment 边界，或从运动量曲线辅助定位）。每个片段挂：
   - **层级路径**：task / subtask（/ subsubtask），从**受控词表**选择（操作分级制度固化为配置文件，如 `scripts/config/task_hierarchy.yaml`，支持后续扩充）；
   - **语言指令**：该片段对应的指令文本——即 SRT-H 中 H policy 输出的指令形式，作为低层样本的语言条件；
   - （v2）**corrective 标记**：该片段是否为纠错动作，与备忘 13.3.5 节的纠正指令生成联动。
2. **非破坏式存储**：片段标注并入 `<session>/annotations.json`（schema 扩展 `segments: [{start_row, end_row, path, instruction, corrective?}]`，行号语义与 trim 一致=对齐表 0-based 行号；**向后兼容**仅含 trim/status 的旧标注）。
3. **构建集成**（build_srth_dataset.py）：按片段边界标记样本——每个 chunk 可查询所属片段的层级路径与指令；episode 的语言条件由片段指令生成（替代当前单一 `task_instruction` 透传），跨片段边界的 chunk 按起始步归属或剔除（实现时定稿并记录）。
4. **高层训练对预留**：片段边界 + 指令天然构成 H policy 训练对（片段首帧图像 + 状态 → 指令），在 meta 中保留片段索引以便后续导出。

分期：见上方架构决策（v0 平台与 T10 迁移 → v1 片段标注 → v2 层级/纠错）。

验收：v0——Web 版裁剪在 `sessions/test_20260818_215014` 上与 PyQt5 版产出**逐字节一致**的 annotations.json（同操作序列）；v1——在一条含 ≥2 个操作阶段的演示 session 上完成标注 → 重建数据集 → 抽检 chunk 的语言条件与片段归属正确；仅含旧 schema（trim/status）的 session 构建无回归。

依赖与前置：
- **受控词表先行**：任务分级制度（有哪些 task/subtask、命名规范）由 PM 与操作者先定义一版草案（写进 `task_hierarchy.yaml` + 备忘），GUI 按词表渲染选择器，不允许自由文本，保证标注一致性。
- **占位符版词表已落地（2026-08-26）**：`scripts/config/task_hierarchy.yaml`（v0.1，`status: placeholder`，task=触针/抓取/缝合/待机 × subtask + 指令模板，含 schema 契约注释与定稿 TODO 清单）；配套框架模块 `vlgp/annotations/task_hierarchy.py`（加载/结构校验/路径与片段校验/自检 CLI `python -m vlgp.annotations.task_hierarchy`，自检全 PASS）。**词条语义待 PM/操作者定稿后升 `status: draft/finalized`（YAML 内 notes 列了 5 项待定事项）**；框架可在占位词表上先行搭建。
- **Web 平台后端骨架已落地（2026-08-26，commit 19acf55 + 0e9d0bf）**：`src/vlgp/web/`（FastAPI）——`/api/sessions`（列表/详情）、`/api/sessions/{name}/alignment`（运动量曲线，`?stride=`）、`/api/sessions/{name}/frames/{cam}/{row}?max_width=`（JPEG 预览，缓存）+ `POST .../frames/batch`（base64 预取窗）、`GET/PUT .../annotations`（读/校验/原子写，校验含 trim 越界与 T15 片段词表校验）、`/api/task-hierarchy`、`/api/review/queue` + `/api/review/manifest`（批量评审队列）；静态前端 `web/static/`（会话列表 + 三路 canvas 帧视图 + 运动量条点击跳转 + 播放/逐帧 + IN/OUT + approved/rejected/pending + 保存 + 下一个未评审（N 键）+ J/K/L 快进快退，快捷键同 PyQt5 版）；启动 `python -m vlgp.web.app`（默认 127.0.0.1:8765）或 `start_web_platform.bat`。
- **v0 对齐完成项（2026-08-26）**：① **annotations.json 与 PyQt5 版逐字节一致**——PUT 时服务端按 PyQt5 同格式补 `reviewed_by`（USERNAME）与 `reviewed_at`（本地时区 ISO 秒级），manifest 更新同构；实测 262 字节完全一致 ✅；② 评审队列/清单：`/api/review/queue`（manifest status 非 approved/rejected 即待审，同 PyQt5 `review_manifest.json` 语义）+ 前端「下一个」（N 键）；③ J/K/L 快进快退对齐；④ **播放速度与运动量曲线打磨**（2026-08-26）：速度选择器 0.5x/1x/2x/4x + **基于时间的行推进**（`dt×speed×30`，同 PyQt5 `_tick`），J/K/L 跳 `max(1,speed×5)` 帧；曲线**逐线各自最大值归一**（同 PyQt5 `_redraw_curve` 口径）+ 图例 + IN/OUT 虚线标记 + 白色当前行标记；`/alignment` 增 `points` 参数（按像素预算自适应降采样，长 session 轻量；NaN 行已清洗）。
- **✅ v0 整体回归通过（2026-08-26，`scripts/test/verification/web_regression.py`，37/37 PASS）**：A 会话发现/详情（含 404）→ B 运动量数据（points/stride/NaN 清洗）→ C 帧服务（单帧 JPEG 魔数、超小图、越界 404、batch、空 rows）→ D annotations 往返 + 7 类 422 校验 + **approved/rejected/pending 与含 segments 四种场景逐字节一致** → E 评审队列顺序流程（评审→队列收缩→全审→队列空；manifest 逐字节一致）→ F task-hierarchy/index/openapi。回归同时修复两处：① `review_queue` 只收**含对齐表**会话（对齐 PyQt5 `collect_sessions`，否则无对齐表会话永远滞留队列）；② **segments 行号越界校验**（此前只查 trim 不查 segments）。测试产物自动还原。**v0 剩余**：无（功能基线已对齐；GUI 实机浏览体验建议后续人工过一遍）。
- **Web 平台自动对齐生成（2026-08-26→08-27 迭代，用户需求）**：选中**未生成对齐表**的 session 时自动生成——`POST /api/sessions/{name}/align`（`vlgp/web/align_service.py`，复用 CLI 同款 `build_camera_anchor_alignment`，**与 CLI 产出逐字节一致**（实测 932 行））；**异步执行 + 进度**：POST 立即返回 `started`（后台线程），前端轮询 `GET /api/sessions/{name}/align/status` 显示**进度条**（阶段 加载机器人数据/拟合漂移模型/加载相机元数据/预计算插值数据/生成 30Hz 锚点网格 + 当前/总数百分比；`build_camera_anchor_alignment` 新增可选 `progress_cb`，CLI 行为不变）；幂等（已对齐返回 `already_aligned`）；无机器人数据同步返回 422（中文提示「PLC/ADS 链路可能未就绪」）；列表「未对齐」徽标。
- **机器人状态面板（2026-08-27，用户需求）**：新增 `GET /api/sessions/{name}/row/{row}`（当前对齐行的**四臂**姿态快照：位置/镜系位置（arm2=内窥镜=原点）/四元数 wxyz/夹爪角度/本步位移/器械名称（复用 `INSTRUMENT_NAME`）/能量状态，NaN 行→null）；前端新增**四臂切换 Tab**（arm1..arm4）+ 状态面板（欧拉角由四元数前端换算 ZYX），行切换时防抖（150ms）拉取。修复：自动对齐 422 错误消息提取（`detail.error` 嵌套层级）；`build_camera_anchor_alignment` 进度改造曾误用 `enumerate(iterrows())` 导致 `frame_row` 变元组（测试即捕获），已恢复原解包。回归新增 H 节（align-status/行状态 4 臂+器械/越界 404），**43/43 PASS**。
- **依赖注记（已解除）**：本机 Smart App Control 曾按信誉阻止 conda-forge 的 `pyexpat.pyd`（`应用程序控制策略已阻止此文件`）导致 env 内 pip 不可用；**用户已关闭 SAC（VerifiedAndReputablePolicyState=0）**，pyexpat/pip 恢复正常，fastapi/uvicorn 已用 pip 正式重装（`pip check` 通过）。
- ~~建议在 T10-评审之后实施~~（2026-08-23 修订：T15 改为 Web 平台承载 T10 功能，不再依赖 PyQt5 版评审完成；T10-评审仍建议尽早做，其验收结论直接作为 v0 迁移的功能基线）。

### T16. 基于 MDH 的运动学验证与基座系→镜系变换解算（2026-08-21 模块实现；2026-08-21 delta_rot_6d 改造完成；FK/Kabsch 数据验证待单关节运动）

**已完成（commit 35351c3 + 本批，`src/vlgp/kinematics/` + `build_srth_dataset.py`）**：
- **MDH FK**：4 臂 FK 实现（13 链节 j3..jaw，Modified DH，从 `tech_repo/MDH参数表.md` 转写），自检 12/12 PASS。
- **Kabsch 基座系→镜系**：`transform.py` 单 session 解算 + 多 session 交叉验证（验收线旋转<1°/平移<5mm/RMS 报告）；合成数据自检通过。
- **`delta_rot_6d` 局部系改造 ✅（PM 2026-08-23 批准）**：改为 `R_rel = R(q_i)^T @ R(q_{i+1})`（`q_i⁻¹⊗q_{i+1}`，SRT hybrid 约定）；自检 7/7 PASS（新增判别性测试）；静止段回归 median 旋转角 0.000 mdeg（尾部 ~0.05° 为 PLC 四元数量化 ~1e-4 噪声）；新旧约定共轭关系 `R0^T·old·R0=new` 偏差 7.77e-16；重建 episode 的 delta_rot_6d ≈ 单位阵。T9 遗留风险消解，`R_bs` 降级为增强项。
- **真实数据验证（sessions/20260821_123455，首个 ADS 有效会话）**：新增 `vlgp/kinematics/validate.py`（`validate_kinematics.bat`），报告见 `tech_repo/T16_FK与PLC对标验证_20260821.md`：
  - 通过：四元数单位范数、关节/位置/scope 稳定性、arm2=内窥镜（scope 原点）。
  - **FK vs PLC：全部映射假设失败**（4 种行映射 + 6144 符号组合 0 命中，位置误差 0.7~1.5m）——CSV `Joint_Angles` 与 MDH 关节值非简单"表内 theta 偏移+符号"关系，静态单点无法解析顺序/零位/符号。
  - **Kabsch 单一刚体变换不成立**：R_bs 残差 RMS 68.95mm（逐臂 34~83mm）——各臂 `ArmFrame` 疑为各自安装基座（MDH joint3 theta=0/60/120/180 即安装旋转），或 PLC scope 帧换算有逐臂细节。
  - **下一步（需机器人工程师，与 T5/T6 同批）**：单关节运动实验协议见备忘第四节（每次只动 1 关节、已知增量 ±10°/±10mm、每位置保持 2~3s、≥30s 会话），重跑 `validate_kinematics.bat` 解析映射/符号/零位；同时为 Kabsch 提供 ≥2 位姿逐臂求解。已列 4 个待工程师确认问题（ArmFrame 坐标系定义、Joint_Angles 是否绝对/已补偿、PLC FK 约定、theta 偏移语义）。

> **PM 决策（2026-08-23）：批准 `delta_rot_6d` 改造为 SRT 局部系约定**（`R_delta = R_t^T · R_{t+1}`，即 `q_t⁻¹ ⊗ q_{t+1}`，delta 旋转表达在 t 时刻末端系）。理由：① 与复现目标 SRT 的 hybrid relative actions 严格对齐；② 该约定对基座/镜系的常值旋转**不变**，T9 遗留风险随之彻底消解，`R_bs` 不再是正确性前提；③ 改动局部（差分处换乘序），回归成本低。要求：改造后跑数值回归（静止段 delta_rot≈0、与改造前对比差异应为常值旋转共轭关系），单元自检同步更新。R_bs 解算保留为交叉验证/增强项，不阻塞。

背景：工程师提供了四条臂的 MDH 参数表（`tech_repo/MDH参数表.md`， joints 3–15 + jaw 固定段，长度 mm、角度 deg，`theta` 常值项为关节零位偏置）。这带来两条不依赖现场标定的数据侧路线：

任务内容：

1. **MDH 正运动学（FK）实现与验证**：新建 `src/vlgp/kinematics/` 模块，按 MDH 实现逐臂 FK；用已录 session（如 `test_20260818_215014`）的 `joint_1..12` 复算末端位姿，与 PLC 输出的 `position_*`（基座系）与 `orientation_*` 对比，出误差报告（位置 mm / 姿态 °）。**此步同时验证：MDH 表正确性、关节编号 3–15 ↔ CSV `joint_1..12` 的映射、零位偏置与符号约定**。映射/零位若对不上，先数值反推（网格搜索偏置）再回报工程师确认，不臆断。
2. **基座系→镜系刚体变换离线解算**：PLC 同时输出 `position_*`（基座系）与 `scope_position_*`（镜系），内窥镜固定时两者相差常值变换 `R_bs, t_bs`。取若干条臂部运动充分的 session，对成对位置点集做 Kabsch/Umeyama 配准解算，报告拟合残差（RMS mm）；**多 session 交叉验证一致性**（解出的变换应一致，不一致说明镜系随内窥镜运动，停下来汇报）。
3. **构建集成**（验证通过后）：`build_srth_dataset.py` 增加选项——姿态四元数先经 `R_bs` 从基座系转到镜系，再算 `delta_rot_6d`（消除 T9 遗留的常值旋转误差）；变换参数与验证报告落档（`calibrations/` 或配置，注明来源 session 与日期）。

验收：① FK 复算与 PLC 输出误差在合理量级（位置 < 几 mm 或明确归因于关节间隙/零位），报告含每关节映射结论；② `R_bs/t_bs` 在 ≥2 条不同 session 上解算一致（旋转差 < 1°、平移差 < 5 mm），Kabsch 残差 RMS 报告；③ 集成后 `delta_rot_6d` 数值合理性抽检（与改造前对比，差异应为常值旋转）。

注意：MDH 单位与坐标轴定义（mm/deg、Modified DH 的变换顺序 `Rx(alpha)·Tx(a)·Rz(theta)·Tz(d)`）以实现时与 PLC 输出对齐为准；`alpha` 列出现 16°、-50°/-60° 等非直角落差是结构安装角，照表使用不要「修正」。

> **SRT 论文立场调研（PM，2026-08-21）**：SRT（2407.12998）全文**无任何标定环节**（"calibration" 零出现），且明确假设数据**未做手眼标定**采集（对齐临床大规模数据场景），改用 relative action 对不准的绝对运动学免疫：delta 平移定义在**内窥镜末端系**、delta 旋转定义在**当前末端执行器系**（"hybrid relative actions"）。推论：① 本任务（R_bs 解算）是**数据质量增强项而非对齐 SRT 的必要步骤**，优先级下调，不阻塞 T5/T6 与正式采集；② **优先核对项**：`build_srth_dataset.py` 的 `delta_rot_6d` 若算的是局部系相对旋转（`q_t⁻¹ ⊗ q_{t+1}`，即 SRT 的末端系约定），则与基座/镜系常值旋转无关，T9 遗留风险直接消解——实施 T16 前先核对此约定并与 SRT hybrid 形式对齐。

### T17. 数据集格式对齐 LeRobotDataset v3.0（新立项 2026-08-26，PM 决策）✅ 2026-08-27

背景与决策：数据集对外/训练格式采用 **LeRobotDataset v3.0** 规范（lerobot v0.6.x 文档线）。定位：**在 `build_srth_dataset.py` 之后加一层导出器**（session → 内部 episode → LeRobot v3 数据集），不重写采集与对齐管线；SRT-H 内部 episode 格式保留为中间层（标注/裁剪语义都在这层流动）。

v3.0 格式要点（2026-08-26 从 HF 官方文档 `lerobot-dataset-v3` 核对；**2026-08-27 以安装包源码核定，见下方实现记录**）：

- **文件式存储**（v2 是每 episode 一个文件）：`data/` 为逐帧 **Parquet 分片**（一个文件含多个 episode）；`videos/` 为每相机 **MP4 分片**（同样多 episode 合一）；
- **关系型元数据**：`meta/info.json`（schema：features/shapes/dtypes、fps、codebase 版本、路径模板）、`meta/stats.json`（全局 mean/std/min/max，归一化用）、`meta/tasks.jsonl`（自然语言任务 ↔ 整数 ID）、`meta/episodes/`（每 episode 的长度/任务/偏移，分块 Parquet）；
- **加载侧**：`LeRobotDataset` 支持 `delta_timestamps` 时间窗采样、`StreamingLeRobotDataset` 免下载流式训练；图像增广在训练时做，**落盘图像保持原始**（与我们无损采集策略一致）；
- 另有官方文档 `porting_datasets_v3`（v2→v3 移植脚本可参考其分片/元数据写法）。

与我们字段的映射（设计起点，以实现时核定为准）：`observation.state` ← 四臂关节/末端位姿/gripper/instrument_type；`action` ← 现有 40 维 delta 动作（delta_pos + delta_rot_6d 局部系 + jaw + arm_active_mask）；`observation.images.camera_arm_{1,2,3}` ← 三路相机帧；`task`/`task_index` ← **T15 的片段指令**（天然对应 tasks.jsonl 的语言↔ID 映射）；`timestamp`/`frame_index`/`episode_index` 按规范生成；`stats.json` 在导出时全量统计。

任务内容：

1. **契约核定（第一步）**：pin lerobot 版本（v0.6.x 线），`pip install lerobot` 后直接读 `lerobot.datasets` 源码与 `porting_datasets_v3` 文档确定精确 schema（本机访问 HF 网页不稳，**以安装包源码为准**，不要用记忆中的格式臆造）；记录版本号与 schema 摘要到备忘。
2. **导出器实现**：新增 `src/vlgp/dataset/export_lerobot.py`（或并入 build 流程的可选步骤）：内部 episode 目录 → LeRobot v3 目录树（parquet 分片 + mp4 分片 + meta 全套）；视频分片编码参数（编码器/CRF/GOP）参考官方文档的 video encoding 约定，**原始 PNG 序列保留不落**（mp4 有损，PNG 母版仍是源数据）。
3. **往返验证**：用 `LeRobotDataset` 重新加载导出结果，校验帧数/时间戳/逐字段值与源 episode 一致（含三路图像抽帧像素级比对）；`delta_timestamps` 窗口采样冒烟。
4. **多 episode 分片验证**：≥3 条 episode 合并导出，验证「多 episode 合一文件」的分片与 episodes 元数据正确。

验收：导出的数据集被未修改的官方 `LeRobotDataset` 零警告加载；往返数值一致；一条完整 session（经 T10 裁剪 + T15 标注）端到端导出成功。

**实现记录（2026-08-27，全部完成 ✅）**：

1. **契约核定**：pin `lerobot==0.4.4`（PyPI 最新；PM 注记的 "v0.6.x" 是 HF 文档线编号，PyPI 与源码一致为 0.4.4）；直接读 `site-packages/lerobot/datasets/` 源码核定 schema——v3.0 布局为 `data/chunk-*/file-*.parquet`（多 episode 合一）、`videos/<cam>/chunk-*/file-*.mp4`、`meta/info.json + stats.json + **tasks.parquet**（非 tasks.jsonl，v2 遗留）+ meta/episodes/chunk-*/file-*.parquet`；官方写入 API：`LeRobotDataset.create(..., vcodec=)` → `add_frame(frame)` → `save_episode(parallel_encoding=)` → `meta.save_episode_tasks([...])` → `finalize()`；`validate_frame` 要求 frame 恰好含非 DEFAULT 特征 + `task`，`timestamp`/`frame_index`/`episode_index`/`task_index` 由库内部生成（`timestamp=frame_index/fps`，即 `delta_timestamps` 要求的 1/fps 时间轴）；视频编码器仅接受 lerobot 名（`h264`/`hevc`/`libsvtav1`/`auto`…，`libx264` 非法）；解码侧 `decode_video_frames_torchvision` 返回 **CHW float32 [0,1]**。
2. **导出器实现**：`src/vlgp/dataset/export_lerobot.py`——`observation.state`（float32[32]，4 臂基座系 pos3+quat4+gripper）、`observation.instrument_type`（int64[4]）、`observation.active_mask`（bool[4]）、`action`（float32[40]，SRT-H 40 维 delta）、`observation.images.camera_arm_{2,1,3}`（视频，endoscope 1280×720 + 腕部 640×480）；`--repeat` 多 episode、`--vcodec h264|libsvtav1|...`、`--parallel` 可选；`--out` 已存在即拒绝（exit 2）；帧源优先 `obs/<stream>/`，否则回退源 session 相机目录（`meta['source_session']` 为 repo 相对路径）。
3. **往返验证**：`scripts/test/verification/lerobot_roundtrip.py`（5 节 12 项）——官方类零警告加载、帧数/数值精确一致（state/action max|diff|=0）、时间戳 1/fps 轴、三路图像 MP4 解码 vs 源 PNG（修复后 worst mean|diff| 2.35~3.28，纯有损量化噪声）、`delta_timestamps` 窗口采样、≥3 episode 分片与 episodes 元数据。**12/12 PASS**。
4. **修复的两个实现 bug**（测试即捕获）：① 测试侧把解码帧当 HWC uint8 比较，实际解码为 **CHW float32 [0,1]**（形状误判 → 误报）；② 导出器把 `cv2.imread` 的 **BGR** 数组直接入帧，而 lerobot 视频管线全程 RGB（临时 PNG 字节直存 + `convert("RGB")`）→ 视频 R/B 互换（修复前 worst mean|diff| 15.96~23.23，修复后 <3.3）。导出前 `cv2.cvtColor(COLOR_BGR2RGB)`。
5. **验证产物**：`datasets/srth_demo_20260821`（session `20260821_123455` 的 srth_episode ×3，677 帧/条，h264，2031 帧）。注：视频编码/解码与数据集文件读写需完整权限（sandbox danger-full-access），导出+验证须在同一权限上下文执行（否则产物不可读）。

注意：T15 的片段指令（v1 落地前）先用 session 级 `task_instruction` 占位映射 tasks.parquet（导出时 `--task` 覆盖），T15 完成后切换为片段级指令。

### 里程碑达成后立即排期（非阻塞，先立项不实施）

- **纠正指令自动生成**：按备忘 13.3.5 节方案（分析未来 10 帧动作块主导运动方向）实现 `corrective_instruction` / `correction_flag` 的离线生成。
- **标注工具**：参考仓库有 PyQt5 标注 GUI（phase/event/contact），评估自建最小标注工具或借用其 `data_annotation/` 模块。

## 4. 汇报与记录规范

- 每完成一项任务：在备忘文档 **第 0 节更新记录表**追加一行（日期 + 一句话结论），详细数据写入对应章节。
- 同步更新根目录 `SESSION_CHECKPOINT.md` 的「最近完成的工作」与「待办」。
- git 提交遵循仓库现有 conventional commits 风格（`feat(...)` / `fix(...)` / `docs(...)`）。
- 遇到以下情况**停下来汇报而不是绕过**：硬件指标不达标、数据异常（坐标系/量纲可疑）、采集进程不稳定、需要修改采集框架核心代码（`scripts/acquisition/`）。

## 5. 明确不做的事（本阶段范围外）

- PTP/EL6688 时间同步升级（事后漂移模型已够用）。
- DAgger 纠正采集 GUI（属于初始模型训练之后）。
- 存储格式迁移调研（HDF5/zarr 之外）。
- `scripts/acquisition/sources/legacy/` 与各 `archive/` 目录：只读，不改动。
