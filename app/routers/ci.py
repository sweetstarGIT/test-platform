"""CI 集成接口 — 供打包流程推送测试任务并查询结果"""
import os
import json
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task, Package, Report, CiJob
from app.config import API_KEY, UPLOAD_DIR
from app.services.load_balancer import auto_assign_device
from app.services.package_service import get_file_type, parse_package_name
from app.services import task_runner

router = APIRouter()


class PushTaskRequest(BaseModel):
    external_task_id: str = Field(..., description="开发平台任务ID")
    package_name: str = Field("", description="开发平台识别到的包名，可为空")
    artifact_url: str = Field(..., description="APK/RPK/ZIP 下载地址")
    filename: str = Field("", description="文件名；下载地址不带文件名时建议传")
    file_type: str = Field("", description="文件类型 apk/rpk/zip；无法从文件名识别时必传")
    task_status: str = Field("created", description="开发平台当前任务状态")
    callback_url: str = Field("", description="测试完成后回调开发平台的 URL")
    device_serial: str | None = Field(None, description="指定设备；为空则自动分配")
    new_package: bool = Field(True, description="是否启用新包模式；CI 推送任务固定按新包模式执行")


def _require_api_key(x_api_key: str | None):
    if x_api_key != API_KEY:
        raise HTTPException(401, "无效的 API Key")


def _add_job_event(db: Session, job: CiJob, step: str, message: str, status: str | None = None):
    try:
        events = json.loads(job.events) if job.events else []
    except Exception:
        events = []
    events.append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "step": step,
        "message": message,
    })
    job.events = json.dumps(events, ensure_ascii=False)
    job.current_step = step
    if status:
        job.status = status
    job.updated_at = datetime.now()
    db.commit()


def _fail_job(db: Session, job: CiJob, step: str, message: str):
    job.status = "failed"
    job.error = message
    job.finished_at = datetime.now()
    _add_job_event(db, job, step, message, "failed")


def _resolve_filename(url: str, req: PushTaskRequest) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    filename = req.filename or os.path.basename(urllib.parse.unquote(parsed.path))
    ext = get_file_type(filename)
    file_type = (req.file_type or ext).lower().lstrip(".")
    if file_type not in ("apk", "rpk", "zip"):
        raise HTTPException(400, "必须提供 APK/RPK/ZIP 文件名，或传 file_type=apk/rpk/zip")
    if ext not in ("apk", "rpk", "zip"):
        filename = f"{req.external_task_id}.{file_type}"
    return filename, file_type


def _download_artifact(url: str, filename: str) -> tuple[str, int]:
    safe_name = f"{int(time.time())}_{filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            with open(file_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(400, f"下载包失败: {e}")

    return file_path, os.path.getsize(file_path)


def _safe_extract_zip(zip_path: str, target_dir: str):
    os.makedirs(target_dir, exist_ok=True)
    target_root = os.path.abspath(target_dir)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                dest = os.path.abspath(os.path.join(target_dir, member.filename))
                if not dest.startswith(target_root + os.sep):
                    raise HTTPException(400, "压缩包包含非法路径")
                zf.extract(member, target_dir)
    except zipfile.BadZipFile:
        raise HTTPException(400, "压缩包格式无效")


def _find_package_in_dir(root_dir: str) -> tuple[str, str, str]:
    candidates = []
    for dirpath, _, filenames in os.walk(root_dir):
        for name in filenames:
            ext = get_file_type(name)
            if ext in ("rpk", "apk"):
                full_path = os.path.join(dirpath, name)
                candidates.append((ext, full_path, name))

    if not candidates:
        raise HTTPException(400, "压缩包内未找到 APK 或 RPK 文件")

    # 快应用场景优先选择 RPK；如果没有 RPK，再使用 APK。
    candidates.sort(key=lambda item: (0 if item[0] == "rpk" else 1, item[2]))
    file_type, file_path, filename = candidates[0]
    return file_path, filename, file_type


