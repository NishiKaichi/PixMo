from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from .db import Base

def utcnow():
    return datetime.now(timezone.utc)

class Session(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    targets = relationship("Target", back_populates="session", cascade="all, delete-orphan")
    materials = relationship("Material", back_populates="session", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="session", cascade="all, delete-orphan")

class Target(Base):
    __tablename__ = "targets"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id"), index=True, nullable=False)

    name = Column(String, nullable=False)
    path = Column(String, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    session = relationship("Session", back_populates="targets")

class Material(Base):
    __tablename__ = "materials"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id"), index=True, nullable=False)

    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    message = Column(String, nullable=False, default="Queued")
    count = Column(Integer, nullable=False, default=0)

    # 生成済みサムネ/メタ情報の場所（ファイルはセッション削除で消える）
    zip_path = Column(String, nullable=True)   # tiles.zip（処理後は消す想定）
    meta_path = Column(String, nullable=True)  # meta.json

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    session = relationship("Session", back_populates="materials")

class Job(Base):
    __tablename__ = "jobs"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id"), index=True, nullable=False)

    target_id = Column(String, nullable=False)
    material_id = Column(String, nullable=False)

    status = Column(String, nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    message = Column(String, nullable=False, default="Queued")
    result_path = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    session = relationship("Session", back_populates="jobs")
