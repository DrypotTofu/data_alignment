# Agent Notes

## Project Layout

- `scripts/` — data processing and acquisition scripts.
  - `run_acquisition.py` — **main entry point** for multi-modal recording sessions.
  - `acquisition/` — **new unified multi-modal acquisition framework** (Session + pluggable DataSources).
  - `config/` — YAML session configs for `run_acquisition.py`.
    - `default.yaml` — current working multi-arm full-data configuration.
    - `single_arm.yaml` — fallback single-arm configuration.
    - `three_cameras.yaml` — three-camera production configuration.
  - `tools/` — post-processing and alignment utilities.
    - `verify_alignment.py` — align each video camera's frames to TwinCAT robot poses.
    - `unify_alignment.py` — build one unified CSV where each robot pose row references the nearest frame from all cameras.
    - `analyze_alignment.py` — alignment error distributions, histograms, CDF, time-series.
    - `analyze_session.py` — TwinCAT sampling rate/RTT, camera FPS stability, PLC-PC drift residual, cross-modal timeline.
    - `calibrate_temporal_drift.py` — estimate and correct camera timestamp drift against host clock.
    - `camera_assignment_setup.py` — interactive camera-to-arm assignment + real FPS benchmark.
    - `preview_connected_cameras.py` / `preview_single_camera.py` — camera preview helpers.
  - `test/` — quick diagnostic scripts, mostly for USB cameras and TwinCAT benchmarks.
- `requirements.txt` — Python dependencies; includes `opencv-python` and `pyyaml` for the acquisition framework.

## Conda Environment

Use the `vla_data_align` environment created from `D:\Program Files\miniconda`:

```powershell
conda activate vla_data_align
```

If `conda` is not on PATH, run the hook first:

```powershell
& "D:\Program Files\miniconda\shell\condabin\conda-hook.ps1"
conda activate vla_data_align
```

For analysis scripts (`analyze_alignment.py`, `analyze_session.py`) you can also use the lightweight `.venv_analysis/` venv which has `matplotlib`, `pandas`, `seaborn`, `numpy` installed.

## Acquisition Framework

Entry point: `run_acquisition.py`

To add a new sensor/modality, subclass `acquisition.base_source.DataSource`, add a writer, and register both in `acquisition.session.AcquisitionSession._create_writer()` and `run_acquisition.py::build_source_from_config()`.

Current built-in sources:
- `usb_camera` — single OpenCV + DirectShow camera, saves frames + metadata.csv
- `usb_multi_camera` — multiple identical USB cameras in one thread
- `process_camera` — single camera running in a separate OS process (recommended for stable multi-camera recording)
- `twincat` — TwinCAT ADS robot pose/joint reads, supports multi-connection and `output_all_arms`
- `reserved_arm` — placeholder source for an arm without a physical camera
