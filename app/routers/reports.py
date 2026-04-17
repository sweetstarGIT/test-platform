"""报告查看路由"""
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Report

router = APIRouter()


@router.get("")
def list_reports(db: Session = Depends(get_db)):
    """报告列表"""
    reports = db.query(Report).order_by(Report.created_at.desc()).limit(50).all()
    return [
        {
            "id": r.id,
            "task_id": r.task_id,
            "batch_id": r.batch_id,
            "package_name": r.package_name,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in reports
    ]


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
