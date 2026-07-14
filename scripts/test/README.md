# Camera Test Scripts

This folder contains quick diagnostic scripts for the VLGP data-collection hardware. The main acquisition entry point is `../run_acquisition.py`; the scripts here are for one-off checks and debugging.

## Dependencies

```bash
pip install -r ../requirements.txt
```

If you are using the `vla_data_align` conda environment, `opencv-python` is already installed.

## Scripts

### 1. `camera_detect.py`
List every camera index that OpenCV can open.

```bash
python camera_detect.py
python camera_detect.py --max-index 10
```

### 2. `preview_camera.py`
Show a live preview window with FPS overlay. Runs until you press `q`.

```bash
python preview_camera.py --index 3 --width 1280 --height 720
```

### 3. `camera_benchmark.py`
Measure the actual frame rate and frame-to-frame jitter of a single camera.

```bash
python camera_benchmark.py --index 3 --duration 5
```

### 4. `verify_capture.py`
Read a saved `metadata.csv` and generate timestamp-watermarked verification images.

```bash
python verify_capture.py --dir ../sessions/<session>/<camera>/ --num-frames 3
```

### 5. `check_clock_sync.py`
Check PC clock against an NTP server. TwinCAT/ADS clock sync requires the TwinCAT runtime.

```bash
python check_clock_sync.py --ntp pool.ntp.org
```

## Typical workflow

1. Run `camera_detect.py` to see which indices are available.
2. Run `preview_camera.py --index N` for each index to map it to a physical camera.
3. Run `camera_benchmark.py` on each candidate camera to confirm real FPS.
4. Use `../run_acquisition.py --config ../config/three_cameras.yaml` for actual multi-camera recording.
5. Run `verify_capture.py` to check the saved frames and timestamps.

## Notes

- On Windows the scripts use `cv2.CAP_DSHOW` (DirectShow backend) to avoid MSMF initialization problems.
- The timestamp recorded is the **frame-arrival time on the PC**, not the camera exposure time. For sub-frame synchronization you will later need calibration or hardware triggering.
- If multiple cameras share the same USB controller you may see frame drops; plug them into separate USB root hubs when possible.
