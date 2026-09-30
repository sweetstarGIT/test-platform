"""Focused coverage for the report-list pagination contract."""
from datetime import datetime, timedelta
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Report
from app.routers.reports import router


class ReportPaginationApiTests(unittest.TestCase):
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
                Report(
                    package_name=f"com.example.report{index}",
                    status="success",
                    html_path=f"report_{index}.html",
                    created_at=created_at + timedelta(seconds=index),
                )
                for index in range(45)
            ]
        )
        self.db.commit()

        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/reports")

        def get_test_db():
            yield self.db

        self.app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_returns_metadata_and_newest_first_page(self):
        response = self.client.get("/api/reports?page=1&page_size=20")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 45)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 20)
        self.assertEqual(payload["total_pages"], 3)
        self.assertEqual(len(payload["items"]), 20)
        self.assertEqual(payload["items"][0]["package_name"], "com.example.report44")
        self.assertEqual(payload["items"][-1]["package_name"], "com.example.report25")

    def test_returns_a_non_overlapping_later_page(self):
        first_page = self.client.get("/api/reports?page=1&page_size=20").json()
        second_page = self.client.get("/api/reports?page=2&page_size=20").json()

        first_ids = {item["id"] for item in first_page["items"]}
        second_ids = {item["id"] for item in second_page["items"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(second_page["page"], 2)
        self.assertEqual(second_page["items"][0]["package_name"], "com.example.report24")
        self.assertEqual(second_page["items"][-1]["package_name"], "com.example.report5")

    def test_clamps_an_out_of_range_page_to_the_last_page(self):
        response = self.client.get("/api/reports?page=999&page_size=20")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"], 3)
        self.assertEqual(payload["total_pages"], 3)
        self.assertEqual(len(payload["items"]), 5)
        self.assertEqual(payload["items"][0]["package_name"], "com.example.report4")
        self.assertEqual(payload["items"][-1]["package_name"], "com.example.report0")

    def test_rejects_invalid_page_parameters(self):
        self.assertEqual(self.client.get("/api/reports?page=0").status_code, 422)
        self.assertEqual(self.client.get("/api/reports?page_size=101").status_code, 422)


if __name__ == "__main__":
    unittest.main()
