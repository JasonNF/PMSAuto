from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine, func
from sqlalchemy.orm import declarative_base, sessionmaker

DB_URL = "sqlite:///./emby_admin.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserAccount(Base):
    __tablename__ = "user_accounts"
    id = Column(Integer, primary_key=True, index=True)
    emby_user_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    tg_id = Column(String, nullable=True)
    status = Column(String, default="active")  # active | archived
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, onupdate=func.now(), default=func.now())

class RenewalCode(Base):
    __tablename__ = "renewal_codes"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    days = Column(Integer, nullable=False)
    expired_at = Column(DateTime, nullable=True)
    redeemed_by = Column(String, nullable=True)  # emby_user_id
    redeemed_at = Column(DateTime, nullable=True)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=True)  # who triggered
    target = Column(String, nullable=True) # target user or code
    detail = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


def init_db():
    Base.metadata.create_all(bind=engine)
