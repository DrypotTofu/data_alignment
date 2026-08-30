# VLGP 仓库代码审查报告（架构 / 内容 / 过时 / 冗余）

> 审查日期：2026-08-30 ｜ 审查基准：工作区当前快照（git HEAD 1ce9205）
> 审查方式：主代理逐文件精读核心框架（acquisition 全量 + 关键下游模块）+ 三个并行子代理分别审查
> （① alignment/dataset/review/analysis/calibration/kinematics/annotations；② capture/web/scripts/config/bat；
> ③ 文档-代码一致性），全部发现经交叉核对，未修改任何文件。
> 严重级别：🔴 高（会出错/装不上/行为错误）｜🟡 中（维护风险/一致性）｜🟢 低（清理项）

---

## 〇、总体评价

这是一个**质量明显高于一般"科研工具脚本库"**的项目：

- **分层健康**：`acquisition`（session_layout / clock_fit / registry）作为底层被下游依赖，`web` 在最上层，无循环依赖；registry+config 校验的采集框架设计清晰。
- **正面实践**：`clock_fit.LinearClockFit` 的 origin-relative 数值稳定拟合被 3 个工具复用；T12/T13 的失败可观测（worker 非零退出码、ready 超时即抛）设计到位；`.bat` 启动器全部指向存在的 `vlgp.*` 模块、GBK+CRLF 合规；archive/ 归档策略（含"被 xxx 取代"注释）规范。
- **Web 平台亮点**（T15/T17）：磁盘写穿缓存、batch-bin 二进制取帧通道、rAF 渲染/网络解耦、与 PyQt5 版 annotations.json 逐字节 parity，都是成熟做法。

主要债务集中在三类：

1. **"有设计、没接线"**：T13 声称"魔法数字集中到 constants.py"，但 `vlgp.constants` 全库 **0 处引用**；核心 schema（相机↔stream 映射、arm 列名、filename 列名）在 **5+ 个模块重复定义**。
2. **新旧方案切换清理不彻底**：`scripts/tools/*.py` 时代残留（约 20 个模块 docstring、3 个默认 config 路径、7 个 archive 测试脚本 import、`generated_by` 元数据）、被取代的功能（`pack_session`、`calibrate_temporal_drift`）未归档、消费关系未断干净。
3. **依赖清单三方割裂**：fastapi/uvicorn/lerobot 只存在于 environment.yml，按 README 的 pip 安装路径装不出 Web 平台与 LeRobot 导出器。

---

