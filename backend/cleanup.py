import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sqlalchemy import select, delete

from .db import SessionLocal
from .models import Session as SessionModel, Target, Material, Job
from .settings import MATERIALS_DIR, TARGETS_DIR

def _safe_rmtree(p: Path) -> None:
    shutil.rmtree(p, ignore_errors=True)

def _safe_unlink(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass

def purge_session_files(db, session_id: str) -> None:
    # targets
    t_rows = db.execute(select(Target).where(Target.session_id == session_id)).scalars().all()
    for t in t_rows:
        tp = Path(t.path)
        # targetは targets/<id>/target.xxx の想定 → 親ディレクトリごと消す
        _safe_rmtree(tp.parent)

    # materials
    m_rows = db.execute(select(Material).where(Material.session_id == session_id)).scalars().all()
    for m in m_rows:
        _safe_rmtree(MATERIALS_DIR / m.id)

    # jobs/results
    j_rows = db.execute(select(Job).where(Job.session_id == session_id)).scalars().all()
    for j in j_rows:
        if j.result_path:
            _safe_unlink(Path(j.result_path))

def delete_session_everything(session_id: str) -> None:
    with SessionLocal() as db:
        purge_session_files(db, session_id)

        db.execute(delete(Job).where(Job.session_id == session_id))
        db.execute(delete(Material).where(Material.session_id == session_id))
        db.execute(delete(Target).where(Target.session_id == session_id))
        db.execute(delete(SessionModel).where(SessionModel.id == session_id))
        db.commit()

def cleanup_expired_sessions(ttl_minutes: int) -> int:
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=ttl_minutes)

    expired_ids: list[str] = []
    with SessionLocal() as db:
        expired = db.execute(
            select(SessionModel.id).where(SessionModel.last_seen < deadline)
        ).all()
        expired_ids = [row[0] for row in expired]

    for sid in expired_ids:
        delete_session_everything(sid)

    return len(expired_ids)
