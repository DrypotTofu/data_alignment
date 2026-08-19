# Agent Notes

## Project Layout

- `src/vlgp/` — **installable package** (T13; `pip install -e .`, import as `vlgp.*`).
  - `acquisition/` — **unified multi-modal acquisition framework** (Session + pluggable DataSources).
    - `sources/` — active sources: `twincat`, `usb_camera_process`, `reserved_arm`.
    - `sources/legacy/` — **deprecated** thread-based camera sources; kept only for backward
      compatibility with a runtime deprecation warning (T12-P2-5); do not use for new work.
    - `process_workers/` — camera subprocess worker (launched as `-m vlgp.acquisition...`).
  - `alignment/` — `align_camera_anchor` / `verify_alignment` / `calibrate_temporal_drift`
    (superseded `unify_alignment` moved to `alignment/archive/`).
  - `dataset/` — `build_srth_dataset` / `pack_session` / `images_to_video`.
  - `review/` — `review_session` (GUI trim) / `process_session` (pipeline driver) /
    `write_session_meta`.
  - `calibration/` — `collect_calibration` / `camera_param_tuner`.
  - `analysis/` — `analyze_session` / `analyze_gripper`.
  - `capture/` — `manual_acquisition` / `camera_assignment_gui` / `camera_assignment_setup` /
    `preview_*`.
  - `constants.py` — shared magic numbers (30 Hz, 15 ms threshold, 60-step chunk, ...).
- `scripts/` — entry points + config + Windows launchers.
  - `run_acquisition.py` — **main entry point** for multi-modal recording sessions
    (imports `vlgp.acquisition`).
  - `config/` — YAML session configs (`default.yaml` current; `archive/` historical).
  - `tools/*.bat` — Windows launchers; paths derived from `%~dp0` (T13), python via conda
    fallback.
  - `test/` — TwinCAT benchmarks + diagnostics (`archive/` holds retired scripts).

## Conda Environment

Use the `SurgDataColl` environment (see root `environment.yml`):

```bash
conda activate SurgDataColl
```

## Acquisition Framework

Entry point: `run_acquisition.py`

To add a new sensor/modality, subclass `acquisition.base_source.DataSource`, add a writer if the source does not write directly to disk, and register both in `acquisition.registry` (`SOURCE_BUILDERS` and `WRITER_FACTORIES`). YAML loading and validation live in `acquisition.config`; session layout path helpers live in `acquisition.session_layout`.

Current built-in sources:
- `process_camera` — single camera running in a separate OS process (**recommended**; supports `backend: msmf` for cameras that downclock when DirectShow FPS is set)
- `twincat` — TwinCAT ADS robot pose/joint/gripper reads, supports multi-connection and `output_all_arms` (all 8 PLC symbol groups, 4 arms)
- `reserved_arm` — placeholder source for an arm without a physical camera
- legacy (deprecated): `usb_camera`, `usb_multi_camera`, `endoscope`

## Camera Bindings

Current YAML configs keep hardware mapping in top-level `camera_bindings` (`arm_1: 1`, `arm_2: 0`, `arm_3: 3`) and keep acquisition parameters (`width`, `fps`, `target_fps`, format) on each source. Direct per-source `camera_index` remains supported for old configs, but new edits should update `camera_bindings`; both assignment tools maintain that field.

## Housekeeping

- `fps: null` in a camera config means "do not touch `CAP_PROP_FPS`" (see `usb_camera_worker.py`); this is required for cameras whose driver downclocks when FPS is set explicitly.
- `.bat` files must be saved as **GBK + CRLF** or they fail to parse on Chinese Windows.
