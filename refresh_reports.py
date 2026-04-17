"""批量刷新历史测试报告样式

用法:
    python refresh_reports.py
"""
import json
import os
import sys

# 确保能导入 app 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models import Task, Package, Report
from app.config import TESTCASE_PROJECT_DIR
from app.services import device_service
from app.services.report_service import generate_html_report, generate_batch_report


def refresh_single_reports(db):
    """刷新单任务报告"""
    reports = db.query(Report).filter(Report.task_id.isnot(None), Report.batch_id.is_(None)).all()
    print(f"[Single] 发现 {len(reports)} 个单任务报告")

    refreshed = 0
    skipped = 0

    for report in reports:
        task = db.query(Task).filter(Task.id == report.task_id).first()
        if not task:
            print(f"  [Skip] 报告 {report.id}: 任务 {report.task_id} 不存在")
            skipped += 1
            continue

        pkg = db.query(Package).filter(Package.id == task.package_id).first()
        if not pkg:
            print(f"  [Skip] 报告 {report.id}: 包不存在")
            skipped += 1
            continue

        # 解析 test_result
        test_result = {}
        if report.summary:
            try:
                test_result = json.loads(report.summary)
            except Exception:
                pass

        # 解析日志
        logs = []
        if task.logs:
            try:
                logs = json.loads(task.logs)
            except Exception:
                pass

        # 获取设备型号
        device_model = ""
        try:
            device_info = device_service.get_device_info(task.device_serial)
            device_model = device_info.get("model", "")
        except Exception:
            pass

        # testcase 报告路径
        testcase_report_path = os.path.join(
            TESTCASE_PROJECT_DIR, "reports", f"report_task_{task.id}.html"
        )

        # 删除旧文件
        if report.html_path and os.path.exists(report.html_path):
            try:
                os.remove(report.html_path)
                print(f"  [Del] 旧报告 {report.html_path}")
            except Exception as e:
                print(f"  [Warn] 删除旧报告失败: {e}")

        # 生成新报告
        try:
            new_path = generate_html_report(
                task_id=task.id,
                pkg=pkg,
                test_result=test_result,
                device_serial=task.device_serial,
                device_model=device_model,
                logs=logs,
                testcase_report_path=testcase_report_path,
            )
        except Exception as e:
            print(f"  [Error] 生成报告失败 task={task.id}: {e}")
            skipped += 1
            continue

        # 更新报告记录
        report.html_path = new_path
        report.status = test_result.get("status", task.status or "unknown")
        task.report_path = new_path
        db.commit()

        print(f"  [OK] 任务 {task.id} -> {new_path}")
        refreshed += 1

    print(f"[Single] 完成: 刷新 {refreshed} 个, 跳过 {skipped} 个\n")
    return refreshed, skipped


def refresh_batch_reports(db):
    """刷新批量汇总报告"""
    reports = db.query(Report).filter(Report.batch_id.isnot(None)).all()
    print(f"[Batch] 发现 {len(reports)} 个批量汇总报告")

    refreshed = 0
    skipped = 0

    for report in reports:
        batch_id = report.batch_id

        # 获取同批次所有任务
        batch_tasks = db.query(Task).filter(Task.batch_id == batch_id).all()
        if not batch_tasks:
            print(f"  [Skip] 批次 {batch_id}: 无任务")
            skipped += 1
            continue

        pkg_results = []
        for t in batch_tasks:
            pkg = db.query(Package).filter(Package.id == t.package_id).first()
            logs = []
            if t.logs:
                try:
                    logs = json.loads(t.logs)
                except Exception:
                    pass
            pkg_results.append({
                "task_id": t.id,
                "package_name": pkg.package_name if pkg else "",
                "filename": pkg.filename if pkg else "",
                "status": t.status,
                "error": t.error or "",
                "logs": logs,
                "started_at": t.started_at.isoformat() if t.started_at else "",
                "finished_at": t.finished_at.isoformat() if t.finished_at else "",
            })

        # 删除旧文件
        if report.html_path and os.path.exists(report.html_path):
            try:
                os.remove(report.html_path)
                print(f"  [Del] 旧批量报告 {report.html_path}")
            except Exception as e:
                print(f"  [Warn] 删除旧报告失败: {e}")

        # 生成新报告
        try:
            new_path = generate_batch_report(batch_id, pkg_results)
        except Exception as e:
            print(f"  [Error] 生成批量报告失败 batch={batch_id}: {e}")
            skipped += 1
            continue

        # 更新记录
        all_done = all(r["status"] == "done" for r in pkg_results)
        any_done = any(r["status"] == "done" for r in pkg_results)
        overall_status = "success" if all_done else ("partial" if any_done else "failed")

        report.html_path = new_path
        report.status = overall_status
        db.commit()

        print(f"  [OK] 批次 {batch_id} -> {new_path}")
        refreshed += 1

    print(f"[Batch] 完成: 刷新 {refreshed} 个, 跳过 {skipped} 个\n")
    return refreshed, skipped


def main():
    db = SessionLocal()
    try:
        s_refreshed, s_skipped = refresh_single_reports(db)
        b_refreshed, b_skipped = refresh_batch_reports(db)

        total = s_refreshed + b_refreshed
        print(f"=" * 40)
        print(f"全部完成: 共刷新 {total} 个报告")
        print(f"  - 单任务: {s_refreshed} 个")
        print(f"  - 批量:   {b_refreshed} 个")
        if s_skipped + b_skipped > 0:
            print(f"  - 跳过:   {s_skipped + b_skipped} 个")
    finally:
        db.close()


if __name__ == "__main__":
    main()
