# VLGP Acquisition Framework

A unified, extensible multi-modal data acquisition framework for surgical-robot VLGP research.

## Core concepts

- **Session**: orchestrates one recording, manages the unified clock, starts/stops sources, writes metadata.
- **DataSource**: abstract base class for every sensor/modality. Runs in its own thread.
- **DataSample**: timestamped sample produced by a source.
- **UnifiedTimestamp**: single clock provider; defaults to host `time.time_ns()`, can be switched to TwinCAT-master or manual offset.

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
├── camera_endoscope/            # endoscope frames + metadata.csv (placeholder)
├── camera_wrist_left/           # USB camera frames + metadata.csv
├── camera_wrist_right/          # USB camera frames + metadata.csv
└── robot_poses/                 # TwinCAT CSV
```

## How to add a new data modality

1. Create `acquisition/sources/my_sensor.py`.
2. Subclass `DataSource` and implement `name`, `_acquire_loop()`, and optionally `get_summary()`.
3. Create a writer class (e.g. `MySensorWriter`) that knows how to persist samples.
4. Register both in `AcquisitionSession._create_writer()`.
5. Add a factory branch in `run_acquisition.py::build_source_from_config()`.
6. Add an entry to your YAML config.

## Running a session

```bash
# Use the example config (includes placeholders for endoscope + TwinCAT)
python run_acquisition.py --config config/example_acquisition.yaml --duration 10

# USB camera only quick test
python run_acquisition.py --config config/test_usb_only.yaml --duration 5
```

Press `Ctrl-C` to stop gracefully.

## Multi-USB camera recording

Use `usb_multi_camera` source when you have two or more identical USB cameras
(deprecated — prefer one `process_camera` source per camera):

```bash
python run_acquisition.py --config config/archive/multi_usb_cameras.yaml --duration 10
```

**Important Windows limitation**: two identical USB cameras usually cannot both use
DirectShow at the same resolution. The recommended workaround is to set different
resolutions per camera in the config and resize frames in post-processing if needed.

## TwinCAT integration checklist

- PLC variables declared in `MAIN` and `PlcTask` has **Create symbols** enabled.
- `StaticRoutes.xml` on the TwinCAT host contains the acquisition PC route with `Flags=0`.
- PC can reach TwinCAT on port `851` and the AMS Net ID is known.
- `pyads` installed in the environment.
- PLC publishes a nanosecond timestamp (`timestamp_ns`) for unified clock alignment.
