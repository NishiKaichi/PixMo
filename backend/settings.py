from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
RESULTS_DIR = BASE_DIR / "results"
MATERIALS_DIR = UPLOADS_DIR / "materials"
TARGETS_DIR = UPLOADS_DIR / "targets"

for d in [UPLOADS_DIR, RESULTS_DIR, MATERIALS_DIR, TARGETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ===== 制限（現状main.pyの値を踏襲）=====
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_ZIP_FILES = 200000
MAX_SINGLE_FILE_BYTES = 200 * 1024 * 1024
MAX_THUMBS_DISK_BYTES = 20 * 1024 * 1024 * 1024
THUMB_SIZE = 64
BIN_Q = 8

# ===== セッションTTL（分）=====
SESSION_TTL_MINUTES = 15
CLEANUP_INTERVAL_SECONDS = 60