## 一、🔴 高风险问题（建议最先处理）

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| H1 | **3 个 capture 工具默认 `--config` 路径失效** | `capture/manual_acquisition.py:71`、`camera_assignment_gui.py:316`、`camera_assignment_setup.py:440` | `Path(__file__).parents[1] / 'config' / 'default.yaml'` 在 T13 迁移后解析到不存在的 `src/vlgp/config/`。不带 `--config` 直接 `python -m vlgp.capture.manual_acquisition` 立即报错（.bat 显式传路径才没暴露）。应改为 `parents[3] / 'scripts' / 'config' / 'default.yaml'` |
| H2 | **依赖三方不一致：fastapi/uvicorn/lerobot 仅 environment.yml 有** | `pyproject.toml:10-19`、`scripts/requirements.txt` | Web 平台（`app.py:292` import uvicorn）与 LeRobot 导出器（`export_lerobot.py:211`）在按 README `pip install -e .` / `pip install -r requirements.txt` 的新环境下**无法启动/运行**。README 推荐的 pip 路径是伪可用 |
| H3 | **`review_session.py`（PyQt5）播放速度双定时器 bug** | `review_session.py:261-264` + `:562-566` | `play_timer`（33ms→`_tick`）与 main() 里的 `t`（30ms→`_tick()+_redraw_curve()`）两个 QTimer 同时驱动 `_tick`，1x 播放实际约 **2x 速度**。删掉其中一个（建议保留 play_timer，把重绘挂上去） |
| H4 | **`scripts/test/archive/` 7 个脚本 import 全部失效** | `test_twincat_*.py`（5 个）、`test_calibrate_temporal_drift.py`、`test_twincat_plc_time_config.py` | 仍 import `acquisition.*` / `tools.*` / `run_acquisition` 旧符号（T13 前包路径），**完全不可运行**；且 `test_twincat_plc_time_config.py:12` 引用的 `build_source_from_config` 在 run_acquisition.py 中本就不存在 |
| H5 | **`vlgp/constants.py` 是"假共享"——零引用** | `constants.py` 全文件 | grep 确认无任何 `vlgp.constants` import；而 `build_srth_dataset.py:74-100` 等 **6+ 个模块仍各自定义 `ARMS = (1,2,3,4)`** 和全套列名/魔法数字。要么接线（推荐），要么删除该模块 |
| H6 | **核心 schema 在 5+ 模块重复** | 相机↔stream 映射：`build_srth_dataset.py:309/481-487`、`export_lerobot.py:57-67`、`review_session.py:49-55`、`web/session_index.py:22-25`、`align_camera_anchor.py:314-317` | arm 列名（POS/ORIENT/GRIPPER/INSTRUMENT）另有 align/verify/build/export/session_index 各一份；任何一次字段改名需同步 5 处，是当前最高维护风险点 |
| H7 | **Web 层反向依赖 dataset 层** | `web/session_index.py:139` `from vlgp.dataset.build_srth_dataset import instrument_label` | 下游 Web 反向 import 上游构建工具的**内部函数**（层次泄漏）；`INSTRUMENT_NAME`/`instrument_label` 应上移到公共模块（annotations 或 constants） |
| H8 | **`example_acquisition.yaml` 相机绑定过时且是默认配置** | `scripts/config/example_acquisition.yaml:2,9`（`arm_1: 2`）vs `default.yaml:6`（`arm_1: 1`） | 硬件绑定已在 T3/T4（08-17/18）确认为 `arm_1=1, arm_2=0, arm_3=3`；example 的注释与绑定值还停在旧 rig，而它是 `run_acquisition.py` 的**默认 --config**——不传参运行会录错相机映射 |
| H9 | **`pack_session.py` 是被取代的孤立功能** | `dataset/pack_session.py` 全文件 | SurgSync 风格 FFV1 打包：无 .bat、无文档提及、无调用方（`PACKED_REL` 同样无人用）；其"释放格式"定位已被 T17 LeRobotDataset v3 导出取代。建议归档或明确保留并补文档 |
| H10 | **操作依据类文档仍引用迁移前路径** | `AGENT_HANDOFF.md:127,138,275,282`（`scripts/acquisition/`、`python scripts/tools/review_session.py ...`）；`tech_repo/VLGP_标定与元数据规范_20260817.md` 第 6 节"正式采集一页流程"（L163-179）；`tech_repo/VLGP_项目状态与技术备忘_20260816.md` §7 关键文件索引（L96-100） | 这些是执行 Agent/操作者**实际照做的命令**，指向不存在的文件。应批量改为 `python -m vlgp.*` 或 .bat |
| H11 | **前端 innerHTML 未转义** | `web/static/app.js:102-103`（session 列表）、`:706-708`（renderArmStatus） | `s.name` 来自文件系统目录名，未转义直接拼 innerHTML。本机工具攻击面有限，但若 sessions/ 下出现含 HTML 的目录名即触发注入；配合 CORS `allow_origins=['*']`（app.py:73）模式不佳。建议改 textContent/createElement，CORS 收紧到 127.0.0.1 |

---

## 二、🟡 中风险问题（一致性 / 维护风险）

### 2.1 死代码 / 无消费者（建议清理或归档）

