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


class UserPref(Base):
    __tablename__ = "user_prefs"
    id = Column(Integer, primary_key=True, index=True)
    emby_user_id = Column(String, unique=True, index=True, nullable=False)
    notify_opt_out = Column(Boolean, default=False)  # True 表示用户关闭每日通知
    updated_at = Column(DateTime, onupdate=func.now(), default=func.now())


class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    emby_user_id = Column(String, index=True, nullable=False)
    date = Column(String, index=True, nullable=False)  # 格式 YYYY-MM-DD（服务器本地时区）
    seconds_total = Column(Integer, default=0)
    donation_total = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now(), default=func.now())


class DonationStat(Base):
    __tablename__ = "donation_stats"
    id = Column(Integer, primary_key=True, index=True)
    emby_user_id = Column(String, unique=True, index=True, nullable=False)
    amount_total = Column(Integer, default=0)  # 金额单位：可用元或积分换算前的金额（按你的口径为整数金额）
    updated_at = Column(DateTime, onupdate=func.now(), default=func.now())

class RenewalCode(Base):
    __tablename__ = "renewal_codes"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    days = Column(Integer, nullable=False)
    expired_at = Column(DateTime, nullable=True)
    redeemed_by = Column(String, nullable=True)  # emby_user_id
    redeemed_at = Column(DateTime, nullable=True)


class WatchStat(Base):
    __tablename__ = "watch_stats"
    id = Column(Integer, primary_key=True, index=True)
    emby_user_id = Column(String, unique=True, index=True, nullable=False)
    seconds_total = Column(Integer, default=0)
    updated_at = Column(DateTime, onupdate=func.now(), default=func.now())

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
