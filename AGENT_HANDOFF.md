# AGENT 任务交接：开始采集标注/训练数据前的就绪工作

> 创建日期：2026-08-16；最近更新：2026-08-17（项目管理 Agent：T1/T2 标记完成，新增 T0/T7/T8/T9 与里程碑后排期）。
> 角色分工：项目管理 Agent 负责规划与验收；本文档面向执行 Agent，覆盖具体操作、测试与汇报要求。
> 阶段性目标（里程碑）：**达到「可以开始正式采集用于标注和模型训练的数据」的状态**。

---

## 1. 背景与必读材料

开始前请按顺序阅读：

1. 根目录 `README.md`：环境安装、采集/对齐快速开始命令。
2. `tech_repo/VLGP_项目状态与技术备忘_20260816.md`：**当前阶段快照**，先看这个。
3. `tech_repo/VLGP_项目状态与技术备忘_20260803.md`：详细技术背景，重点是 **第 0 节（更新记录）、3.1 节（相机帧率特性）、13.3/13.5 节（SRT-H 数据构建方案）、第 11 节（标注规划）**。
4. `scripts/AGENTS.md`：开发约定。

环境：conda 环境 `SurgDataColl`（`C:\Users\11764\.conda\envs\SurgDataColl\python.exe`），Windows + PowerShell。

## 2. 里程碑验收标准（Definition of Done）

以下全部满足，里程碑即达成：

- 三路相机 + TwinCAT 四臂在 `default.yaml` 下达到精确 30 Hz（每路 >= 29.9 fps），30 分钟连续采集无掉线、无写盘堆积。
- 一条规范演示 episode 完整跑通：采集 -> `verify_alignment.py` -> `align_camera_anchor.py` -> `build_srth_dataset.py` -> `images_to_video.py` 目检。
- 产出的数据集样本通过数值合理性检查（动作幅度、符号、坐标系正确；对齐误差达标）。
- 所有结果已写入备忘文档并提交 git。

## 3. 任务清单

### 已完成（归档，供查阅）

- **T1 ✅（2026-08-16）**：gripper_angle 量程与方向反推。全局量程约 [-55, +85]；`gripper_signal` 恒 0，张开方向离线不可证，暂约定「角度增大 = 张开」待 T5 实测确认。详见 0803 备忘 13.5 节补充，工具 `scripts/tools/analyze_gripper.py`。
- **T2 ✅（2026-08-16）**：`scripts/tools/build_srth_dataset.py` 实现 + 离线验证（`test_binding_alignment_20260814_170005`）。单元自检 6/6 PASS；646 个 60 步 chunk 样本（40 维）；静止 session delta_pos ~0.05 mm 量级正确。详见 0816 备忘第 3 节。

### T0. 解除硬件阻塞（最高优先级，T3/T4 前置，负责人：机器人工程师 + 执行 Agent 配合）

当前整条链路唯一阻塞点。执行 Agent 负责协调与复测，不自行修改控制器系统配置之外的代码。

1. **TwinCAT RTIME 4132 修复**：控制器报「RTIME: incompatible software detected」，ADS 851 端口不可达。按 0803 备忘 13.5 节末尾的五步指引排查（杀软白名单 → 关闭内核隔离/内存完整性 → 关闭 Hyper-V（`bcdedit /set hypervisorlaunchtype off` 后重启）→ 退出占用高精度定时器的监控类软件 → TwinCAT 与 Windows 版本兼容性）。在控制器机器上操作，每步改动记录到备忘。
2. **相机侧**：DSHOW 枚举已恢复，index 0-3 均可打开，无需处理。
3. **内窥镜接回或新相机到位**：index 0 房景相机任何分辨率都只有 ~29.7 fps，**不满足验收线，必须替换，不要降低验收标准**。
4. 修复后先做 **45 秒预检**（短时采集 + `verify_alignment.py`），确认链路通再进入 T3。

汇报：修复措施与生效步骤、ADS 连通性确认、预检结果。若五步排查均无效，停下来汇报，由项目管理 Agent 升级处理（重装/修复 TwinCAT XAR）。

### T3. 5-10 分钟长时间稳定性测试（依赖 T0）

- 用 `default.yaml` 连续采集 >= 5 分钟（目标 10 分钟）：`python scripts/run_acquisition.py --config scripts/config/default.yaml --duration 1800`。
- 汇报：每路相机实测 fps（mean/std/最大帧间隔/丢帧数）、TwinCAT 实测频率、采集前后漂移残差 std（`calibrate_temporal_drift.py` / `verify_alignment.py`）、内存与磁盘占用趋势、是否出现 writer 堆积或进程异常。

### T4. 三路 30 Hz 全量验证（依赖 T0 + 内窥镜/新相机到位）

- 执行 `camera_assignment_setup` 重新分配索引并写回 `default.yaml`；确认 arm_3 相机接入。
- 采一条 30 秒 session，跑 `align_camera_anchor.py`。
- 汇报：三路实测 fps（验收线 >= 29.9 fps、帧间隔 std < 6 ms）、对齐覆盖率（验收线 >= 99%）、各模态对齐误差。

