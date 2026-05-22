"""CI 集成接口 — 供打包流程查询批量测试结果"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Task, Package, Report

router = APIRouter()


@router.get("/batch/{batch_id}")
def get_batch_result(batch_id: str, db: Session = Depends(get_db)):
    """查询批量任务的测试结果

    参数:
        batch_id: 批量任务ID（在 test-platform 上创建批量任务时生成）

    返回:
        {
            "batch_id": "abc123",
            "total": 5,
            "passed": 3,
            "failed": 2,
            "all_passed": false,
            "packages": [
                { "name": "包A", "filename": "A.rpk", "status": "done", "passed": true },
                { "name": "包B", "filename": "B.rpk", "status": "failed", "passed": false, "reason": "..." }
            ],
            "failed_packages": [
                { "name": "包B", "filename": "B.rpk", "reason": "..." }
            ]
        }
    """
    tasks = (
        db.query(Task)
        .filter(Task.batch_id == batch_id)
        .order_by(Task.created_at)
        .all()
    )

    if not tasks:
        raise HTTPException(404, "批量任务不存在")

    # 如果还有任务没跑完，返回状态
    unfinished = [t for t in tasks if t.status in ("pending", "running")]
    if unfinished:
        return {
            "batch_id": batch_id,
            "status": "running",
            "total": len(tasks),
            "finished": len(tasks) - len(unfinished),
            "message": f"还有 {len(unfinished)} 个包正在测试中",
            "packages": [],
            "failed_packages": [],
        }

    # 全部跑完了，汇总结果
    packages_result = []
    failed_list = []

    for task in tasks:
        pkg = db.query(Package).filter(Package.id == task.package_id).first()
        pkg_name = pkg.package_name if pkg else ""
        filename = pkg.filename if pkg else ""
        passed = task.status == "done"

        item = {
            "name": pkg_name,
            "filename": filename,
            "status": task.status,
            "passed": passed,
            "reason": task.error if not passed else None,
        }
        packages_result.append(item)

        if not passed:
            failed_list.append({
                "name": pkg_name,
                "filename": filename,
                "reason": task.error or "测试失败",
            })

    total = len(tasks)
    passed_count = total - len(failed_list)

    return {
        "batch_id": batch_id,
        "status": "completed",
        "total": total,
        "passed": passed_count,
        "failed": len(failed_list),
        "all_passed": len(failed_list) == 0,
        "packages": packages_result,
        "failed_packages": failed_list,
    }


@router.get("/batch/{batch_id}/failed")
def get_batch_failed(batch_id: str, db: Session = Depends(get_db)):
    """只返回批量任务中失败的包（更简洁）

    返回:
        {
            "batch_id": "abc123",
            "failed_count": 2,
            "packages": [
                { "name": "包B", "filename": "B.rpk", "reason": "..." }
            ]
        }
    """
    tasks = (
        db.query(Task)
        .filter(Task.batch_id == batch_id, Task.status != "done")
        .all()
    )

    failed_list = []
    for task in tasks:
        pkg = db.query(Package).filter(Package.id == task.package_id).first()
        failed_list.append({
            "name": pkg.package_name if pkg else "",
            "filename": pkg.filename if pkg else "",
            "reason": task.error or "测试失败",
        })

    return {
        "batch_id": batch_id,
        "failed_count": len(failed_list),
        "packages": failed_list,
    }
