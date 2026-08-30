# Diagnostic Test Scripts

One-off diagnostic and benchmark scripts for the VLGP data-collection
hardware and TwinCAT integration. The main acquisition entry point is
`../run_acquisition.py`; the scripts here are for checks and debugging only.

Organized by function (2026-08-19):

```text
benchmark/     TwinCAT/ADS performance benchmarks
verification/  capture & multi-modal sync verification
probes/        system / device / USB / PLC symbol probes
archive/       retired one-off validation scripts (reference only)
```

Retired scripts (camera probes, TwinCAT timestamp-mode experiments) live in
`archive/` — see that folder if you need them for reference.

After T13 the capable tools live in the installed `vlgp` package
(`pip install -e .` from the repo root), invoked as modules:

- `python -m vlgp.capture.camera_assignment_setup` — camera-to-arm assignment + real FPS benchmark + config writer
- `python -m vlgp.capture.camera_assignment_gui` — GUI version of the above
- `python -m vlgp.capture.preview_connected_cameras` — scan all camera indices, save preview frames
- `python -m vlgp.capture.preview_single_camera` — live preview one camera
- `python -m vlgp.alignment.verify_alignment` / `python -m vlgp.alignment.align_camera_anchor` — cross-modal time alignment

## Dependencies

```bash
pip install -r ../requirements.txt
```

If you are using the `SurgDataColl` conda environment, `opencv-python` and
`pyads` are already installed.

## Scripts

### verification/

- `verify_capture.py` — read a saved `metadata.csv` and generate timestamp-watermarked verification images:
  `python verification/verify_capture.py --dir ../sessions/<session>/<camera>/ --num-frames 3`
- `verify_full_sync.py` — verify multi-modal alignment for a full sync session (camera metadata + TwinCAT poses):
  `python verification/verify_full_sync.py --session ../sessions/<session_name>`
- `check_clock_sync.py` — compare the PC clock against the TwinCAT PLC timestamp via ADS (per-sample offset + RTT; useful for verifying NTP/PTP synchronization or measuring ADS latency):
  `python verification/check_clock_sync.py --ams-net-id 192.168.1.100.1.1 --symbol MAIN.timestamp_ns --samples 30`

### benchmark/

- `benchmark_twincat.py` — comprehensive single-connection ADS RTT and read-loop benchmark
- `benchmark_twincat_connections.py` — multi-connection scaling test (1/2/4 connections) with duplicate CycleCount detection
- `benchmark_twincat_full_pipeline.py` — end-to-end benchmark through the full `AcquisitionSession` + CSV writer pipeline
- `benchmark_twincat_notification.py` — ADS device-notification (async subscription) performance and jitter
- `benchmark_twincat_raw.py` — low-level raw ADS reads using resolved index group/offset

### probes/

- `list_camera_devices.ps1` / `usb_info.ps1` / `usb_speed.ps1` / `network_info.ps1` — PowerShell system probes
- `list_twincat_time_symbols.py` — list PLC timestamp-related symbols
- `probe_cam1_preview_order.py` — camera preview ordering probe

## Typical workflow

1. `python -m vlgp.capture.camera_assignment_setup` — discover cameras, preview, benchmark real FPS, write arm assignment to `../config/default.yaml`.
2. `python -m vlgp.capture.preview_single_camera` — fine-tune focus or mounting position.
3. `python ../run_acquisition.py --config ../config/default.yaml --duration 60` — actual recording.
4. `python verification/verify_capture.py` — check saved frames and timestamps.
5. `python -m vlgp.alignment.verify_alignment` — cross-modal time alignment quality.

## Notes

- On Windows the scripts use `cv2.CAP_DSHOW` (DirectShow backend) by default to avoid MSMF initialization problems. (Exception: cameras that downclock when FPS is set explicitly may work better with `backend: msmf`; see the 2026-08-05 cam1 findings in the tech memo.)
- The timestamp recorded is the **frame-arrival time on the PC**, not the camera exposure time.
- If multiple cameras share the same USB controller you may see frame drops; plug them into separate USB root hubs when possible.