### T5. 镜坐标系轴向 sanity check（可与 T6 合并）

- 采一条 1 分钟测试 session：让某条臂沿已知方向做一次纯平移（如朝镜头方向约 2 cm），其余臂静止。
- 用 `build_srth_dataset.py` 生成动作并检查该臂 `delta_pos` 的符号与轴向是否符合预期；静止臂的 delta 应接近 0。
- **同步实测 gripper 张开方向**：命令某臂开→闭→开并观察 `gripper_angle` 符号，确认或推翻「角度增大 = 张开」的暂定约定；据此固化 jaw 归一化（是否启用 `jaw_invert`、per-arm 标定参数），写回备忘 13.5 节。
- 汇报：测试设置（哪条臂、哪个方向、移动距离）、观测到的 delta 向量、gripper 方向结论。

### T6. 端到端试跑（里程碑验收）

- 采一条规范演示 episode（触针或抓针，1-3 分钟），完整跑通：采集 -> `verify_alignment.py` -> `align_camera_anchor.py` -> `build_srth_dataset.py` -> `images_to_video.py` 目检视频。
- **同时固化 `arm_active_mask` 约定**：目前是运动阈值启发式（0.05 mm/步 或 0.1°/步），基于真实任务数据按任务约定固化阈值并写入文档。
- 汇报：完整指标表（fps、对齐覆盖率与误差、样本数、动作数值范围、目检结论）、发现的问题清单。通过后由项目管理 Agent 宣告里程碑达成。

### T7. 相机标定随 session 落盘（正式采集前必须就位，可与 T3-T6 并行）

对照参考仓库 `dvrk_multimodal_data_collection`，其每条 clip 都带 `camera_calibration/` 与 `hand_eye_calibration/`。我们目前完全没有标定文件采集步骤；内窥镜对焦/位置一旦变动，事后无法补标定。

- 设计标定板采集流程（内窥镜 + 两腕部相机的内参标定），约定标定文件的存放路径与命名。
- 每个正式 session 的 meta 中记录所用标定文件（或标定日期/版本）。
- 汇报：标定流程文档、一条示例 session 的标定记录。此任务只需要相机，不依赖 T0 的 TwinCAT 修复，可提前启动。

### T8. session 级元数据与任务指令采集规范（正式采集前必须就位，纯文档+小改动，立即可做）

`language.json` 目前是离线占位。正式采集时操作者必须**当场**记录任务信息，大规模采集后无法回溯补录。

- 定义 session 级元数据模板（task_instruction、任务类型、操作者、组织/模型编号、备注），确定写入位置（session meta 或 episode `language.json`）。
- 如需改动采集框架写入逻辑，先提交方案给项目管理 Agent 确认再动手（`scripts/acquisition/` 属核心代码，见第 4 节汇报规则）。
- 汇报：元数据模板与操作者采集流程说明（一页以内）。

### T9. 确认 PLC 四元数坐标系定义（沟通任务，可并行）

`build_srth_dataset.py` 的遗留风险：PLC 只输出镜系位置、无镜系四元数，`delta_rot_6d` 暂用基座系四元数，旋转轴差一个未标定的「基座系→镜系」常值变换（短期可接受，见 0816 备忘第 2 节）。

- 与机器人工程师确认 `EndEffector_Orientation` 是否本就定义在镜系；若不是，安排一次该固定变换的标定（可并入 T7 的手眼标定环节）。
- 汇报：工程师答复结论；如需标定，给出标定方案与所需材料。

### 里程碑达成后立即排期（非阻塞，先立项不实施）

以下工作在正式采集启动后才需要，但应提前纳入计划，避免采完数据才发现标注环节无工具无人手：

- **纠正指令自动生成**：按备忘 13.3.5 节方案（分析未来 10 帧动作块主导运动方向）实现 `corrective_instruction` / `correction_flag` 的离线生成。
- **标注工具**：参考仓库有 PyQt5 标注 GUI（phase/event/contact），评估自建最小标注工具或借用其 `data_annotation/` 模块。

## 4. 汇报与记录规范

- 每完成一项任务：在备忘文档 **第 0 节更新记录表**追加一行（日期 + 一句话结论），详细数据写入对应章节。
- 同步更新根目录 `SESSION_CHECKPOINT.md` 的「最近完成的工作」与「待办」。
- git 提交遵循仓库现有 conventional commits 风格（`feat(...)` / `fix(...)` / `docs(...)`）。
- 遇到以下情况**停下来汇报而不是绕过**：硬件指标不达标、数据异常（坐标系/量纲可疑）、采集进程不稳定、需要修改采集框架核心代码（`scripts/acquisition/`）、TwinCAT 五步排查无效。

## 5. 明确不做的事（本阶段范围外）

- PTP/EL6688 时间同步升级（事后漂移模型已够用）。
- DAgger 纠正采集 GUI（属于初始模型训练之后）。
- 存储格式迁移调研（HDF5/zarr 之外）。
- `scripts/acquisition/sources/legacy/` 与各 `archive/` 目录：只读，不改动。
