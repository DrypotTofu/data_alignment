# AGENT 任务交接：开始采集标注/训练数据前的就绪工作

> 创建日期：2026-08-16
> 角色分工：项目管理 Agent 负责规划与验收；本文档面向执行 Agent，覆盖具体操作、测试与汇报要求。
> 阶段性目标（里程碑）：**达到「可以开始正式采集用于标注和模型训练的数据」的状态**。

---

## 1. 背景与必读材料

开始前请按顺序阅读：

1. 根目录 `README.md`：环境安装、采集/对齐快速开始命令。
2. `tech_repo/VLGP_项目状态与技术备忘_20260803.md`：重点是 **第 0 节（更新记录）、3.1 节（相机帧率特性）、9 节（待办）、13.3/13.5 节（SRT-H 数据构建方案）**。
3. `scripts/AGENTS.md`：开发约定。

环境：conda 环境 `SurgDataColl`（`C:\Users\11764\.conda\envs\SurgDataColl\python.exe`），Windows + PowerShell。

## 2. 里程碑验收标准（Definition of Done）

以下全部满足，里程碑即达成：

- 三路相机 + TwinCAT 四臂在 `default.yaml` 下达到精确 30 Hz（每路 >= 29.9 fps），30 分钟连续采集无掉线、无写盘堆积。
- 一条规范演示 episode 完整跑通：采集 -> `verify_alignment.py` -> `align_camera_anchor.py` -> `build_srth_dataset.py` -> `images_to_video.py` 目检。
- 产出的数据集样本通过数值合理性检查（动作幅度、符号、坐标系正确；对齐误差达标）。
- 所有结果已写入备忘文档并提交 git。

## 3. 任务清单（按依赖顺序执行）

### T1. gripper_angle 量程与方向反推（纯离线，立即可做）

- 扫描 `sessions/` 下已有 session 的 TwinCAT CSV，统计四臂 `gripper_angle` 的最小/最大值与典型分布。
- 结合夹钳信号列判断：角度增大对应张开还是闭合。
- 汇报：每臂 min/max/常见工作区间、张开方向结论、建议的归一化映射（到 [0,1] 或 [-1,1]），写入备忘 13.5 节补充。

### T2. 实现 scripts/tools/build_srth_dataset.py（核心开发任务，1-2 天）

按备忘 13.5 节（含 2026-08-16 补充）与 13.3.3 节实现，要点：

- 输入：`align_camera_anchor.py` 产出的 30 Hz 对齐表 + session 原始数据；输出 13.5 节定义的 episode 结构。
- **四元数 wxyz -> xyzw 重排**后交给 scipy；脚本内置单元自检（恒等旋转、已知 90 度旋转的 SLERP/相对旋转结果）。
- 每臂动作：`delta_pos`（**镜坐标系**位姿差分）+ `delta_rot_6d`（相对旋转矩阵前两列）+ `jaw`（按 T1 结论归一化），四臂 40 维 + `arm_active_mask`；每样本存未来 60 步 action chunk；序列末尾不足 60 步的处理策略需在代码注释与文档中说明。
- HL policy 样本：内窥镜流按 1 s 间隔取 5 帧。
- `language.json` 先放占位结构（task_instruction / corrective_instruction / correction_flag），`meta.json` 含对齐误差统计与基坐标系位姿。
- 离线验证：在 `sessions/test_binding_alignment_20260814_170005` 上跑通全流程。
- 汇报：样本数与维度、各动作分量的数值范围/直方图、被 15 ms 阈值过滤的帧比例、对齐误差统计（中位/p99）、单元自检结果。若动作数值出现异常（量纲不符、符号翻转），停下来汇报，不要自行"修正"坐标系。

### T3. 10-30 分钟长时间稳定性测试（高优先级，需要硬件在位）

- 用 `default.yaml` 连续采集 >= 10 分钟（目标 30 分钟）：`python scripts/run_acquisition.py --config scripts/config/default.yaml --duration 1800`。
- 汇报：每路相机实测 fps（mean/std/最大帧间隔/丢帧数）、TwinCAT 实测频率、采集前后漂移残差 std（`calibrate_temporal_drift.py` / `verify_alignment.py`）、内存与磁盘占用趋势、是否出现 writer 堆积或进程异常。

### T4. 三路 30 Hz 全量验证（依赖：内窥镜接回或新相机到位）

- 执行 `camera_assignment_setup` 重新分配索引并写回 `default.yaml`；确认 arm_3 相机接入。
- 采一条 30 秒 session，跑 `align_camera_anchor.py`。
- 汇报：三路实测 fps（验收线 >= 29.9 fps、帧间隔 std < 6 ms）、对齐覆盖率（验收线 >= 99%）、各模态对齐误差。**当前 index 0 房景相机原生约 29.7 fps，若它仍在绑定中必须替换，不要降低验收标准。**

### T5. 镜坐标系轴向 sanity check（可与 T6 合并）

- 采一条 1 分钟测试 session：让某条臂沿已知方向做一次纯平移（如朝镜头方向约 2 cm），其余臂静止。
- 用 T2 脚本生成动作并检查该臂 `delta_pos` 的符号与轴向是否符合预期；静止臂的 delta 应接近 0。
- 汇报：测试设置（哪条臂、哪个方向、移动距离）、观测到的 delta 向量、结论。

### T6. 端到端试跑（里程碑验收）

- 采一条规范演示 episode（触针或抓针，1-3 分钟），完整跑通：采集 -> `verify_alignment.py` -> `align_camera_anchor.py` -> `build_srth_dataset.py` -> `images_to_video.py` 目检视频。
- 汇报：完整指标表（fps、对齐覆盖率与误差、样本数、动作数值范围、目检结论）、发现的问题清单。通过后由项目管理 Agent 宣告里程碑达成。

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