| 位置 | 问题 |
|---|---|
| `alignment/verify_alignment.py:40-43` | `fit_plc_pc_model()` 兼容包装函数全库无调用（死代码） |
| `alignment/calibrate_temporal_drift.py` 全文 | **无任何活跃消费者**；唯一引用它的 `scripts/test/archive/test_calibrate_temporal_drift.py` 已失效（import 旧 `tools.` 路径）。其功能已被 align/verify 内建 drift-fit 取代 → 建议随 `unify_alignment` 一起归档 |
| `alignment/verify_alignment.py:122` | `aligned_twincat.csv` 产物的唯一消费者是已归档的 `analysis/archive/analyze_alignment.py`；活跃流水线只用到控制台诊断 |
| `acquisition/timestamp.py:48,68,31` | `convert_host_to_master`、`ns_to_filetime`、`ClockConfig.twincat_epoch_offset_ns` 均无调用方（`filetime_to_ns` 仅 `scripts/test/verification/check_clock_sync.py` 用） |
| `kinematics/transform.py:45-61` | `self_check()` 无任何调用（不在 `__main__` 也不在测试） |
| `capture/camera_assignment_setup.py:194,59` | `best_profile_for_target()`、`ResolutionProfile.__repr__` 无调用；且该 CLI 版整体无 .bat（`camera_assignment_setup.bat` 实际运行的是 GUI 版），与 GUI 功能几乎完全重叠 |
| `pyproject.toml:22-23` | `vlgp-acquire` 与 `vlgp-run` 指向同一函数且全库无人引用；`vlgp-run` 命名还暗示主采集入口（真正入口是 `run_acquisition.py`） |
| `dataset/export_lerobot.py:52,87,77-82,126` | `import session_layout` 未用；`SESSIONS_ROOT` 未用；`ACTION_COLS` 与 `ACTION_NAMES` 完全重复；`export_episode(img_sizes=...)` 为死参数（main 从不传） |
| `review/write_session_meta.py:80` | `k in template or k not in template` 是**恒真式**，等价 `meta.update(data)`（疑似"只取 template 内字段"的意图写反了） |
| `annotations/task_hierarchy.py:241-242,237-240` | `if not subs and not leaf_ok: pass` 死块；`leaf_ok` 只写不读；`:162` 对同一路径调用两次 `instructions_for()` |
| `analysis/analyze_gripper.py:80` | `arm_id is not None` 恒真（键恒为 int）；`:71-75` legacy 分支先塞 None 再覆盖，写法绕 |
| `analysis/analyze_session.py:218` | `analyze_cross_modal(session_info, ...)` 的 `session_info` 参数体内从未使用 |
| `calibration/camera_param_tuner.py:169,246` | `last_save = time.time()` 两处只写不读 |
| `capture/camera_assignment_setup.py` / `usb_camera_process.py:10` | `import os` 未使用（后者）；`twincat.py:89` `local_ams_net_id` 配置字段从未传入 `pyads.Connection` |
| 10 个模块 | `import sys` 未使用：align_camera_anchor(31)、verify_alignment(9)、unify_alignment(14)、build_srth_dataset(65)、pack_session(29)、images_to_video(25)、write_session_meta(20)、analyze_session(10)、analyze_gripper(28)、collect_calibration(22) |

### 2.2 重复实现（应抽公共模块）

| 主题 | 位置 |
|---|---|
| **相机探测/预览/写配置** ×4 | `capture/camera_assignment_gui.py:23-216`、`camera_assignment_setup.py:23-216,371-434`、`preview_connected_cameras.py:21-114`、`preview_single_camera.py:21-36`（`COMMON_RESOLUTIONS`+`BACKENDS`+`CameraInfo`+`probe_cameras`+`update_config` 各自一份，细节互有出入）→ 建议抽 `vlgp.capture.camera_probe` |
| **PLC-PC 漂移拟合诊断** ×4 | `align_camera_anchor.fit_plc_pc_model`、`verify_alignment.fit_plc_pc_clock_model`、`calibrate_temporal_drift`、`analyze_session`（均包 `fit_linear_timestamps`，diag 键名还不一致：`residual_std_ms` vs `residual_std_ns`） |
| **最近帧 searchsorted** ×3 | verify_alignment(68-73)、align_camera_anchor(158-162)、archive/unify_alignment(24-30) |
| **SLERP** ×2 | `build_srth_dataset.slerp_quat`(125-142) 与 `align_camera_anchor.slerp`(143-155) 同公式 |
| **路径解析** ×2 | `review_session._resolve_session`(508-521) 与 `process_session.resolve_session`(40-49) |
| **reviewed_by/reviewed_at/os_user** ×2 | `review_session.os_user` ↔ `web/app.py._os_user`(247-250)（注释自认 "Same as..."）；`_resolve_session` 同理 |
| **平均 FPS** ×2 | `images_to_video.compute_average_fps`(41-46) 与 `pack_session`(85) |
| **session 装配** ×2 | `run_acquisition.py:44-50` 与 `manual_acquisition.py:83-89`（建议 `AcquisitionSession.from_config()`） |
| **手拼 robot_pose 路径绕过 session_layout** | `kinematics/transform.py:64-91`（`rp.glob('robot_pose_arm*/...')`，且不支持 legacy 单 CSV）、`kinematics/validate.py:44-47`、`web/align_service.py:140`、`review_session.py:48`（自建 `ALIGN_REL` 而不用 `session_layout.alignment_path()`） |

