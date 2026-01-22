from __future__ import annotations

import io
import shutil
import threading
import uuid
import zipfile
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
RESULTS_DIR = BASE_DIR / "results"
MATERIALS_DIR = UPLOADS_DIR / "materials"
TARGETS_DIR = UPLOADS_DIR / "targets"

for d in [UPLOADS_DIR, RESULTS_DIR, MATERIALS_DIR, TARGETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ===== 制限（安全のため。必要なら上げてOK）=====
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_ZIP_FILES = 200000          # 素材枚数の上限（上げると処理が長くなる）
MAX_SINGLE_FILE_BYTES = 200 * 1024 * 1024  # 1ファイル200MBまで（メモリ保護）
MAX_THUMBS_DISK_BYTES = 20 * 1024 * 1024 * 1024  # サムネ保存の上限（20GB）
THUMB_SIZE = 64                 # 素材の保存サムネサイズ（小さいほど軽い）
BIN_Q = 8                       # 色量子化幅（8→32bin/chn、軽い）

# ===== ランタイム保持（アプリを閉じるまで保持）=====
jobs: Dict[str, Dict[str, Any]] = {}
materials: Dict[str, Dict[str, Any]] = {}
targets: Dict[str, Dict[str, Any]] = {}

locks = {
    "jobs": threading.Lock(),
    "materials": threading.Lock(),
    "targets": threading.Lock(),
}


def avg_rgb(img: Image.Image) -> Tuple[int, int, int]:
    small = img.resize((1, 1), resample=Image.Resampling.BOX)
    r, g, b = small.getpixel((0, 0))
    return int(r), int(g), int(b)


@lru_cache(maxsize=4096)
def _lut(scale_x100: int) -> list[int]:
    s = scale_x100 / 100.0
    return [max(0, min(255, int(i * s))) for i in range(256)]

def color_match_tile(
    tile_im: Image.Image,
    tile_avg: Tuple[int, int, int],
    target_avg: Tuple[int, int, int],
    strength: float,
) -> Image.Image:
    """
    tile_im を target_avg に寄せる（RGBごとのスケール補正）
    strength: 0.0(無補正)〜1.0(強め)
    """
    if strength <= 0.0:
        return tile_im

    # ratioを使いつつ強さでブレンド（極端になりすぎないようclamp）
    def blend_scale(t: int, a: int) -> float:
        ratio = (t + 1) / (a + 1)  # /0回避
        s = (1.0 - strength) + strength * ratio
        # かけすぎ防止（見た目が破綻しやすいので抑える）
        return max(0.6, min(1.6, s))

    sr = blend_scale(target_avg[0], tile_avg[0])
    sg = blend_scale(target_avg[1], tile_avg[1])
    sb = blend_scale(target_avg[2], tile_avg[2])

    r, g, b = tile_im.split()
    r = r.point(_lut(int(sr * 100)))
    g = g.point(_lut(int(sg * 100)))
    b = b.point(_lut(int(sb * 100)))
    return Image.merge("RGB", (r, g, b))


def color_dist2(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def bin_key(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return (rgb[0] // BIN_Q, rgb[1] // BIN_Q, rgb[2] // BIN_Q)


def _set(store: str, key: str, **kwargs):
    with locks[store]:
        if store == "jobs":
            jobs[key].update(kwargs)
        elif store == "materials":
            materials[key].update(kwargs)
        elif store == "targets":
            targets[key].update(kwargs)


def _get(store: str, key: str) -> Dict[str, Any]:
    with locks[store]:
        if store == "jobs":
            v = jobs.get(key)
        elif store == "materials":
            v = materials.get(key)
        else:
            v = targets.get(key)
        if not v:
            raise KeyError
        return dict(v)


def _list(store: str) -> List[Dict[str, Any]]:
    with locks[store]:
        if store == "jobs":
            return [dict(v) for v in jobs.values()]
        if store == "materials":
            return [dict(v) for v in materials.values()]
        return [dict(v) for v in targets.values()]


def preprocess_material_zip(material_id: str, zip_path: Path):
    """
    ZIP → 画像ごとにサムネ生成 → 平均RGB算出 → 量子化index構築
    ※ 元画像はフル展開しない（＝展開後サイズ問題を回避）
    """
    try:
        mat_dir = MATERIALS_DIR / material_id
        thumbs_dir = mat_dir / "thumbs"
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        _set("materials", material_id, status="processing", progress=0, message="Reading ZIP...")

        tile_paths: List[str] = []
        tile_avgs: List[Tuple[int, int, int]] = []
        index: Dict[Tuple[int, int, int], List[int]] = {}

        written_bytes = 0
        processed = 0

        with zipfile.ZipFile(zip_path, "r") as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]

            if len(infos) > MAX_ZIP_FILES:
                raise ValueError(f"ZIP内ファイル数が多すぎます: {len(infos)} > {MAX_ZIP_FILES}")

            # 進捗用
            total = len(infos) if len(infos) > 0 else 1

            for k, info in enumerate(infos):
                name = info.filename.replace("\\", "/")
                # ZIP Slip対策
                if name.startswith("/") or ".." in name.split("/"):
                    continue

                ext = Path(name).suffix.lower()
                if ext not in ALLOWED_EXT:
                    continue

                if info.file_size > MAX_SINGLE_FILE_BYTES:
                    continue

                # 展開サイズではなく「サムネ保存容量」を上限にする（実質拡張）
                if written_bytes > MAX_THUMBS_DISK_BYTES:
                    break

                # ZipExtFileはseekできない場合があるのでBytesIO化（サイズ上限でメモリ守る）
                with zf.open(info, "r") as f:
                    data = f.read()

                try:
                    with Image.open(io.BytesIO(data)) as im:
                        im = im.convert("RGB")
                        im.thumbnail((THUMB_SIZE, THUMB_SIZE), resample=Image.Resampling.LANCZOS)
                        # 64x64に揃える（不足分は拡大でOK、目的は特徴量+貼り付け用素材）
                        im = im.resize((THUMB_SIZE, THUMB_SIZE), resample=Image.Resampling.LANCZOS)

                        rgb = avg_rgb(im)

                        out_name = f"t_{processed:07d}.jpg"
                        out_path = thumbs_dir / out_name
                        im.save(out_path, quality=85)

                        written_bytes += out_path.stat().st_size

                        tile_paths.append(str(out_path))
                        tile_avgs.append(rgb)

                        b = bin_key(rgb)
                        idx = len(tile_paths) - 1
                        index.setdefault(b, []).append(idx)

                        processed += 1
                except Exception:
                    continue

                if k % 200 == 0:
                    _set("materials", material_id, progress=int((k + 1) / total * 100))

        if processed < 10:
            raise ValueError("素材画像が少なすぎます（有効画像が10枚未満）")

        _set(
            "materials",
            material_id,
            status="ready",
            progress=100,
            message=f"Ready: {processed} tiles",
            tile_paths=tile_paths,
            tile_avgs=tile_avgs,
            index=index,
            count=processed,
        )
    except Exception as e:
        _set("materials", material_id, status="error", message=str(e))


def find_best_tile(
    rgb: Tuple[int, int, int],
    tile_avgs: List[Tuple[int, int, int]],
    index: Dict[Tuple[int, int, int], List[int]],
) -> int:
    """
    量子化ビンから近傍候補を拾い、候補内で正確距離を計算
    """
    br, bg, bb = bin_key(rgb)
    best_i = 0
    best_d = 10**18

    # 半径を広げながら候補を探す
    for radius in range(0, 6):
        cand: List[int] = []
        for dr in range(-radius, radius + 1):
            for dg in range(-radius, radius + 1):
                for db in range(-radius, radius + 1):
                    key = (br + dr, bg + dg, bb + db)
                    if key in index:
                        cand.extend(index[key])

        if cand:
            # 候補の中で最小距離
            for i in cand:
                d = color_dist2(rgb, tile_avgs[i])
                if d < best_d:
                    best_d = d
                    best_i = i
            return best_i

    # 万一候補が取れない場合（極端に偏ったセット等）、フォールバックで全探索（小規模なら問題なし）
    for i, a in enumerate(tile_avgs):
        d = color_dist2(rgb, a)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def find_best_tile_avoid(
    rgb: Tuple[int, int, int],
    tile_avgs: List[Tuple[int, int, int]],
    index: Dict[Tuple[int, int, int], List[int]],
    forbidden: set[int],
) -> int:
    """
    forbidden を避けつつ最近傍タイルを選ぶ（候補の重複を排除して安定化）
    """
    br, bg, bb = bin_key(rgb)

    # ★重複しない候補集合として集める
    candidates: set[int] = set()

    # 半径を広げて候補を増やす（必要ならradius上げてもOK）
    for radius in range(0, 9):  # 0..8
        for dr in range(-radius, radius + 1):
            for dg in range(-radius, radius + 1):
                for db in range(-radius, radius + 1):
                    key = (br + dr, bg + dg, bb + db)
                    if key in index:
                        # ★setに追加（重複ゼロ）
                        candidates.update(index[key])

        # 候補がある程度集まったら打ち切り（速度優先）
        if len(candidates) >= 2000:
            break

    if not candidates:
        return find_best_tile(rgb, tile_avgs, index)

    # 距離でソート（候補が最大2000程度なので全ソートでOK）
    scored = [(color_dist2(rgb, tile_avgs[i]), i) for i in candidates]
    scored.sort(key=lambda x: x[0])

    # 禁止を避けて選ぶ（上位から順に）
    for _, i in scored:
        if i not in forbidden:
            return i

    # 全部禁止なら最良を返す（素材が少なすぎる等）
    return scored[0][1]



@lru_cache(maxsize=512)
def load_tile_cached(path_str: str, tile_size: int) -> Image.Image:
    """
    サムネ(64x64)→必要tile_sizeへリサイズして返す（LRUでI/O削減）
    """
    p = Path(path_str)
    with Image.open(p) as im:
        im = im.convert("RGB")
        if tile_size != THUMB_SIZE:
            im = im.resize((tile_size, tile_size), resample=Image.Resampling.LANCZOS)
        return im


def build_mosaic_exact_size(
    target_path: Path,
    material: Dict[str, Any],
    out_path: Path,
    tile_size: int,
    job_id: str,
    no_repeat_k: int,
    color_strength: float,
    overlay_strength: float,  
):
    _set("jobs", job_id, status="running", progress=0, message="Loading target...")

    with Image.open(target_path) as timg:
        target = timg.convert("RGB")

    W, H = target.size
    out = Image.new("RGB", (W, H))

    tile_paths: List[str] = material["tile_paths"]
    tile_avgs: List[Tuple[int, int, int]] = material["tile_avgs"]
    index: Dict[Tuple[int, int, int], List[int]] = material["index"]

    grid_w = (W + tile_size - 1) // tile_size
    grid_h = (H + tile_size - 1) // tile_size
    total_cells = grid_w * grid_h
    done = 0

    recent = deque(maxlen=max(0, no_repeat_k))
    prev_row: List[Optional[int]] = [None] * grid_w
    left_tile: Optional[int] = None

    _set("jobs", job_id, message="Building mosaic...")

    for gy in range(grid_h):
        y0 = gy * tile_size
        y1 = min(y0 + tile_size, H)
        region_h = y1 - y0

        left_tile = None  # 行が変わるのでリセット

        for gx in range(grid_w):
            x0 = gx * tile_size
            x1 = min(x0 + tile_size, W)
            region_w = x1 - x0

            region = target.crop((x0, y0, x1, y1))
            rgb = avg_rgb(region)

            forbidden: set[int] = set(recent)
            if left_tile is not None:
                forbidden.add(left_tile)
            if prev_row[gx] is not None:
                forbidden.add(prev_row[gx])

            best_i = find_best_tile_avoid(rgb, tile_avgs, index, forbidden)

            tile_im = load_tile_cached(tile_paths[best_i], tile_size)

            # 色補正（忠実度UP）
            if color_strength > 0.0:
                tile_im = color_match_tile(tile_im, tile_avgs[best_i], rgb, color_strength)

            # 端の切り落とし対応
            if region_w != tile_size or region_h != tile_size:
                tile_crop = tile_im.crop((0, 0, region_w, region_h))
                out.paste(tile_crop, (x0, y0))
            else:
                out.paste(tile_im, (x0, y0))

            left_tile = best_i
            prev_row[gx] = best_i
            if no_repeat_k > 0:
                recent.append(best_i)

            done += 1

        _set("jobs", job_id, progress=int(done / total_cells * 99))
    if overlay_strength > 0.0:
        _set("jobs", job_id, message="Blending overlay...", progress=99)
        out = Image.blend(out, target, overlay_strength)  # (1-a)*out + a*target

    _set("jobs", job_id, message="Saving...", progress=99)
    out.save(out_path, quality=92)
    _set("jobs", job_id, status="done", progress=100, message="Done!", result_path=str(out_path))




def run_job(job_id: str, target_path: Path, material_id: str, tile_size: int, no_repeat_k: int, color_strength: float, overlay_strength: float):

    try:
        material = _get("materials", material_id)
        if material["status"] != "ready":
            raise ValueError("素材セットがreadyではありません（processing/errorの可能性）")

        out_path = RESULTS_DIR / f"{job_id}.jpg"
        build_mosaic_exact_size(
            target_path=target_path,
            material=material,
            out_path=out_path,
            tile_size=tile_size,
            job_id=job_id,
            no_repeat_k=no_repeat_k,
            color_strength=color_strength,
            overlay_strength=overlay_strength,
        )

    except Exception as e:
        _set("jobs", job_id, status="error", message=str(e))


# ================== API ==================

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---- Targets（再現画像）保持 ----

@app.post("/api/targets")
async def upload_target(
    image: UploadFile = File(...),
):
    ext = Path(image.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "対応形式は jpg/png/webp です")

    target_id = uuid.uuid4().hex
    tdir = TARGETS_DIR / target_id
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / f"target{ext if ext else '.png'}"

    try:
        with open(path, "wb") as f:
            shutil.copyfileobj(image.file, f)
    finally:
        image.file.close()

    # サイズ取得
    with Image.open(path) as im:
        w, h = im.size

    with locks["targets"]:
        targets[target_id] = {
            "id": target_id,
            "name": image.filename or f"target_{target_id}",
            "path": str(path),
            "width": w,
            "height": h,
        }

    return {"target_id": target_id, "name": targets[target_id]["name"], "width": w, "height": h}


@app.get("/api/targets")
def list_targets():
    return {"targets": _list("targets")}


@app.get("/api/targets/{target_id}/file")
def get_target_file(target_id: str):
    try:
        t = _get("targets", target_id)
    except KeyError:
        raise HTTPException(404, "target not found")
    p = Path(t["path"])
    if not p.exists():
        raise HTTPException(404, "file missing")
    # 拡張子でざっくり
    return FileResponse(p)


@app.delete("/api/targets/{target_id}")
def delete_target(target_id: str):
    try:
        t = _get("targets", target_id)
    except KeyError:
        raise HTTPException(404, "target not found")

    # ファイル削除
    p = Path(t["path"])
    if p.exists():
        shutil.rmtree(p.parent, ignore_errors=True)

    with locks["targets"]:
        targets.pop(target_id, None)

    return {"ok": True}


# ---- Materials（素材セット）保持 ----

@app.post("/api/materials")
async def upload_materials(
    tiles_zip: UploadFile = File(...),
    name: str = Form("materials"),
):
    material_id = uuid.uuid4().hex
    mdir = MATERIALS_DIR / material_id
    mdir.mkdir(parents=True, exist_ok=True)

    zip_path = mdir / "tiles.zip"
    try:
        with open(zip_path, "wb") as f:
            shutil.copyfileobj(tiles_zip.file, f)
    finally:
        tiles_zip.file.close()

    with locks["materials"]:
        materials[material_id] = {
            "id": material_id,
            "name": name,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "count": 0,
            # ready後に入る
            "tile_paths": [],
            "tile_avgs": [],
            "index": {},
        }

    t = threading.Thread(target=preprocess_material_zip, args=(material_id, zip_path), daemon=True)
    t.start()

    return {"material_id": material_id}


@app.get("/api/materials")
def list_materials():
    ms = _list("materials")
    # 重いデータ（tile_paths等）は返さない
    slim = []
    for m in ms:
        slim.append({
            "id": m["id"],
            "name": m["name"],
            "status": m["status"],
            "progress": m.get("progress", 0),
            "message": m.get("message", ""),
            "count": m.get("count", 0),
        })
    return {"materials": slim}


@app.get("/api/materials/{material_id}")
def get_material(material_id: str):
    try:
        m = _get("materials", material_id)
    except KeyError:
        raise HTTPException(404, "material not found")
    return {
        "id": m["id"],
        "name": m["name"],
        "status": m["status"],
        "progress": m.get("progress", 0),
        "message": m.get("message", ""),
        "count": m.get("count", 0),
    }


@app.delete("/api/materials/{material_id}")
def delete_material(material_id: str):
    try:
        m = _get("materials", material_id)
    except KeyError:
        raise HTTPException(404, "material not found")

    # サムネ・zip含め削除
    shutil.rmtree(MATERIALS_DIR / material_id, ignore_errors=True)
    load_tile_cached.cache_clear()

    with locks["materials"]:
        materials.pop(material_id, None)

    return {"ok": True}


# ---- Jobs ----

@app.post("/api/jobs")
async def create_job(
    target_id: str = Form(...),
    material_id: str = Form(...),
    tile_size: int = Form(32),
    no_repeat_k: int = Form(30),
    color_strength: float = Form(0.35),
    overlay_strength: float = Form(0.0),
):
    if tile_size < 8 or tile_size > 128:
        raise HTTPException(400, "tile_sizeは8〜128の範囲にしてください")
    if no_repeat_k < 0 or no_repeat_k > 500:
        raise HTTPException(400, "no_repeat_kは0〜500の範囲にしてください")
    if color_strength < 0.0 or color_strength > 1.0:
        raise HTTPException(400, "color_strengthは0.0〜1.0の範囲にしてください")
    if overlay_strength < 0.0 or overlay_strength > 1.0:
        raise HTTPException(400, "overlay_strengthは0.0〜1.0の範囲にしてください")
    try:
        t = _get("targets", target_id)
    except KeyError:
        raise HTTPException(404, "target not found")

    try:
        m = _get("materials", material_id)
    except KeyError:
        raise HTTPException(404, "material not found")

    if m["status"] != "ready":
        raise HTTPException(400, f"material is not ready: {m['status']}")

    job_id = uuid.uuid4().hex
    with locks["jobs"]:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "result_path": None,
            "target_id": target_id,
            "material_id": material_id,
        }

    target_path = Path(t["path"])

    th = threading.Thread(
        target=run_job,
        args=(job_id, target_path, material_id, tile_size, no_repeat_k, color_strength, overlay_strength),
        daemon=True
    )

    th.start()


    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        j = _get("jobs", job_id)
    except KeyError:
        raise HTTPException(404, "job not found")
    return {
        "job_id": j["id"],
        "status": j["status"],
        "progress": j["progress"],
        "message": j["message"],
    }


@app.get("/api/jobs/{job_id}/result")
def get_result(job_id: str):
    try:
        j = _get("jobs", job_id)
    except KeyError:
        raise HTTPException(404, "job not found")

    if j["status"] != "done" or not j.get("result_path"):
        raise HTTPException(404, "result not ready")

    p = Path(j["result_path"])
    if not p.exists():
        raise HTTPException(404, "result file missing")

    return FileResponse(p, media_type="image/jpeg", filename=f"{job_id}.jpg")