def _prepare_package_file(file_path: str, filename: str, file_type: str, external_task_id: str) -> tuple[str, str, str, int]:
    if file_type != "zip":
        return file_path, filename, file_type, os.path.getsize(file_path)

    extract_dir = os.path.join(UPLOAD_DIR, f"{int(time.time())}_{external_task_id}_extract")
    try:
        _safe_extract_zip(file_path, extract_dir)
        package_path, package_filename, package_type = _find_package_in_dir(extract_dir)
        staged_name = f"{int(time.time())}_{external_task_id}_{package_filename}"
        staged_path = os.path.join(UPLOAD_DIR, staged_name)
        shutil.copy2(package_path, staged_path)
        return staged_path, package_filename, package_type, os.path.getsize(staged_path)
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.isdir(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)


@router.post("/tasks")
def push_task(
    req: PushTaskRequest,
    x_api_key: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """开发平台推送自动测试任务。

    开发平台提供外部任务ID、包名、下载地址、任务状态和回调地址。
    本平台下载包、创建测试任务并自动开始执行。
    """
    _require_api_key(x_api_key)

    existing_job = (
        db.query(CiJob)
        .filter(CiJob.external_task_id == req.external_task_id)
        .order_by(CiJob.created_at.desc())
        .first()
    )
    if existing_job:
        return {
            "message": "CI流转任务已存在",
            "ci_job_id": existing_job.id,
            "external_task_id": req.external_task_id,
            "task_id": existing_job.task_id,
            "package_id": existing_job.package_id,
            "package_name": existing_job.package_name,
            "device_serial": existing_job.device_serial,
            "status": existing_job.status,
            "current_step": existing_job.current_step,
            "new_package": existing_job.new_package,
            "duplicate": True,
        }

    existing = (
        db.query(Task)
        .filter(Task.external_task_id == req.external_task_id)
        .order_by(Task.created_at.desc())
        .first()
    )
    if existing:
        pkg = db.query(Package).filter(Package.id == existing.package_id).first()
        return {
            "message": "任务已存在",
            "external_task_id": req.external_task_id,
            "task_id": existing.id,
            "package_id": existing.package_id,
            "package_name": pkg.package_name if pkg else req.package_name,
            "device_serial": existing.device_serial,
            "status": existing.status,
            "new_package": existing.new_package,
            "duplicate": True,
        }

    job = CiJob(
        external_task_id=req.external_task_id,
        package_name=req.package_name,
        artifact_url=req.artifact_url,
        task_status=req.task_status,
        callback_url=req.callback_url,
        new_package=True,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _add_job_event(db, job, "received", "收到开发平台推送任务", "received")

    try:
        filename, file_type = _resolve_filename(req.artifact_url, req)
        job.artifact_filename = filename
        job.artifact_type = file_type
        _add_job_event(db, job, "resolved", f"识别构建产物: {filename} ({file_type})", "downloading")

        artifact_path, _ = _download_artifact(req.artifact_url, filename)
        _add_job_event(db, job, "downloaded", "构建产物下载完成", "extracting" if file_type == "zip" else "packaging")

        file_path, filename, file_type, file_size = _prepare_package_file(
            artifact_path,
            filename,
            file_type,
            req.external_task_id,
        )
        job.artifact_filename = filename
        job.artifact_type = file_type
        _add_job_event(db, job, "package_found", f"找到测试包: {filename} ({file_type})", "packaging")
    except HTTPException as e:
        _fail_job(db, job, "prepare_failed", str(e.detail))
        raise
    except Exception as e:
        _fail_job(db, job, "prepare_failed", str(e))
        raise HTTPException(400, f"准备构建产物失败: {e}")

    parsed_package_name = req.package_name or parse_package_name(file_path) or os.path.splitext(filename)[0]
    pkg = Package(
        filename=filename,
        package_name=parsed_package_name,
        file_type=file_type,
        file_size=file_size,
        file_path=file_path,
        source="ci",
    )
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    job.package_id = pkg.id
    job.package_name = pkg.package_name
    _add_job_event(db, job, "package_saved", f"测试包已入库: package_id={pkg.id}", "assigning")

    device_serial = req.device_serial or auto_assign_device()
    if not device_serial:
        pkg_id = pkg.id
        if os.path.exists(file_path):
            os.remove(file_path)
        db.delete(pkg)
        job.package_id = None
        db.commit()
        _fail_job(db, job, "assign_failed", "无可用设备，已取消任务创建")
        raise HTTPException(400, f"无可用设备，已取消任务创建 package_id={pkg_id}")
    job.device_serial = device_serial
    _add_job_event(db, job, "device_assigned", f"已分配设备: {device_serial}", "queued")

    task = Task(
        package_id=pkg.id,
        device_serial=device_serial,
        status="pending",
        new_package=True,
        external_task_id=req.external_task_id,
        external_status=req.task_status,
        artifact_url=req.artifact_url,
        callback_url=req.callback_url,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    job.task_id = task.id
    _add_job_event(db, job, "task_created", f"测试任务已创建: task_id={task.id}", "queued")

    task_runner.submit_task(task.id)
    _add_job_event(db, job, "submitted", "测试任务已提交执行队列", "queued")

    return {
        "message": "任务已创建",
        "ci_job_id": job.id,
        "external_task_id": req.external_task_id,
        "task_id": task.id,
        "package_id": pkg.id,
        "package_name": pkg.package_name,
        "filename": pkg.filename,
        "device_serial": device_serial,
        "status": job.status,
        "task_status": task.status,
        "current_step": job.current_step,
        "new_package": task.new_package,
        "duplicate": False,
    }


def _serialize_ci_job(job: CiJob, db: Session) -> dict:
    task = db.query(Task).filter(Task.id == job.task_id).first() if job.task_id else None
    report_id = job.report_id
    if not report_id and task:
        report = db.query(Report).filter(Report.task_id == task.id).order_by(Report.created_at.desc()).first()
        report_id = report.id if report else None
    try:
        events = json.loads(job.events) if job.events else []
    except Exception:
        events = []
    return {
        "id": job.id,
        "external_task_id": job.external_task_id,
        "package_name": job.package_name,
        "artifact_url": job.artifact_url,
        "artifact_filename": job.artifact_filename,
        "artifact_type": job.artifact_type,
        "task_status": job.task_status,
        "status": job.status,
        "current_step": job.current_step,
        "device_serial": job.device_serial,
        "package_id": job.package_id,
        "task_id": job.task_id,
        "task_platform_status": task.status if task else "",
        "report_id": report_id,
        "report_url": f"/api/reports/{report_id}" if report_id else "",
        "callback_url": job.callback_url,
        "callback_sent": job.callback_sent,
        "callback_error": job.callback_error,
        "error": job.error,
        "new_package": job.new_package,
        "events": events,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
        "finished_at": job.finished_at.isoformat() if job.finished_at else "",
    }


@router.get("/jobs")
def list_ci_jobs(db: Session = Depends(get_db)):
    jobs = db.query(CiJob).order_by(CiJob.created_at.desc()).limit(100).all()
    return [_serialize_ci_job(job, db) for job in jobs]


@router.get("/jobs/{job_id}")
def get_ci_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(CiJob).filter(CiJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "CI流转任务不存在")
    return _serialize_ci_job(job, db)


@router.delete("/jobs/{job_id}")
def delete_ci_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(CiJob).filter(CiJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "CI流转任务不存在")
    db.delete(job)
    db.commit()
    return {"message": "已删除"}


@router.delete("/jobs")
def clear_ci_jobs(db: Session = Depends(get_db)):
    count = db.query(CiJob).delete()
    db.commit()
    return {"message": f"已删除 {count} 条CI流转记录", "deleted": count}


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