### 2.3 行为/健壮性问题

| 位置 | 问题 |
|---|---|
| `web/app.py:132,58-59` | `max_width` 与 `rows` 无上限：超大 max_width 不缩反放、超大 rows 放大线程池编码的 CPU/内存占用 → 建议 max_width 限 32~1280、rows 单请求限 ~512 |
| `web/app.py:65-69,281` | `TaskHierarchy.load()` 异常被吞成 `hier=None` 后，segments 词表校验**静默跳过**而 PUT 照常 200；应显式 503 |
| `usb_camera_worker.py:355-359` | `capture_thread.run = _run_with_result` hack：target/args 已传 `capture_loop` 又被整体覆盖成死参数，`capture_result` dict 绕圈传值 → 直接以包装函数作 target |
| `usb_camera_worker.py:204-205` | worker `--width/--height` 必填，与 source 层"可选"语义冲突：不写宽高的 `process_camera` 配置会在 `_wait_for_ready` 以 RuntimeError 退出 → 去掉 required 或 config 校验强制 |
| `usb_camera_worker.py:274` | `--exposure` None 时仍强制应用 -6，与配置注释"None = driver default"矛盾 |
| `preview_connected_cameras.py:79` | `ret` 变量被分辨率循环第二次 read 覆盖，`default_frame` 与"最后循环的 ret"混用 → 末分辨率探测失败时默认帧被误标 FAIL |
| `default.yaml:81-82,99-100` | `auto_wb: 1` 与 `wb_temperature` 并存——worker(281-288) 明确 auto_wb==1 时跳过 WB_TEMPERATURE，**色温值静默无效**（与备忘 0803"禁用 AWB 后 arm_3 spread 降至 0.005"结论冲突）→ 需硬件核收口 |
| `default.yaml`（无 warmup_s）vs 备忘"arm_1/3 需 warmup_s: 6.0" | 待确认是否已被 auto_wb 方案替代 |
| `review_session.py:179-192` | fallback 表把相机 PC 时间戳列命名为 `anchor_timestamp_ns_plc`——**名不符实**（PC 轴不是 PLC 轴） |
| `review_session.py:191` | 魔法数字 `tolerance=50_000_000`（50 ms） |
| `analyze_session.py:113-115` | 漂移分支条件用 `timestamp_ns_plc` 列（仅 `output_dual_timestamps: true` 时写入），pc_arrival 模式会话**静默不执行**该分支；align/verify 都用 `timestamp_ns` 对 `timestamp_ns_pc` 拟合 → 统一列名 |
| `kinematics/transform.py:31` | 生产代码用 assert 校验输入（`python -O` 下失效）→ 改显式 raise |
| `kinematics/__init__.py` 全文 | **FK 实现放在包 `__init__.py`**（180 行 MDH 表+`fk()`），validate.py `from . import fk` 依赖；建议移 `kinematics/fk.py`，`__init__` 只 re-export |
| `build_srth_dataset.py:500` | `kept_files` 含 len(grid) 帧而 actions 只有 len(grid)-1 行 → obs 清单多一张"孤儿帧"（帧号 len(grid)-1）；export_lerobot 按 actions 行读不受影响，但 manifest 与 actions 行数不一致（待确认意图） |
| `pack_session.py:191` | `session_dir.parents[1] / 'packed'` 隐含"会话在 <repo>/sessions/ 下"假设 |
| `kinematics/__init__.py:110` | 12 个 joint 值只消费 11 个（joint_12 恒被丢弃）——T16 待验证假设 |
| `app.js:170` | 切换 session 清空 `frameCache` 时未 revoke 旧 blob object URL（仅预加载窗口驱逐时 revoke） |

### 2.4 一致性 / 文档矛盾（内容层）

