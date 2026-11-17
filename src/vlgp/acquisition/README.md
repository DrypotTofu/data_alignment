# VLGP Acquisition Framework

A unified, extensible multi-modal data acquisition framework for surgical-robot VLGP research.

## Core concepts

- **Session**: orchestrates one recording, manages the unified clock, starts/stops sources, writes metadata.
- **DataSource**: abstract base class for every sensor/modality. Runs in its own thread.
- **DataSample**: timestamped sample produced by a source.
- **UnifiedTimestamp**: single clock provider; defaults to host `time.time_ns()`, can be switched to TwinCAT-master or manual offset.
- **Registry**: `acquisition.registry` maps YAML source types to source factories and source classes to writer factories.
- **Config validation**: `acquisition.config` loads YAML, applies top-level `camera_bindings`, and rejects duplicate camera indices, duplicate arm ids, and invalid rates before acquisition starts.
- **Session layout**: `acquisition.session_layout` is the shared source of truth for camera metadata and TwinCAT CSV paths.

## Architecture

```
┌─────────────────────────────────────────┐
│           AcquisitionSession            │
│  - unified clock                        │
│  - shared sample queue                  │
│  - writer thread                        │
│  - Ctrl-C graceful shutdown             │
└─────────────────────────────────────────┘
         │ registers        │ consumes
         ▼                  ▼
┌─────────────┐      ┌──────────────┐
│ DataSource  │      │ Disk writers │
│  (threads)  │──────▶│  per source  │
└─────────────┘      └──────────────┘
```

## Session directory layout

```
sessions/20260713_120000/
├── session.json                 # metadata, source summaries, sample counts
├── camera_arm_1/                # left wrist camera frames + metadata.csv
├── camera_arm_2/                # endoscope/main-view frames + metadata.csv
├── camera_arm_3/                # right wrist camera frames + metadata.csv
└── robot_pose/
    └── robot_pose_arm<N>/       # per-arm TwinCAT CSV in multi-arm mode
```

## How to add a new data modality

1. Create `acquisition/sources/my_sensor.py`.
2. Subclass `DataSource` and implement `name`, `_acquire_loop()`, and optionally `get_summary()`.
3. Create a writer class (e.g. `MySensorWriter`) that knows how to persist samples.
4. Register the source in `acquisition.registry.SOURCE_BUILDERS`.
5. Register the writer in `acquisition.registry.WRITER_FACTORIES` unless the source writes directly to disk.
6. Add an entry to your YAML config.

## Running a session

```bash
# Use the documented example config (three process cameras + TwinCAT)
python run_acquisition.py --config config/example_acquisition.yaml --duration 10

# Current single-arm fallback
python run_acquisition.py --config config/single_arm.yaml --duration 5
```

Press `Ctrl-C` to stop gracefully.

## Multi-USB camera recording

Use one `process_camera` source per camera. Legacy `usb_multi_camera` configs
remain under `config/archive/` only for reference.

```bash
python run_acquisition.py --config config/default.yaml --duration 10
```

**Important Windows limitation**: two identical USB cameras usually cannot both use
DirectShow at the same resolution. The recommended workaround is to set different
resolutions per camera in the config and resize frames in post-processing if needed.

## HWS capture card (创视之星)

Use `type: hws_camera_process` for SDK callback capture with device-side frame
timestamps. A ready-to-edit full-rig example is `scripts/config/hws_endoscope.yaml`.
For the current dual-channel 720p60 setup, use `scripts/config/hws_dual_60fps.yaml`;
it maps arm 2 to SDK index 0 and arm 1 to SDK index 1, with no software decimation.
The Web acquisition panel also lists these configs, and its camera preflight uses
the HWS SDK.

```yaml
- type: hws_camera_process
  name: camera_arm_2
  topic_name: camera/arm_2/image_raw
  arm_id: 2
  width: 1280
  height: 720
  fps: 60                 # requested SDK input rate
  target_fps: 30          # decimate on the device-time grid
  buffer_count: 4
  writer_threads: 6
  image_format: png
  png_compression: 1
```

The SDK's `timestamp100ns` uses a relative device clock. The first post-warm-up
frame anchors it to `time.time_ns()`; later frames use
`anchor_pc_ns + (device_ns - anchor_device_ns) + clock_offset_ns`. This avoids
Python callback scheduling jitter while preserving an absolute timeline. The
original callback arrival time remains available for offset/drift calibration.

HWS `metadata.csv` keeps the standard seven columns and appends
`device_timestamp_100ns` and `device_frame_index`. Standard downstream readers
continue to use `timestamp_ns` as before. The default DLL is
`D:\ProgramFiles\HWS_Capture_SDK_v6.0.0\binX64\hwscapture.dll`; override it with
`sdk_dll` in YAML or the `HWS_SDK_DLL` environment variable. DirectShow exposure,
white-balance, FourCC and backend settings do not apply to this source.

Measured on this PC (2026-11-16): native callbacks were about 59.72 fps despite a
30 fps request, so `target_fps` remains necessary. Raw device inter-frame jitter
was 0.052 ms versus 0.487 ms for PC arrival. A 10 s dual-channel 720p test wrote
303/308 frames at a mean 29.999 fps with zero queue drops; the variable selected
frame intervals are expected when decimating a non-exactly-60-fps stream to 30.

## TwinCAT integration checklist

- PLC variables declared in `MAIN` and `PlcTask` has **Create symbols** enabled.
- `StaticRoutes.xml` on the TwinCAT host contains the acquisition PC route with `Flags=0`.
- PC can reach TwinCAT on port `851` and the AMS Net ID is known.
- `pyads` installed in the environment.
- PLC publishes a nanosecond timestamp (`timestamp_ns`) for unified clock alignment.
