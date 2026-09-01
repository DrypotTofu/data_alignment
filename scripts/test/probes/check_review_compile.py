# -*- coding: utf-8 -*-
"""Compile-check every changed/new module (subagent output verification)."""
import py_compile

FILES = [
    'src/vlgp/acquisition/process_workers/usb_camera_worker.py',
    'src/vlgp/acquisition/sources/usb_camera_process.py',
    'src/vlgp/acquisition/timestamp.py',
    'src/vlgp/alignment/align_camera_anchor.py',
    'src/vlgp/alignment/verify_alignment.py',
    'src/vlgp/analysis/analyze_gripper.py',
    'src/vlgp/analysis/analyze_session.py',
    'src/vlgp/annotations/task_hierarchy.py',
    'src/vlgp/calibration/camera_param_tuner.py',
    'src/vlgp/calibration/collect_calibration.py',
    'src/vlgp/capture/preview_connected_cameras.py',
    'src/vlgp/capture/preview_single_camera.py',
    'src/vlgp/dataset/images_to_video.py',
    'src/vlgp/kinematics/__init__.py',
    'src/vlgp/kinematics/fk.py',
    'src/vlgp/kinematics/transform.py',
    'src/vlgp/review/write_session_meta.py',
    'scripts/test/verification/verify_full_sync.py',
    'src/vlgp/alignment/archive/calibrate_temporal_drift.py',
    'src/vlgp/dataset/archive/pack_session.py',
    'src/vlgp/web/app.py',
    'src/vlgp/web/frame_service.py',
    'src/vlgp/web/session_index.py',
    'src/vlgp/web/align_service.py',
    'src/vlgp/review/process_session.py',
    'src/vlgp/review/common.py',
    'src/vlgp/dataset/build_srth_dataset.py',
    'src/vlgp/dataset/export_lerobot.py',
    'src/vlgp/constants.py',
    'src/vlgp/annotations/instruments.py',
    'src/vlgp/acquisition/session.py',
    'src/vlgp/acquisition/sources/twincat.py',
    'src/vlgp/capture/manual_acquisition.py',
    'src/vlgp/capture/camera_assignment_gui.py',
    'src/vlgp/capture/camera_assignment_setup.py',
    'scripts/test/verification/web_regression.py',
]

bad = []
for f in FILES:
    try:
        py_compile.compile(f, doraise=True)
    except Exception as e:  # noqa: BLE001
        bad.append(f'{f}: {type(e).__name__}: {e}')

if bad:
    print('FAILED:')
    print('\n'.join(bad))
    raise SystemExit(1)
print(f'all {len(FILES)} files compile OK')