| 位置 | 问题 |
|---|---|
| `README.md:112` vs `:118-119` | 目录树注释称 0803 为"主文档，持续更新"，与自身"更多文档"（0803=技术背景主文档、0816=当前快照）矛盾 |
| `SESSION_CHECKPOINT.md` 待办 L91 | "T15 ⏸️ 待 PM 定义词表" 与同文件"最近完成"L44-52（T15 v0 已 37/37、43/43 PASS、词表 v0.1 已落地）自相矛盾 |
| `SESSION_CHECKPOINT.md:3-4` | 头部"更新时间 2026-08-26"落后内容条目（08-27~08-30）；编号重复两个"22."（L37/38） |
| `SESSION_CHECKPOINT.md:12` / 0816 头部 | "任务清单 T1–T6"范围标注过时（AGENT_HANDOFF 现覆盖 T0–T17） |
| `AGENT_HANDOFF.md:3` | 头部"最近更新 08-26、剩余 T15/T16"未反映正文 T15 v0 完成、T17 ✅（08-27） |
| `AGENT_HANDOFF.md:41,42,46,47,49` 等归档区 | 历史 `scripts/tools/*.py` 记录（可接受，建议加迁移注记） |
| `tech_repo/VLGP_项目技术文档_20260719.md`、`_项目进展报告_20260719.md` | 仍留在根目录且被 README 引用，内容全为迁移前（`scripts/acquisition/`）；archive/ 已收纳更老的 0713/0717 → 建议归档 |
| `tech_repo/VLGP_项目状态与技术备忘_20260803.md` §4.2/§6.6 | 工具表与用法仍列 `scripts/tools/*.py` 与不存在的 `three_cameras.yaml`（仅 archive/three_cameras_legacy.yaml）；08-26 已有"过程性归档"文件但归档不彻底 |
| `tech_repo/VLGP_项目状态与技术备忘_20260816.md` §5 | T9 行"待工程师答复"已过时（08-20 已关闭） |
| `scripts/test/README.md:45-46` | `check_clock_sync.py` 描述为"NTP 检查"，实际是 TwinCAT ADS 时钟对比 |
| `scripts/test/verification/verify_full_sync.py:20-24` | 仍读旧布局 `all_cameras/camera_endoscope_image_raw/...`（现行 `camera_arm_N/` + `robot_pose/robot_pose_armN/`），且在 verification/（非 archive），按 README 可运行但实际找不到文件 |

> 勘误：文档子代理曾报告 `task_hierarchy.yaml` 为 GBK 编码——经字节级验证为**合法 UTF-8（无 BOM）**，此条不成立，已剔除。

---

## 三、🟢 低风险 / 系统性清理项

1. **docstring 命令过时（系统性，约 20 个模块）**：`python scripts/tools/xxx.py` / `python tools/xxx.py` 在 T13 后全部失效，正确形式 `python -m vlgp.<pkg>.<module>`。涉及：align_camera_anchor、verify_alignment、calibrate_temporal_drift、build_srth_dataset、pack_session、images_to_video、write_session_meta、review_session、process_session、manual_acquisition、camera_assignment_gui/setup、preview_*、collect_calibration、camera_param_tuner、analyze_session、analyze_gripper（仅 export_lerobot、task_hierarchy 写对了）。
2. **产物元数据旧路径**：`build_srth_dataset.py:589` `'generated_by': 'scripts/tools/build_srth_dataset.py'`、`analyze_gripper.py:196` 同类——会把错误出处写进数据集/报告 JSON。
3. **冗余依赖**：`xlsxwriter`、`openpyxl`（requirements.txt:2-3 + environment.yml）全仓无任何使用；`matplotlib` 在 requirements/env 有但 pyproject 缺（analyze_session 需要）；`seaborn` 仅归档文件 import 且未使用。
4. **`pyproject.toml:29`** 声明 `py.typed` 但文件不存在。
5. **worker docstring**：`usb_camera_worker.py:8` `python -m acquisition.process_workers...` 缺 `vlgp.` 前缀。
6. **`.bat` 命名**：`camera_assignment_setup.bat` 实际跑 GUI 版（README 已注明，属有意为之，但建议改名或补 CLI 版 .bat）；`pack_session`/`export_lerobot` 无 .bat。
7. **`web/app.py:154-166`** base64 `/frames/batch` 端点仅测试在用（前端已切 batch-bin）——可保留兼容但建议注释标注。
8. **`align_service.py:41` / `frame_service.py:39`** 模块级全局 `_jobs`/`_PRELOAD_REGISTRY` 只增不减；多实例 create_app 时 lru_cache 以 SessionIndex 身份为 key 会失效——单进程部署可接受，建议注释说明。
9. **`frame_service.py:154`** 每预加载 job 新建 12 线程池，多会话并发叠加。
10. **`manual_acquisition.py:91,99`** 访问私有 `session._sources` → 建议加公开属性。
11. **`collect_calibration.py:36-38`** `DEFAULT_BINDINGS`/`DEFAULT_RESOLUTIONS` 硬编码并自注"mirrors default.yaml"——配置漂移源，应从配置读取。
12. **`process_session.py:90-117`** 步骤编号硬编码 "1/6..6/6"，跳过 review 后编号错位。
13. **`usb_camera_worker.py:114`** 日志 `Camera %d ... cap.get(CAP_PROP_POS_MSEC)`——`%d` 与实参（毫秒位）语义不符（copy-paste 痕迹）。
14. **`twincat.py:642-658`** writer `write()` 先拼 row 再重建 ordered，同一 dict 构造两遍，可化简。

