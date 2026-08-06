# Agent Notes

## Project Layout

- `scripts/` — data processing and acquisition scripts.
  - `run_acquisition.py` — **main entry point** for multi-modal recording sessions.
  - `acquisition/` — **unified multi-modal acquisition framework** (Session + pluggable DataSources).
    - `sources/` — active sources: `twincat`, `usb_camera_process`, `reserved_arm`.
    - `sources/legacy/` — **deprecated** thread-based camera sources (`usb_camera`, `usb_multi_camera`) and the `endoscope` placeholder. Kept only for backward compatibility; do not use for new work.
  - `config/` — YAML session configs for `run_acquisition.py`.
    - `default.yaml` — current working multi-arm full-data configuration.
    - `single_arm.yaml` — fallback single-arm configuration.
    - `three_cameras.yaml` — three-camera production configuration.
    - `example_acquisition.yaml` — documented example.
    - `archive/` — historical one-off test configs (no longer used).
  - `tools/` — post-processing and alignment utilities.
    - `align_camera_anchor.py` — SRT-H-style alignment: camera-anchored 30 Hz grid + LERP/SLERP pose interpolation for all 4 arms.
    - `verify_alignment.py` — align each video camera's frames to TwinCAT robot poses.
    - `unify_alignment.py` — robot-anchored unified CSV (nearest frame from all cameras).
    - `analyze_alignment.py` — alignment error distributions, histograms, CDF, time-series.
    - `analyze_session.py` — TwinCAT sampling rate/RTT, camera FPS stability, PLC-PC drift residual, cross-modal timeline.
    - `calibrate_temporal_drift.py` — estimate and correct camera timestamp drift against host clock.
    - `camera_assignment_setup.py` — interactive camera-to-arm assignment + real FPS benchmark.
    - `preview_connected_cameras.py` / `preview_single_camera.py` — camera preview helpers.
  - `test/` — TwinCAT benchmarks + diagnostics (`archive/` holds retired validation scripts).

## Conda Environment

Use the `SurgDataColl` environment (see root `environment.yml`):

```bash
conda activate SurgDataColl
```

## Acquisition Framework

Entry point: `run_acquisition.py`

To add a new sensor/modality, subclass `acquisition.base_source.DataSource`, add a writer, and register both in `acquisition.session.AcquisitionSession._create_writer()` and `run_acquisition.py::build_source_from_config()`.

Current built-in sources:
- `process_camera` — single camera running in a separate OS process (**recommended**; supports `backend: msmf` for cameras that downclock when DirectShow FPS is set)
- `twincat` — TwinCAT ADS robot pose/joint/gripper reads, supports multi-connection and `output_all_arms` (all 8 PLC symbol groups, 4 arms)
- `reserved_arm` — placeholder source for an arm without a physical camera
- legacy (deprecated): `usb_camera`, `usb_multi_camera`, `endoscope`

## Housekeeping

- `fps: null` in a camera config means "do not touch `CAP_PROP_FPS`" (see `usb_camera_worker.py`); this is required for cameras whose driver downclocks when FPS is set explicitly.
- `.bat` files must be saved as **GBK + CRLF** or they fail to parse on Chinese Windows.
