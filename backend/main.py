from __future__ import annotations

import io
import json
import shutil
import threading
import uuid
import zipfile
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header,Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image

from .db import init_db, SessionLocal
from .models import Session as SessionModel, Target as TargetModel, Material as MaterialModel, Job as JobModel
from .cleanup import delete_session_everything, cleanup_expired_sessions
from .settings import (
    UPLOADS_DIR, RESULTS_DIR, MATERIALS_DIR, TARGETS_DIR,
    ALLOWED_EXT, MAX_ZIP_FILES, MAX_SINGLE_FILE_BYTES, MAX_THUMBS_DISK_BYTES,
    THUMB_SIZE, BIN_Q,
    SESSION_TTL_MINUTES, CLEANUP_INTERVAL_SECONDS,
)

app = FastAPI()

# ===== ランタイム保持（同一サーバプロセス中の高速化用キャッシュ）=====
jobs: Dict[str, Dict[str, Any]] = {}
materials: Dict[str, Dict[str, Any]] = {}
targets: Dict[str, Dict[str, Any]] = {}

locks = {
    "jobs": threading.Lock(),
    "materials": threading.Lock(),
    "targets": threading.Lock(),
}


# ===== セッション =====
LEGACY_SESSION_ID = "legacy"

def _touch_session(session_id: str) -> None:
    # last_seen更新＆存在しなければ作る
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        s = db.get(SessionModel, session_id)
        if s is None:
            s = SessionModel(id=session_id, created_at=now, last_seen=now)
            db.add(s)
        else:
            s.last_seen = now
        db.commit()

def get_session_id(x_session_id: str | None = Header(default=None, alias="X-Session-Id")) -> str:
    sid = x_session_id or LEGACY_SESSION_ID
    _touch_session(sid)
    return sid


# ===== 画像処理 =====
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
    if strength <= 0.0:
        return tile_im

    def blend_scale(t: int, a: int) -> float:
        ratio = (t + 1) / (a + 1)
        s = (1.0 - strength) + strength * ratio
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

def _purge_in_memory_by_session(session_id: str) -> None:
    with locks["targets"]:
        for k in [k for k, v in targets.items() if v.get("session_id") == session_id]:
            targets.pop(k, None)
    with locks["materials"]:
        for k in [k for k, v in materials.items() if v.get("session_id") == session_id]:
            materials.pop(k, None)
    with locks["jobs"]:
        for k in [k for k, v in jobs.items() if v.get("session_id") == session_id]:
            jobs.pop(k, None)


# ===== Materials preprocess =====
def _write_material_meta(mat_dir: Path, tile_paths, tile_avgs, index) -> Path:
    # indexのkeyがtupleなのでJSON化（"r,g,b" 文字列にする）
    index_json = {f"{k[0]},{k[1]},{k[2]}": v for k, v in index.items()}
    meta = {
        "tile_paths": tile_paths,
        "tile_avgs": tile_avgs,
        "index": index_json,
    }
    meta_path = mat_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    return meta_path

