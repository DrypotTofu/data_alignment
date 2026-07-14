# Agent Notes

## Project Layout

- `scripts/` — data processing and acquisition scripts.
  - `align_with_video.py`, `make_chart_split.py`, `split_csv_by_time.py`, `xlsx_to_csv.py` — legacy CSV/XLSX alignment utilities.
  - `acquisition/` — **new unified multi-modal acquisition framework** (Session + pluggable DataSources).
  - `config/` — YAML session configs for `run_acquisition.py`.
  - `test/` — quick diagnostic scripts, mostly for USB cameras.
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

## Acquisition Framework

Entry point: `run_acquisition.py`

To add a new sensor/modality, subclass `acquisition.base_source.DataSource`, add a writer, and register both in `acquisition.session.AcquisitionSession._create_writer()` and `run_acquisition.py::build_source_from_config()`.

Current built-in sources:
- `usb_camera` — single OpenCV + DirectShow camera, saves PNG frames + metadata.csv
- `usb_multi_camera` — **multiple identical USB cameras in one thread** (recommended for wrist cameras)
- `endoscope` — placeholder for Hikvision SDK / HDMI / RTSP
- `twincat` — placeholder for ADS pose/joint reads