---

## 四、待确认（需硬件/现场真值，未作定论）

- **default.yaml `arm_1:1` vs example_acquisition.yaml `arm_1:2` 哪份是当前 rig**（H8；T3 报告确认 arm_1=1，建议以 default 为准并同步 example）。
- **`auto_wb:1` + `wb_temperature` 并存**是否是有意保留的生产值（2.3 节；备忘 0803 与 default.yaml 结论冲突）。
- **`warmup_s: 6.0`**（备忘）是否已被 auto_wb 方案替代。
- **`build_srth_dataset.py:500` 孤儿末帧**、**`pack_session.py:191` packed 路径假设**、**MDH 12 关节消费 11 个**（T16 数据验证后定论）。

---

## 五、建议优先级排序

| 批次 | 动作 | 对应条目 |
|---|---|---|
| **P0（一跑就炸/装不上）** | 修 3 个默认 config 路径；依赖三方对齐（pyproject/requirements 补 fastapi、uvicorn、lerobot、matplotlib）；修 review_session 双定时器 | H1、H2、H3 |
| **P1（数据正确性）** | 收口 example_acquisition.yaml 相机绑定（或让 run_acquisition 默认指向 default.yaml）；统一 PLC 轴列名命名 | H8、2.3 |
| **P2（安全/边界）** | app.js innerHTML 转义 + CORS 收紧；max_width/rows 上限；hier 缺失时显式 503 | H11、2.3 |
| **P3（架构收口）** | 建共享 schema/常量模块并接线 constants.py（H5/H6）；抽 capture 相机探测公共模块（2.2）；拆 `web→dataset` 反向依赖（H7）；`kinematics/fk.py` 迁移（2.3） | H5、H6、H7、2.2 |
| **P4（清理归档）** | 归档 `calibrate_temporal_drift` + 修复/删除失效 archive 测试（H4）；归档 `pack_session`（H9）；删除死代码清单（2.1）；删 xlsxwriter/openpyxl | H4、H9、2.1、三.3 |
| **P5（文档同步）** | 批量更新 docstring 命令（三.1）+ `generated_by`（三.2）；AGENT_HANDOFF/标定规范第 6 节/0816 索引改 `python -m`（H10）；SESSION_CHECKPOINT 头部/待办、README 主文档注释、0719 文档归档（2.4）；README 目录树补 web/kinematics/annotations 与 3 个缺失 .bat | H10、2.4、三 |

**一句话总结**：代码分层与单工具质量良好，最大的"架构债"是**跨工具共享常量的缺失（constants.py 零接线 + schema 五处重复）**与**新旧方案切换后的清理不彻底**；最大的"内容债"是**依赖清单三方割裂**与**文档/元数据中的迁移前路径残留**。按 P0→P5 顺序整改即可，无需伤筋动骨的重构。
