from __future__ import annotations

import io
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Tuple

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
RESULTS_DIR = BASE_DIR / "results"
UPLOADS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# ざっくり制限（まずは安全側）
MAX_ZIP_FILES = 800
MAX_TOTAL_UNZIPPED_BYTES = 1.2 * 1024 * 1024 * 1024 # 1.2GB（必要なら調整）
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# 超簡易ジョブストア（開発用：再起動すると消える）
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()


def _set_job(job_id: str, **kwargs):
    with jobs_lock:
        jobs[job_id].update(kwargs)


def _get_job(job_id: str) -> Dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise KeyError
        return dict(job)


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> List[Path]:
    """
    ZIP Slip対策 + ファイル数/サイズ制限付きで解凍して、画像ファイルパス一覧を返す
    """
    extracted: List[Path] = []
    total_unzipped = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > MAX_ZIP_FILES:
            raise ValueError(f"ZIP内ファイル数が多すぎます: {len(members)} > {MAX_ZIP_FILES}")

        for info in members:
            # パスの正規化（ZIP Slip防止）
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                continue

            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_EXT:
                continue

            total_unzipped += info.file_size
            if total_unzipped > MAX_TOTAL_UNZIPPED_BYTES:
                raise ValueError("ZIPの展開サイズが上限を超えました")

            out_path = dest_dir / Path(name).name
            with zf.open(info, "r") as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

            extracted.append(out_path)

    if not extracted:
        raise ValueError("ZIPから有効な画像が取り出せませんでした（jpg/png/webpのみ対応）")
    return extracted


def avg_rgb(img: Image.Image) -> Tuple[int, int, int]:
    # 1x1に縮小して平均色の近似を取る（高速・十分使える）
    small = img.resize((1, 1), resample=Image.Resampling.BOX)
    r, g, b = small.getpixel((0, 0))
    return int(r), int(g), int(b)


def color_dist2(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def build_mosaic(
    target_path: Path,
    tile_paths: List[Path],
    out_path: Path,
    tile_size: int,
    out_width: int,
    job_id: str,
):
    """
    超シンプルなフォトモザイク：
    - 素材タイル：平均RGBで最近傍（全探索）
    - 使い回し制限なし（まず動かす）
    """
    _set_job(job_id, status="running", progress=0, message="Loading images...")

    # 1) ターゲット読み込み & リサイズ
    target = Image.open(target_path).convert("RGB")
    w, h = target.size
    if out_width < tile_size * 5:
        raise ValueError("out_widthが小さすぎます")

    scale = out_width / w
    new_h = int(h * scale)
    target = target.resize((out_width, new_h), resample=Image.Resampling.LANCZOS)

    # タイルに割り切れるようにクロップ
    tw = (target.size[0] // tile_size) * tile_size
    th = (target.size[1] // tile_size) * tile_size
    target = target.crop((0, 0, tw, th))

    grid_w = tw // tile_size
    grid_h = th // tile_size
    total_cells = grid_w * grid_h

    # 2) 素材タイル前処理（リサイズ＋平均色）
    _set_job(job_id, message="Preprocessing tiles...")
    tiles_img: List[Image.Image] = []
    tiles_avg: List[Tuple[int, int, int]] = []

    for p in tile_paths:
        try:
            im = Image.open(p).convert("RGB").resize((tile_size, tile_size), resample=Image.Resampling.LANCZOS)
            tiles_img.append(im)
            tiles_avg.append(avg_rgb(im))
        except Exception:
            continue

    if len(tiles_img) < 10:
        raise ValueError("素材画像が少なすぎます（最低でも10枚以上推奨）")

    # 3) 合成
    _set_job(job_id, message="Building mosaic...")
    out = Image.new("RGB", (tw, th))

    done = 0
    for gy in range(grid_h):
        for gx in range(grid_w):
            x0 = gx * tile_size
            y0 = gy * tile_size
            region = target.crop((x0, y0, x0 + tile_size, y0 + tile_size))
            region_avg = avg_rgb(region)

            # 最近傍（全探索）
            best_i = 0
            best_d = 10**18
            for i, a in enumerate(tiles_avg):
                d = color_dist2(region_avg, a)
                if d < best_d:
                    best_d = d
                    best_i = i

            out.paste(tiles_img[best_i], (x0, y0))

            done += 1

        # 進捗更新（行ごと）
        progress = int(done / total_cells * 100)
        _set_job(job_id, progress=progress)

    out.save(out_path, quality=92)
    _set_job(job_id, status="done", progress=100, message="Done!")


def run_job(job_id: str, target_path: Path, tiles_zip_path: Path, tile_size: int, out_width: int):
    try:
        job_dir = UPLOADS_DIR / job_id
        tiles_dir = job_dir / "tiles"
        tiles_dir.mkdir(parents=True, exist_ok=True)

        tile_paths = safe_extract_zip(tiles_zip_path, tiles_dir)

        out_path = RESULTS_DIR / f"{job_id}.jpg"
        build_mosaic(
            target_path=target_path,
            tile_paths=tile_paths,
            out_path=out_path,
            tile_size=tile_size,
            out_width=out_width,
            job_id=job_id,
        )
        _set_job(job_id, result_path=str(out_path))
    except Exception as e:
        _set_job(job_id, status="error", message=str(e))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/jobs")
async def create_job(
    target_image: UploadFile = File(...),
    tiles_zip: UploadFile = File(...),
    tile_size: int = Form(32),
    out_width: int = Form(1200),
):
    if tile_size < 8 or tile_size > 128:
        raise HTTPException(status_code=400, detail="tile_sizeは8〜128の範囲にしてください")
    if out_width < 400 or out_width > 4000:
        raise HTTPException(status_code=400, detail="out_widthは400〜4000の範囲にしてください")

    job_id = uuid.uuid4().hex

    # ジョブ登録
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "result_path": None,
        }

    job_dir = UPLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    target_path = job_dir / "target.png"
    zip_path = job_dir / "tiles.zip"

    # アップロード保存（ストリーム）
    try:
        with open(target_path, "wb") as f:
            shutil.copyfileobj(target_image.file, f)
        with open(zip_path, "wb") as f:
            shutil.copyfileobj(tiles_zip.file, f)
    finally:
        target_image.file.close()
        tiles_zip.file.close()

    # バックグラウンドで処理
    t = threading.Thread(
        target=run_job,
        args=(job_id, target_path, zip_path, tile_size, out_width),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        job = _get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
    }


@app.get("/api/jobs/{job_id}/result")
def get_result(job_id: str):
    try:
        job = _get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")

    if job["status"] != "done" or not job["result_path"]:
        raise HTTPException(status_code=404, detail="result not ready")

    path = Path(job["result_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="result file missing")

    return FileResponse(path, media_type="image/jpeg", filename=f"{job_id}.jpg")

