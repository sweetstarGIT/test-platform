"""Focused coverage for package, task and CI list pagination contracts."""
from collections import Counter
from datetime import datetime, timedelta
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import CiJob, Package, Task
from app.routers import ci, packages, tasks


class OperationalListPaginationApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        created_at = datetime(2026, 1, 1, 9, 0, 0)

        self.db.add_all(
            [
                Package(
                    filename=f"package-{index}.rpk",
                    package_name=f"com.example.package{index}",
                    file_type="rpk",
                    file_size=1024 + index,
                    file_path=f"package-{index}.rpk",
                    created_at=created_at + timedelta(seconds=index),
                )
                for index in range(55)
            ]
        )
        self.db.commit()
        packages_by_id = self.db.query(Package).order_by(Package.id).all()

        task_statuses = ["pending", "running", "done", "failed", "cancelled"]
        self.task_status_counts = Counter()
        task_rows = []
        for index in range(55):
            status = task_statuses[index % len(task_statuses)]
            self.task_status_counts[status] += 1
            task_rows.append(
                Task(
                    package_id=packages_by_id[index].id,
                    device_serial=f"device-{index}",
                    status=status,
                    created_at=created_at + timedelta(minutes=index),
                )
            )
        self.db.add_all(task_rows)

        ci_statuses = ["received", "running", "done", "failed", "cancelled"]
        self.ci_status_counts = Counter()
        ci_rows = []
        for index in range(105):
            status = ci_statuses[index % len(ci_statuses)]
            self.ci_status_counts[status] += 1
            ci_rows.append(
                CiJob(
                    external_task_id=f"external-{index}",
                    package_name=f"com.example.ci{index}",
                    artifact_url=f"https://example.test/build-{index}.zip",
                    status=status,
                    current_step=status,
                    events="[]",
                    created_at=created_at + timedelta(hours=index),
                    updated_at=created_at + timedelta(hours=index),
                )
            )
        self.db.add_all(ci_rows)
        self.db.commit()

        self.app = FastAPI()
        self.app.include_router(packages.router, prefix="/api/packages")
        self.app.include_router(tasks.router, prefix="/api/tasks")
        self.app.include_router(ci.router, prefix="/api/ci")

        def get_test_db():
            yield self.db

        self.app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_packages_support_pagination_and_keep_legacy_array_response(self):
        legacy = self.client.get("/api/packages")
        self.assertEqual(legacy.status_code, 200)
        self.assertIsInstance(legacy.json(), list)
        self.assertEqual(len(legacy.json()), 55)

        first_page = self.client.get("/api/packages?page=1&page_size=20").json()
        second_page = self.client.get("/api/packages?page=2&page_size=20").json()
        self.assertEqual(first_page["total"], 55)
        self.assertEqual(first_page["total_pages"], 3)
        self.assertEqual(first_page["items"][0]["package_name"], "com.example.package54")
        self.assertEqual(second_page["items"][0]["package_name"], "com.example.package34")
        self.assertFalse({item["id"] for item in first_page["items"]} & {item["id"] for item in second_page["items"]})

        clamped_page = self.client.get("/api/packages?page=999&page_size=20").json()
        self.assertEqual(clamped_page["page"], 3)
        self.assertEqual(len(clamped_page["items"]), 15)

    def test_tasks_return_global_status_counts_and_legacy_limit(self):
        legacy = self.client.get("/api/tasks")
        self.assertEqual(legacy.status_code, 200)
        self.assertIsInstance(legacy.json(), list)
        self.assertEqual(len(legacy.json()), 50)

        payload = self.client.get("/api/tasks?page=3&page_size=20").json()
        self.assertEqual(payload["total"], 55)
        self.assertEqual(payload["page"], 3)
        self.assertEqual(len(payload["items"]), 15)
        self.assertEqual(payload["status_counts"], dict(self.task_status_counts))
        self.assertEqual(payload["items"][0]["package_name"], "com.example.package14")

        clamped_page = self.client.get("/api/tasks?page=999&page_size=20").json()
        self.assertEqual(clamped_page["page"], 3)
        self.assertEqual(len(clamped_page["items"]), 15)

    def test_ci_jobs_keep_legacy_limit_and_clamp_out_of_range_pages(self):
        legacy = self.client.get("/api/ci/jobs")
        self.assertEqual(legacy.status_code, 200)
        self.assertIsInstance(legacy.json(), list)
        self.assertEqual(len(legacy.json()), 100)

        payload = self.client.get("/api/ci/jobs?page=999&page_size=20").json()
        self.assertEqual(payload["total"], 105)
        self.assertEqual(payload["page"], 6)
        self.assertEqual(payload["total_pages"], 6)
        self.assertEqual(len(payload["items"]), 5)
        self.assertEqual(payload["status_counts"], dict(self.ci_status_counts))
        self.assertEqual(payload["items"][0]["external_task_id"], "external-4")

    def test_rejects_invalid_pagination_parameters(self):
        self.assertEqual(self.client.get("/api/packages?page=0").status_code, 422)
        self.assertEqual(self.client.get("/api/tasks?page_size=101").status_code, 422)
        self.assertEqual(self.client.get("/api/ci/jobs?page=0").status_code, 422)


if __name__ == "__main__":
    unittest.main()
