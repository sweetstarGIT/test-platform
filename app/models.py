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
    external_task_id = Column(String(128), nullable=True)  # 开发平台任务ID
    external_status = Column(String(50), default="")  # 开发平台推送过来的任务状态
    artifact_url = Column(Text, default="")  # 开发平台包下载地址
    callback_url = Column(String(500), default="")  # 测试完成后的回调地址
    callback_sent = Column(Boolean, default=False)  # 是否已回调开发平台
    callback_error = Column(Text, default="")  # 最近一次回调失败原因


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


class CiJob(Base):
    __tablename__ = "ci_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_task_id = Column(String(128), nullable=False)
    package_name = Column(String(255), default="")
    artifact_url = Column(Text, default="")
    artifact_filename = Column(String(255), default="")
    artifact_type = Column(String(20), default="")  # zip / rpk / apk
    task_status = Column(String(50), default="")
    status = Column(String(30), default="received")  # received/downloading/extracting/queued/running/done/failed/cancelled
    current_step = Column(String(50), default="received")
    device_serial = Column(String(100), default="")
    package_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    report_id = Column(Integer, nullable=True)
    callback_url = Column(String(500), default="")
    callback_sent = Column(Boolean, default=False)
    callback_error = Column(Text, default="")
    error = Column(Text, default="")
    new_package = Column(Boolean, default=True)
    events = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)
