# Diagnostic Test Scripts

This folder contains quick diagnostic and benchmark scripts for the VLGP data-collection hardware and TwinCAT integration. The main acquisition entry point is `../run_acquisition.py`; the scripts here are for one-off checks and debugging.

For camera discovery, preview, and arm assignment, prefer the more capable tools in `../tools/`:

- `../tools/camera_assignment_setup.py` — interactive camera-to-arm assignment + real FPS benchmark + config writer
- `../tools/camera_assignment_gui.py` — GUI version of the above
- `../tools/preview_connected_cameras.py` — scan all camera indices and save preview frames
- `../tools/preview_single_camera.py` — live preview one camera with interactive selection

## Dependencies

```bash
pip install -r ../requirements.txt
```

If you are using the `vla_data_align` conda environment, `opencv-python` and `pyads` are already installed.

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

### Clock and backend checks

#### `check_clock_sync.py`
Check PC clock against an NTP server.

```bash
python check_clock_sync.py --ntp pool.ntp.org
```

#### `probe_cameras.py`
Probe DirectShow camera indices and report whether each opens and returns frames.

```bash
python probe_cameras.py
```

#### `probe_all_backends.py`
Probe camera indices across multiple OpenCV backends (DSHOW, MSMF, ANY).

```bash
python probe_all_backends.py
```

#### `probe_camera_capabilities.py`
Probe supported resolutions and FOURCC codes for a specific camera index.

```bash
python probe_camera_capabilities.py --index 0
```

### TwinCAT / ADS benchmarks

#### `benchmark_twincat.py`
Comprehensive single-connection ADS RTT and read-loop benchmark.

```bash
python benchmark_twincat.py
```

#### `benchmark_twincat_connections.py`
Framework-level multi-connection scaling test (1/2/4 connections) with duplicate CycleCount detection.

```bash
python benchmark_twincat_connections.py
```

#### `benchmark_twincat_full_pipeline.py`
End-to-end benchmark through the full `AcquisitionSession` + CSV writer pipeline.

```bash
python benchmark_twincat_full_pipeline.py
```

#### `benchmark_twincat_notification.py`
ADS device-notification (async subscription) performance and jitter.

```bash
python benchmark_twincat_notification.py
```

#### `benchmark_twincat_raw.py`
Low-level raw ADS reads using resolved index group/offset.

```bash
python benchmark_twincat_raw.py
```

### TwinCAT configuration / timestamp tests

- `test_twincat_dual_timestamps.py`
- `test_twincat_legacy_modes.py`
- `test_twincat_master_clock.py`
- `test_twincat_plc_time_config.py`
- `test_twincat_rtt_correction.py`
- `test_twincat_timestamp_mode.py`
- `test_calibrate_temporal_drift.py`

### Misc one-off helpers

- `capture_frame_each.py` — capture one frame from each of several indices
- `capture_one_resolution.py` — capture one frame at a specific resolution
- `open_cam3_then_cam0.py` — reproduction helper for a specific camera ordering issue
- `list_camera_devices.ps1` / `list_twincat_time_symbols.py` / `usb_info.ps1` / `usb_speed.ps1` / `network_info.ps1` — PowerShell / Python system probes

## Typical workflow

1. Run `../tools/camera_assignment_setup.py` to discover cameras, preview them, benchmark real FPS, and write the arm assignment to `../config/default.yaml`.
2. Use `../tools/preview_single_camera.py` for fine-tuning focus or mounting position.
3. Run `../run_acquisition.py --config ../config/default.yaml --duration 60` for actual recording.
4. Run `verify_capture.py` to check the saved frames and timestamps.
5. Run `../tools/verify_alignment.py` and `../tools/analyze_alignment.py` for cross-modal time alignment quality.

## Notes

- On Windows the scripts use `cv2.CAP_DSHOW` (DirectShow backend) by default to avoid MSMF initialization problems.
- The timestamp recorded is the **frame-arrival time on the PC**, not the camera exposure time. For sub-frame synchronization you will later need calibration or hardware triggering.
- If multiple cameras share the same USB controller you may see frame drops; plug them into separate USB root hubs when possible.