def preprocess_material_zip(session_id: str, material_id: str, zip_path: Path):
    """
    ZIP → サムネ生成 → 平均RGB算出 → 量子化index構築
    処理後、tiles.zip は削除してストレージを回収する
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

            total = len(infos) if len(infos) > 0 else 1

            for k, info in enumerate(infos):
                # セッションが消されてたら中断（閉じた/TTL）
                with SessionLocal() as db:
                    if db.get(SessionModel, session_id) is None:
                        return

                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    continue

                ext = Path(name).suffix.lower()
                if ext not in ALLOWED_EXT:
                    continue

                if info.file_size > MAX_SINGLE_FILE_BYTES:
                    continue

                if written_bytes > MAX_THUMBS_DISK_BYTES:
                    break

                with zf.open(info, "r") as f:
                    data = f.read()

                try:
                    with Image.open(io.BytesIO(data)) as im:
                        im = im.convert("RGB")
                        im.thumbnail((THUMB_SIZE, THUMB_SIZE), resample=Image.Resampling.LANCZOS)
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
                    prog = int((k + 1) / total * 100)
                    _set("materials", material_id, progress=prog)
                    with SessionLocal() as db:
                        m = db.get(MaterialModel, material_id)
                        if m:
                            m.status = "processing"
                            m.progress = prog
                            m.message = "Processing..."
                            db.commit()

        if processed < 10:
            raise ValueError("素材画像が少なすぎます（有効画像が10枚未満）")

        meta_path = _write_material_meta(mat_dir, tile_paths, tile_avgs, index)

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

        # DB更新
        with SessionLocal() as db:
            m = db.get(MaterialModel, material_id)
            if m:
                m.status = "ready"
                m.progress = 100
                m.message = f"Ready: {processed} tiles"
                m.count = processed
                m.meta_path = str(meta_path)
                db.commit()

    except Exception as e:
        _set("materials", material_id, status="error", message=str(e))
        with SessionLocal() as db:
            m = db.get(MaterialModel, material_id)
            if m:
                m.status = "error"
                m.message = str(e)
                db.commit()

    finally:
        # ★ここがストレージ回収の要：tiles.zipは処理後消す（成功/失敗問わず）
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass


# ===== タイル選択 =====
def find_best_tile(
    rgb: Tuple[int, int, int],
    tile_avgs: List[Tuple[int, int, int]],
    index: Dict[Tuple[int, int, int], List[int]],
) -> int:
    br, bg, bb = bin_key(rgb)
    best_i = 0
    best_d = 10**18

    for radius in range(0, 6):
        cand: List[int] = []
        for dr in range(-radius, radius + 1):
            for dg in range(-radius, radius + 1):
                for dbb in range(-radius, radius + 1):
                    key = (br + dr, bg + dg, bb + dbb)
                    if key in index:
                        cand.extend(index[key])

        if cand:
            for i in cand:
                d = color_dist2(rgb, tile_avgs[i])
                if d < best_d:
                    best_d = d
                    best_i = i
            return best_i

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
    br, bg, bb = bin_key(rgb)
    candidates: set[int] = set()

    for radius in range(0, 9):
        for dr in range(-radius, radius + 1):
            for dg in range(-radius, radius + 1):
                for dbb in range(-radius, radius + 1):
                    key = (br + dr, bg + dg, bb + dbb)
                    if key in index:
                        candidates.update(index[key])
        if len(candidates) >= 2000:
            break

    if not candidates:
        return find_best_tile(rgb, tile_avgs, index)

    scored = [(color_dist2(rgb, tile_avgs[i]), i) for i in candidates]
    scored.sort(key=lambda x: x[0])

    for _, i in scored:
        if i not in forbidden:
            return i
    return scored[0][1]


@lru_cache(maxsize=512)
def load_tile_cached(path_str: str, tile_size: int) -> Image.Image:
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

        left_tile = None

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

            if color_strength > 0.0:
                tile_im = color_match_tile(tile_im, tile_avgs[best_i], rgb, color_strength)

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
        out = Image.blend(out, target, overlay_strength)

    _set("jobs", job_id, message="Saving...", progress=99)
    out.save(out_path, quality=92)
    _set("jobs", job_id, status="done", progress=100, message="Done!", result_path=str(out_path))


def run_job(session_id: str, job_id: str, target_path: Path, material_id: str, tile_size: int, no_repeat_k: int, color_strength: float, overlay_strength: float):
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

        with SessionLocal() as db:
            j = db.get(JobModel, job_id)
            if j:
                j.status = "done"
                j.progress = 100
                j.message = "Done!"
                j.result_path = str(out_path)
                db.commit()

    except Exception as e:
        _set("jobs", job_id, status="error", message=str(e))
        with SessionLocal() as db:
            j = db.get(JobModel, job_id)
            if j:
                j.status = "error"
                j.message = str(e)
                db.commit()


# ================== API ==================
class SessionCloseRequest(BaseModel):
    session_id: str

@app.on_event("startup")
def _startup():
    init_db()

    # TTL cleanup worker
    def worker():
        while True:
            try:
                cleanup_expired_sessions(SESSION_TTL_MINUTES)
            except Exception:
                pass
            import time
            time.sleep(CLEANUP_INTERVAL_SECONDS)

    t = threading.Thread(target=worker, daemon=True)
    t.start()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/session/close")
def close_session(body: SessionCloseRequest):
    # DB + ファイル削除
    delete_session_everything(body.session_id)
    # メモリキャッシュも掃除
    _purge_in_memory_by_session(body.session_id)
    load_tile_cached.cache_clear()
    return {"ok": True}


# ---- Targets ----
@app.post("/api/targets")
async def upload_target(
    image: UploadFile = File(...),
    session_id: str = Header(default=None, alias="X-Session-Id"),
):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

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

    with Image.open(path) as im:
        w, h = im.size

    with SessionLocal() as db:
        db.merge(TargetModel(
            id=target_id,
            session_id=sid,
            name=image.filename or f"target_{target_id}",
            path=str(path),
            width=w,
            height=h,
        ))
        db.commit()

    with locks["targets"]:
        targets[target_id] = {
            "id": target_id,
            "session_id": sid,
            "name": image.filename or f"target_{target_id}",
            "path": str(path),
            "width": w,
            "height": h,
        }

    return {"target_id": target_id, "name": targets[target_id]["name"], "width": w, "height": h}


@app.get("/api/targets")
def list_targets(session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    with SessionLocal() as db:
        rows = db.query(TargetModel).filter(TargetModel.session_id == sid).all()

    return {"targets": [
        {"id": r.id, "name": r.name, "path": r.path, "width": r.width, "height": r.height}
        for r in rows
    ]}


@app.get("/api/targets/{target_id}/file")
def get_target_file(
    target_id: str,
    sid: str | None = Query(default=None),
    session_id: str = Header(default=None, alias="X-Session-Id"),
):
    effective_sid = sid or session_id or LEGACY_SESSION_ID
    _touch_session(effective_sid)

    with SessionLocal() as db:
        r = db.query(TargetModel).filter(
            TargetModel.id == target_id,
            TargetModel.session_id == effective_sid
        ).first()
        if not r:
            raise HTTPException(404, "target not found")

    p = Path(r.path)
    if not p.exists():
        raise HTTPException(404, "file missing")
    return FileResponse(p)



@app.delete("/api/targets/{target_id}")
def delete_target(target_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    with SessionLocal() as db:
        r = db.query(TargetModel).filter(TargetModel.id == target_id, TargetModel.session_id == sid).first()
        if not r:
            raise HTTPException(404, "target not found")

        p = Path(r.path)
        if p.exists():
            shutil.rmtree(p.parent, ignore_errors=True)

        db.delete(r)
        db.commit()

    with locks["targets"]:
        targets.pop(target_id, None)

    return {"ok": True}


# ---- Materials ----
@app.post("/api/materials")
async def upload_materials(
    tiles_zip: UploadFile = File(...),
    name: str = Form("materials"),
    session_id: str = Header(default=None, alias="X-Session-Id"),
):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    material_id = uuid.uuid4().hex
    mdir = MATERIALS_DIR / material_id
    mdir.mkdir(parents=True, exist_ok=True)

    zip_path = mdir / "tiles.zip"
    try:
        with open(zip_path, "wb") as f:
            shutil.copyfileobj(tiles_zip.file, f)
    finally:
        tiles_zip.file.close()

    with SessionLocal() as db:
        db.merge(MaterialModel(
            id=material_id,
            session_id=sid,
            name=name,
            status="queued",
            progress=0,
            message="Queued",
            count=0,
            zip_path=str(zip_path),
            meta_path=None,
        ))
        db.commit()

    with locks["materials"]:
        materials[material_id] = {
            "id": material_id,
            "session_id": sid,
            "name": name,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "count": 0,
            "tile_paths": [],
            "tile_avgs": [],
            "index": {},
        }

    t = threading.Thread(target=preprocess_material_zip, args=(sid, material_id, zip_path), daemon=True)
    t.start()

    return {"material_id": material_id}


@app.get("/api/materials")
def list_materials(session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    with SessionLocal() as db:
        rows = db.query(MaterialModel).filter(MaterialModel.session_id == sid).all()

    return {"materials": [
        {
            "id": r.id,
            "name": r.name,
            "status": r.status,
            "progress": r.progress,
            "message": r.message,
            "count": r.count,
        }
        for r in rows
    ]}


@app.get("/api/materials/{material_id}")
def get_material(material_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    with SessionLocal() as db:
        r = db.query(MaterialModel).filter(MaterialModel.id == material_id, MaterialModel.session_id == sid).first()
        if not r:
            raise HTTPException(404, "material not found")

    return {
        "id": r.id,
        "name": r.name,
        "status": r.status,
        "progress": r.progress,
        "message": r.message,
        "count": r.count,
    }


@app.delete("/api/materials/{material_id}")
def delete_material(material_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    with SessionLocal() as db:
        r = db.query(MaterialModel).filter(MaterialModel.id == material_id, MaterialModel.session_id == sid).first()
        if not r:
            raise HTTPException(404, "material not found")
        db.delete(r)
        db.commit()

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
    session_id: str = Header(default=None, alias="X-Session-Id"),
):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    if tile_size < 8 or tile_size > 256:
        raise HTTPException(400, "tile_sizeは8〜256の範囲にしてください")
    if no_repeat_k < 0 or no_repeat_k > 500:
        raise HTTPException(400, "no_repeat_kは0〜500の範囲にしてください")
    if color_strength < 0.0 or color_strength > 1.0:
        raise HTTPException(400, "color_strengthは0.0〜1.0の範囲にしてください")
    if overlay_strength < 0.0 or overlay_strength > 1.0:
        raise HTTPException(400, "overlay_strengthは0.0〜1.0の範囲にしてください")

    # DBで所有確認
    with SessionLocal() as db:
        t = db.query(TargetModel).filter(TargetModel.id == target_id, TargetModel.session_id == sid).first()
        if not t:
            raise HTTPException(404, "target not found")

        m = db.query(MaterialModel).filter(MaterialModel.id == material_id, MaterialModel.session_id == sid).first()
        if not m:
            raise HTTPException(404, "material not found")
        if m.status != "ready":
            raise HTTPException(400, f"material is not ready: {m.status}")

    # メモリキャッシュに素材が無ければmeta.jsonから復元
    try:
        mat_cache = _get("materials", material_id)
    except KeyError:
        mat_dir = MATERIALS_DIR / material_id
        meta_path = mat_dir / "meta.json"
        if not meta_path.exists():
            raise HTTPException(400, "material cache missing (please re-upload materials)")
        meta = json.loads(meta_path.read_text())
        tile_paths = meta["tile_paths"]
        tile_avgs = [tuple(x) for x in meta["tile_avgs"]]
        index = {}
        for k, v in meta["index"].items():
            r, g, b = k.split(",")
            index[(int(r), int(g), int(b))] = v
        with locks["materials"]:
            materials[material_id] = {
                "id": material_id,
                "session_id": sid,
                "name": m.name if "m" in locals() else "materials",
                "status": "ready",
                "progress": 100,
                "message": "Ready (restored)",
                "count": len(tile_paths),
                "tile_paths": tile_paths,
                "tile_avgs": tile_avgs,
                "index": index,
            }

    job_id = uuid.uuid4().hex
    with locks["jobs"]:
        jobs[job_id] = {
            "id": job_id,
            "session_id": sid,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "result_path": None,
            "target_id": target_id,
            "material_id": material_id,
        }

    with SessionLocal() as db:
        db.merge(JobModel(
            id=job_id,
            session_id=sid,
            status="queued",
            progress=0,
            message="Queued",
            result_path=None,
            target_id=target_id,
            material_id=material_id,
        ))
        db.commit()

    target_path = Path(t.path)

    th = threading.Thread(
        target=run_job,
        args=(sid, job_id, target_path, material_id, tile_size, no_repeat_k, color_strength, overlay_strength),
        daemon=True
    )
    th.start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, session_id: str = Header(default=None, alias="X-Session-Id")):
    sid = session_id or LEGACY_SESSION_ID
    _touch_session(sid)

    with SessionLocal() as db:
        j = db.query(JobModel).filter(JobModel.id == job_id, JobModel.session_id == sid).first()
        if not j:
            raise HTTPException(404, "job not found")
    return {"job_id": j.id, "status": j.status, "progress": j.progress, "message": j.message}


@app.get("/api/jobs/{job_id}/result")
def get_result(
    job_id: str,
    sid: str | None = Query(default=None),
    session_id: str = Header(default=None, alias="X-Session-Id"),
):
    effective_sid = sid or session_id or LEGACY_SESSION_ID
    _touch_session(effective_sid)

    with SessionLocal() as db:
        j = db.query(JobModel).filter(
            JobModel.id == job_id,
            JobModel.session_id == effective_sid
        ).first()
        if not j or j.status != "done" or not j.result_path:
            raise HTTPException(404, "result not ready")

    p = Path(j.result_path)
    if not p.exists():
        raise HTTPException(404, "result file missing")
    return FileResponse(p, media_type="image/jpeg", filename=f"{job_id}.jpg")
