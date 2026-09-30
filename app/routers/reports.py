"""报告查看路由"""
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Report

router = APIRouter()


@router.get("")
def list_reports(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db),
):
    """分页获取报告列表。"""
    total = db.query(Report).count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    effective_page = min(page, total_pages)

    reports = (
        db.query(Report)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .offset((effective_page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [
            {
                "id": r.id,
                "task_id": r.task_id,
                "batch_id": r.batch_id,
                "package_name": r.package_name,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in reports
        ],
        "total": total,
        "page": effective_page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    """查看报告 HTML"""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "报告不存在")

    if not os.path.exists(report.html_path):
        raise HTTPException(404, "报告文件不存在")

    with open(report.html_path, "r", encoding="utf-8") as f:
        html = f.read()

    return HTMLResponse(content=html)


@router.delete("/{report_id}")
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """删除单个报告"""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "报告不存在")
    if report.html_path and os.path.exists(report.html_path):
        os.remove(report.html_path)
    db.delete(report)
    db.commit()
    return {"message": "已删除"}


@router.delete("/all/clear")
def delete_all_reports(db: Session = Depends(get_db)):
    """删除全部报告"""
    reports = db.query(Report).all()
    count = len(reports)
    for r in reports:
        if r.html_path and os.path.exists(r.html_path):
            os.remove(r.html_path)
        db.delete(r)
    db.commit()
    return {"message": f"已删除全部 {count} 个报告", "deleted": count}
