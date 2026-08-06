# Diagnostic Test Scripts

This folder contains quick diagnostic and benchmark scripts for the VLGP data-collection hardware and TwinCAT integration. The main acquisition entry point is `../run_acquisition.py`; the scripts here are for one-off checks and debugging.

Retired one-off validation scripts (camera probes, TwinCAT timestamp-mode experiments) have been moved to `archive/` — see that folder if you need them for reference.

For camera discovery, preview, and arm assignment, prefer the more capable tools in `../tools/`:

- `../tools/camera_assignment_setup.py` — interactive camera-to-arm assignment + real FPS benchmark + config writer
- `../tools/camera_assignment_gui.py` — GUI version of the above
- `../tools/preview_connected_cameras.py` — scan all camera indices and save preview frames
- `../tools/preview_single_camera.py` — live preview one camera with interactive selection

## Dependencies

```bash
pip install -r ../requirements.txt
```

If you are using the `SurgDataColl` conda environment, `opencv-python` and `pyads` are already installed.

## Scripts

### Capture verification

#### `verify_capture.py`
Read a saved `metadata.csv` and generate timestamp-watermarked verification images.

```bash
python verify_capture.py --dir ../sessions/<session>/<camera>/ --num-frames 3
```

#### `verify_full_sync.py`
Verify multi-modal alignment for a full sync session by loading camera metadata and TwinCAT poses.

```bash
python verify_full_sync.py --session ../sessions/<session_name>
```

### Clock checks

#### `check_clock_sync.py`
Check PC clock against an NTP server.

```bash
python check_clock_sync.py --ntp pool.ntp.org
```

### TwinCAT / ADS benchmarks

#### `benchmark_twincat.py`
Comprehensive single-connection ADS RTT and read-loop benchmark.

#### `benchmark_twincat_connections.py`
Framework-level multi-connection scaling test (1/2/4 connections) with duplicate CycleCount detection.

#### `benchmark_twincat_full_pipeline.py`
End-to-end benchmark through the full `AcquisitionSession` + CSV writer pipeline.

#### `benchmark_twincat_notification.py`
ADS device-notification (async subscription) performance and jitter.

#### `benchmark_twincat_raw.py`
Low-level raw ADS reads using resolved index group/offset.

### System probes

- `list_camera_devices.ps1` / `list_twincat_time_symbols.py` / `usb_info.ps1` / `usb_speed.ps1` / `network_info.ps1` — PowerShell / Python system probes

## Typical workflow

1. Run `../tools/camera_assignment_setup.py` to discover cameras, preview them, benchmark real FPS, and write the arm assignment to `../config/default.yaml`.
2. Use `../tools/preview_single_camera.py` for fine-tuning focus or mounting position.
3. Run `../run_acquisition.py --config ../config/default.yaml --duration 60` for actual recording.
4. Run `verify_capture.py` to check the saved frames and timestamps.
5. Run `../tools/verify_alignment.py` and `../tools/analyze_alignment.py` for cross-modal time alignment quality.

## Notes

- On Windows the scripts use `cv2.CAP_DSHOW` (DirectShow backend) by default to avoid MSMF initialization problems. (Exception: cameras that downclock when FPS is set explicitly may work better with `backend: msmf`; see the 2026-08-05 cam1 findings in the tech memo.)
- The timestamp recorded is the **frame-arrival time on the PC**, not the camera exposure time. For sub-frame synchronization you will later need calibration or hardware triggering.
- If multiple cameras share the same USB controller you may see frame drops; plug them into separate USB root hubs when possible.
