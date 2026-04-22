"""全局配置"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
REPORT_DIR = os.path.join(BASE_DIR, "reports")
DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'test_platform.db')}"

# CI 推送用的 API Key（生产环境应从环境变量读取）
API_KEY = os.getenv("TEST_PLATFORM_API_KEY", "tp-dev-key-2026")

# testcase 项目路径（集成现有测试框架）
TESTCASE_PROJECT_DIR = os.getenv(
    "TESTCASE_PROJECT_DIR",
    r"C:\sweetstar\UI-Automation"
)

# 将 testcase 项目加入 Python 路径，使其模块可被 import
if os.path.isdir(TESTCASE_PROJECT_DIR) and TESTCASE_PROJECT_DIR not in sys.path:
    sys.path.insert(0, TESTCASE_PROJECT_DIR)

# 上传后是否自动推送到设备（默认关闭，避免上传大文件时阻塞）
AUTO_PUSH_TO_DEVICE = os.getenv("AUTO_PUSH_TO_DEVICE", "false").lower() == "true"

# 确保目录存在
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
