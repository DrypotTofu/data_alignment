# AGENT 任务交接：开始采集标注/训练数据前的就绪工作

> 创建日期：2026-08-16；最近更新：2026-08-18（项目管理 Agent：新增 T10 数据清洗/裁剪 GUI、T11 腕部相机成像质量修复；修正 conda 环境路径；帧间隔 std 验收线调整见 T4 节决策注记；已完成任务保持归档，无需执行 Agent 再处理）。
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
- **T7 工具与规范 ✅（2026-08-17，现场采集待做）**：`scripts/tools/collect_calibration.py` 已就绪（示例 `calibrations/calib_example_synthetic/`）；规范见 `VLGP_标定与元数据规范_20260817.md`。剩余：真实标定板现场采集（见下方 T7-现场）。
- **T8 工具与规范 ✅（2026-08-17，集成待做）**：`scripts/tools/write_session_meta.py` 已就绪；元数据模板见规范文档第 5 节。PM 已确认的集成方案见下方 T8-集成。
- **T9 沟通文档 ✅（2026-08-17，待答复）**：`tech_repo/T9_PLC四元数坐标系确认_20260817.md` 已备好，待发机器人工程师并回收结论（见下方 T9-跟进）。

### T4. 三路 30 Hz 全量验证（下一步，硬件已就绪）

- 执行 `camera_assignment_setup` 确认绑定（arm_1=1、arm_2=0、arm_3=3）并核对 `default.yaml`。
- 采一条 30 秒 session，跑 `align_camera_anchor.py`。
- 汇报：三路实测 fps（验收线 >= 29.9 fps）、**丢帧数（验收线 = 0，核查日志中的 `Writer queue full, dropping frame` 警告）**、帧间隔 p99（验收线 < 66.7 ms，即不超过 2 个帧周期）、对齐覆盖率（验收线 >= 99%）、各模态对齐误差（p99 < 15 ms）。

> **PM 决策（2026-08-18）：帧间隔 std 不再作为验收线。** 背景：arm_2 720p PNG L1 写盘带来 ~10 ms 级帧间隔 std（T3 已见 9.4 ms）。代码确认时间戳在 `cap.read()` 返回时即打（`usb_camera_worker.py` 134-137 行），PNG 编码/写盘在独立 writer 线程且队列缓冲约 2 秒，抖动不进时间戳；对齐管线全程使用真实时间戳（锚点网格 + 最近帧匹配 + 位姿按真实时刻插值），帧间隔不均匀已被架构消化。因此验收改为卡「丢帧数 = 0 + 帧间隔 p99 < 66.7 ms + 对齐误差/覆盖率」这组直接反映数据质量的指标。存储策略维持 PNG L1 无损（PNG0 150 GB/30 min 不可接受，JPG 有损不可用于训练数据）。

### T7-现场. 真实标定板内参采集（正式采集前必须就位，可与 T4 同日并行）

- 打印棋盘格（内角点 9×6，方格 20–25 mm）并**实测**边长；逐台相机用 `collect_calibration.py` 采 15–25 张合格帧；运行内参计算。
- 验收：各相机 RMS < 0.5 px；产出 `calibrations/calib_<ts>/`（含 `calibration.json` 与各相机 `intrinsics.yaml`）；记录标定 ID。
- 注意：对焦/位置变动后必须重标。

### T8-集成. session 元数据带入 episode（PM 已批准过渡方案）

- **已批准**：改造 `build_srth_dataset.py`，若 `<session>/session_meta.json` 存在则将其 `task_instruction` / `task_type` / `organ_model` 等字段自动带入 episode 的 `language.json` 与 `meta.json`（tools 层改动，实施后汇报数值抽样验证）。
- **暂缓**：`session.json` 增加 `camera_calibration` 字段（属 `scripts/acquisition/` 核心代码，里程碑达成前不动框架；过渡期由 `write_session_meta.py` 事后写入）。
- 汇报：改动说明 + 在一条已有 session 上填入示例 `session_meta.json` 并重建 episode，验证字段透传。

### T5+T6 合并. 端到端试跑（里程碑验收，半天）

采一条 1–3 分钟演示 session，**同一条 session 内编排三段**：

1. 某臂沿已知方向纯平移约 2 cm（如朝镜头方向），其余臂静止 → 验 `delta_pos` 符号与轴向；
2. 该臂开→闭→开 → 实测 gripper 张开方向，固化 jaw 归一化（是否启用 `jaw_invert`、per-arm 参数），写回备忘 13.5 节；
3. 正常触针或抓针演示。

然后完整跑通：采集 -> `verify_alignment.py` -> `align_camera_anchor.py` -> `build_srth_dataset.py` -> `images_to_video.py` 目检视频。同时：

- 固化 `arm_active_mask` 阈值约定（当前为 0.05 mm/步 或 0.1°/步 启发式，按真实任务数据定稿）；
- 补一条 **30 分钟**连续采集，闭合 DoD 的长稳要求（T3 的 10 分钟版本之上复验）。

汇报：完整指标表（fps、对齐覆盖率与误差、样本数、动作数值范围、gripper 方向结论、目检结论）、发现的问题清单。通过后由项目管理 Agent 宣告里程碑达成。

### T9-跟进. PLC 四元数坐标系确认（不阻塞里程碑，正式大规模采集前必须闭环）

- 将 `tech_repo/T9_PLC四元数坐标系确认_20260817.md` 发给机器人工程师，回收 Q1–Q4 答复。
- 若 Q1 = A（本就镜系）→ 遗留风险解除；若 Q1 = B 且不改 PLC → 按文档第 3 节安排固定旋转标定（并入 T7 的 hand_eye/ 预留目录）。

### T10. 数据清洗/裁剪 GUI（v1，新立项，设计已就绪）

解决采集数据开头/结尾无关片段的人工裁剪，并为后续人工标注铺路。**严格按 `tech_repo/VLGP_数据清洗与标注工具设计_20260818.md` 实施**，要点：

- 实现 `scripts/tools/review_session.py`（PyQt5 单文件）：三路同步视图 + 运动量曲线点击跳转 + IN/OUT 裁剪 + approved/rejected 状态 + 批量队列模式。
- 非破坏式：人工决策只写 `<session>/annotations.json`（schema 见设计文档 2.1 节），原始数据不动。
- 改造 `build_srth_dataset.py`：自动检测 annotations.json，按 trim 过滤网格行、跳过 rejected session、trim 信息记入 episode `meta.json`。
- v1 不做段标注编辑 UI（`segments` 仅保留结构）、不做视频转码。
- 验收：设计文档第 5 节（在 `sessions/20260817_124143` 上完整评审一遍 + episode 行数核对 + 4x 快放流畅）。

汇报：工具截图或录屏说明、验收结果、annotations.json 实例。

### T11. 腕部相机成像质量修复：手动白平衡/曝光锁定（正式采集前必须完成，可与 T10 并行）

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
