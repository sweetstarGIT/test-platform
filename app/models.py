"""数据模型"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, Boolean
from app.database import Base


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    package_name = Column(String(255), default="")
    file_type = Column(String(10), default="")  # apk / rpk
    file_size = Column(Integer, default=0)
    file_path = Column(String(500), nullable=False)
    source = Column(String(20), default="upload")  # upload / ci
    created_at = Column(DateTime, default=datetime.now)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    package_id = Column(Integer, nullable=False)
    device_serial = Column(String(100), nullable=False)
    batch_id = Column(String(64), nullable=True)  # 批量任务组ID（同批次共享）
    status = Column(String(20), default="pending")  # pending/running/done/failed/cancelled
    logs = Column(Text, default="[]")
    report_path = Column(String(500), default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    new_package = Column(Boolean, default=False)  # 是否使用新包模式测试


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, nullable=True)  # 单任务报告
    batch_id = Column(String(64), nullable=True)  # 批量汇总报告
    package_name = Column(String(255), default="")
    status = Column(String(20), default="")
    html_path = Column(String(500), default="")
    summary = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)
