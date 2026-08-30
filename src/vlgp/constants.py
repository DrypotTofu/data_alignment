"""Shared pipeline constants (T13: magic numbers collected in one place)."""
from __future__ import annotations

# --- Acquisition -----------------------------------------------------------
TARGET_FPS = 30.0          # master grid rate (SRT-H LL policy)
GRID_PERIOD_MS = 1000.0 / TARGET_FPS  # 33.33 ms
ARMS = (1, 2, 3, 4)
NUM_CAMERAS = 3

# Camera worker warm-up default (seconds of frames discarded after open so
# auto white balance / exposure converge before recording starts).
DEFAULT_WARMUP_S = 1.5

# --- Alignment -------------------------------------------------------------
# Max wrist-camera nearest-frame error (ms) to keep a grid row (13.5 节).
ALIGN_ERROR_THRESHOLD_MS = 15.0
# p99 acceptance line for frame intervals: <= 2 frame periods.
MAX_INTERVAL_P99_MS = 2.0 * GRID_PERIOD_MS

# --- SRT-H episode ---------------------------------------------------------
ACTION_DIM_PER_ARM = 10      # 3 delta_pos + 6 delta_rot_6d + 1 jaw
ACTION_DIM = 40              # 4 arms
CHUNK_STEPS = 60             # future action chunk @ 30 Hz = 2 s
HL_INTERVAL_S = 1.0          # HL policy sample spacing
HL_WINDOW = 5                # current + 4 past frames at 1 s spacing

# Default jaw normalisation bounds (T1: global range ≈ [-55, +85]).
JAW_LO = -60.0
JAW_HI = 90.0

# arm_active_mask motion thresholds (heuristic until real task demos fix them).
MASK_POS_MM = 0.05
MASK_ROT_DEG = 0.1

# --- Calibration -----------------------------------------------------------
CALIB_BOARD = (9, 6)         # checkerboard inner corners
CALIB_SQUARE_MM = 22.5
CALIB_MIN_FRAMES = 3         # enough for cv2.calibrateCamera (recommend 15-25)
CALIB_RMS_ACCEPT_PX = 0.5

# --- Review / annotations --------------------------------------------------
ANNOTATIONS_SCHEMA_VERSION = 1

# --- Camera schema (single source of truth) --------------------------------
# CODE_REVIEW_20260830 H5/H6: these were duplicated (with drift risk) in
# review_session, web/session_index, build_srth_dataset and export_lerobot.
CAMERA_ORDER = ('camera_arm_2', 'camera_arm_1', 'camera_arm_3')
CAMERA_STREAMS = {'camera_arm_2': 'endoscope', 'camera_arm_1': 'wrist_left',
                  'camera_arm_3': 'wrist_right'}
# filename column per camera in the alignment CSV
FILENAME_COLS = {'camera_arm_2': 'anchor_filename',
                 'camera_arm_1': 'camera_arm_1_filename',
                 'camera_arm_3': 'camera_arm_3_filename'}
