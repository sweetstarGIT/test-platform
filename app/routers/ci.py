"""CI 集成接口 — 供打包流程推送测试任务并查询结果"""
import os
import json
import shutil
import subprocess
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
from app.config import API_KEY, UPLOAD_DIR, DEVICE_PKG_DIR
from app.services.load_balancer import auto_assign_device
from app.services.package_service import get_file_type, parse_package_name
from app.services.device_service import list_devices
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
    filename = req.filename or ""
    if not filename:
        query = urllib.parse.parse_qs(parsed.query)
        for key in ("name", "filename", "file", "path"):
            values = query.get(key) or []
            for value in values:
                candidate = os.path.basename(urllib.parse.unquote(value))
                if get_file_type(candidate) in ("apk", "rpk", "zip"):
                    filename = candidate
                    break
            if filename:
                break
    if not filename:
        filename = os.path.basename(urllib.parse.unquote(parsed.path))
    ext = get_file_type(filename)
    file_type = (req.file_type or ext).lower().lstrip(".")
    if file_type not in ("apk", "rpk", "zip"):
        raise HTTPException(400, "必须提供 APK/RPK/ZIP 文件名，或传 file_type=apk/rpk/zip")
    if ext not in ("apk", "rpk", "zip"):
        filename = f"{req.external_task_id}.{file_type}"
    return filename, file_type


def _normalize_download_url(url: str) -> str:
    """Encode non-ASCII path/query characters before urllib sends the request."""
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("下载地址必须是 HTTP/HTTPS URL")

    hostname = parsed.hostname.encode("idna").decode("ascii")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username:
        userinfo = urllib.parse.quote(urllib.parse.unquote(parsed.username), safe="")
        if parsed.password:
            password = urllib.parse.quote(urllib.parse.unquote(parsed.password), safe="")
            userinfo = f"{userinfo}:{password}"
        netloc = f"{userinfo}@{netloc}"

    path = urllib.parse.quote(
        urllib.parse.unquote(parsed.path),
        safe="/:@!$&'()*+,;=",
    )
    query = urllib.parse.quote(
        urllib.parse.unquote(parsed.query),
        safe="/?:@!$'()*+,;=&",
    )
    return urllib.parse.urlunsplit((parsed.scheme, netloc, path, query, parsed.fragment))


def _download_artifact(url: str, filename: str) -> tuple[str, int]:
    safe_name = f"{int(time.time())}_{filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    try:
        download_url = _normalize_download_url(url)
        with urllib.request.urlopen(download_url, timeout=120) as resp:
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


def _build_request_from_job(job: CiJob) -> PushTaskRequest:
    return PushTaskRequest(
        external_task_id=job.external_task_id,
        package_name=job.package_name or "",
        artifact_url=job.artifact_url,
        filename="",
        file_type="",
        task_status=job.task_status or "manual_rerun",
        callback_url=job.callback_url or "",
        device_serial=job.device_serial or None,
        new_package=True,
    )


def _is_running_job(job: CiJob) -> bool:
    if job.status not in ("received", "downloading", "extracting", "packaging", "assigning", "queued", "running"):
        return False
    if job.updated_at and (datetime.now() - job.updated_at).total_seconds() > 300:
        return False
    return True


def _remove_job_package(db: Session, job: CiJob):
    if not job.package_id:
        return
    pkg = db.query(Package).filter(Package.id == job.package_id).first()
    if not pkg:
        job.package_id = None
        return
    referenced_tasks = db.query(Task).filter(Task.package_id == pkg.id).count()
    if referenced_tasks:
        job.package_id = None
        return
    if pkg.source == "ci" and pkg.file_path and os.path.exists(pkg.file_path):
        os.remove(pkg.file_path)
    db.delete(pkg)
    job.package_id = None


def _prepare_ci_package(db: Session, job: CiJob, req: PushTaskRequest, reset: bool = False) -> Package:
    if reset:
        _remove_job_package(db, job)
        job.error = ""
        job.callback_error = ""
        job.callback_sent = False
        job.task_id = None
        job.report_id = None
        job.finished_at = None
        job.updated_at = datetime.now()
        db.commit()

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
    job.error = ""
    _add_job_event(db, job, "package_saved", f"测试包已入库: package_id={pkg.id}", "package_ready")
    return pkg


def _push_package_to_device(serial: str, pkg: Package) -> str:
    subprocess.run(
        ["adb", "-s", serial, "shell", "mkdir", "-p", DEVICE_PKG_DIR],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
    )
    dest = f"{DEVICE_PKG_DIR}/{pkg.filename}"
    proc = subprocess.run(
        ["adb", "-s", serial, "push", pkg.file_path, dest],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "adb push failed").strip())
    return dest


def _online_device_serials() -> set[str]:
    devices = list_devices()
    return {d.get("serial") for d in devices if "error" not in d and d.get("status") == "device" and d.get("serial")}


def _resolve_ci_device(job: CiJob, preferred_device_serial: str | None = None) -> str:
    online_serials = _online_device_serials()
    for serial in (preferred_device_serial, job.device_serial):
        if serial and serial in online_serials:
            return serial
    if job.device_serial and job.device_serial not in online_serials:
        job.device_serial = ""
    return auto_assign_device()


def _assign_and_push_ci_package(db: Session, job: CiJob, pkg: Package, preferred_device_serial: str | None = None) -> str:
    device_serial = _resolve_ci_device(job, preferred_device_serial)
    if not device_serial:
        _add_job_event(db, job, "push_skipped", "测试包已入库，但当前无可用设备，未推送到手机", "package_ready")
        return ""

    job.device_serial = device_serial
    try:
        dest = _push_package_to_device(device_serial, pkg)
        _add_job_event(db, job, "package_pushed", f"测试包已推送到设备: {device_serial}:{dest}", "package_ready")
        return dest
    except Exception as e:
        job.error = f"推送到设备失败: {e}"
        _add_job_event(db, job, "push_failed", job.error, "failed")
        raise HTTPException(400, job.error)


def _create_ci_task(db: Session, job: CiJob, req: PushTaskRequest) -> Task:
    if not job.package_id:
        raise HTTPException(400, "还没有可用测试包，请先手动下载")

    pkg = db.query(Package).filter(Package.id == job.package_id).first()
    if not pkg:
        job.package_id = None
        db.commit()
        raise HTTPException(400, "关联测试包不存在，请重新手动下载")

    device_serial = _resolve_ci_device(job, req.device_serial)
    if not device_serial:
        _fail_job(db, job, "assign_failed", "无可用设备，未创建测试任务")
        raise HTTPException(400, "无可用设备，未创建测试任务")
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
    job.report_id = None
    job.callback_sent = False
    job.callback_error = ""
    job.finished_at = None
    _add_job_event(db, job, "task_created", f"测试任务已创建: task_id={task.id}", "queued")

    task_runner.submit_task(task.id)
    _add_job_event(db, job, "submitted", "测试任务已提交执行队列", "queued")
    return task


def _run_ci_job(db: Session, job: CiJob, req: PushTaskRequest, manual: bool = False) -> dict:
    if manual:
        job.status = "received"
        job.current_step = "manual_rerun"
        job.error = ""
        job.callback_error = ""
        job.callback_sent = False
        job.task_id = None
        job.report_id = None
        job.finished_at = None
        job.updated_at = datetime.now()
        db.commit()
        _add_job_event(db, job, "manual_rerun", "手动触发重新运行", "received")

    pkg = _prepare_ci_package(db, job, req, reset=manual)
    _assign_and_push_ci_package(db, job, pkg, req.device_serial)
    task = _create_ci_task(db, job, req)

    return {
        "message": "任务已创建",
        "ci_job_id": job.id,
        "external_task_id": req.external_task_id,
        "task_id": task.id,
        "package_id": pkg.id,
        "package_name": pkg.package_name,
        "filename": pkg.filename,
        "device_serial": task.device_serial,
        "status": job.status,
        "task_status": task.status,
        "current_step": job.current_step,
        "new_package": task.new_package,
        "duplicate": False,
        "manual": manual,
    }


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
    return _run_ci_job(db, job, req)


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


@router.post("/jobs/{job_id}/rerun")
def rerun_ci_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(CiJob).filter(CiJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "CI流转任务不存在")
    if _is_running_job(job):
        raise HTTPException(400, "CI流转任务仍在运行中，不能重复手动运行")
    if not job.artifact_url:
        raise HTTPException(400, "缺少构建产物下载地址，不能手动运行")

    req = _build_request_from_job(job)
    return _run_ci_job(db, job, req, manual=True)


@router.post("/jobs/{job_id}/download")
def download_ci_job_artifact(job_id: int, db: Session = Depends(get_db)):
    job = db.query(CiJob).filter(CiJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "CI流转任务不存在")
    if _is_running_job(job):
        raise HTTPException(400, "CI流转任务仍在运行中，不能手动下载")
    if not job.artifact_url:
        raise HTTPException(400, "缺少构建产物下载地址，不能手动下载")

    req = _build_request_from_job(job)
    _add_job_event(db, job, "manual_download", "手动触发下载构建产物", "downloading")
    pkg = _prepare_ci_package(db, job, req, reset=True)
    pushed_dest = _assign_and_push_ci_package(db, job, pkg, req.device_serial)
    return {
        "message": "构建产物已下载、入库并推送到设备" if pushed_dest else "构建产物已下载并入库，暂无可用设备可推送",
        "ci_job_id": job.id,
        "package_id": pkg.id,
        "package_name": pkg.package_name,
        "filename": pkg.filename,
        "device_serial": job.device_serial,
        "pushed_dest": pushed_dest,
        "status": job.status,
        "current_step": job.current_step,
    }


@router.post("/jobs/{job_id}/run-script")
def run_ci_job_script(job_id: int, db: Session = Depends(get_db)):
    job = db.query(CiJob).filter(CiJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "CI流转任务不存在")
    if _is_running_job(job):
        raise HTTPException(400, "CI流转任务仍在运行中，不能重复跑脚本")
    if not job.package_id:
        _fail_job(db, job, "package_missing", "还没有可用测试包，请先手动下载")
        raise HTTPException(400, "还没有可用测试包，请先手动下载")

    req = _build_request_from_job(job)
    job.error = ""
    job.callback_error = ""
    job.callback_sent = False
    job.task_id = None
    job.report_id = None
    job.finished_at = None
    _add_job_event(db, job, "manual_run_script", "手动触发跑脚本", "assigning")
    try:
        task = _create_ci_task(db, job, req)
    except HTTPException as e:
        _fail_job(db, job, "run_script_failed", str(e.detail))
        raise
    except Exception as e:
        _fail_job(db, job, "run_script_failed", str(e))
        raise HTTPException(400, f"手动跑脚本失败: {e}")
    pkg = db.query(Package).filter(Package.id == job.package_id).first()
    return {
        "message": "测试脚本已提交执行",
        "ci_job_id": job.id,
        "task_id": task.id,
        "package_id": job.package_id,
        "package_name": pkg.package_name if pkg else job.package_name,
        "device_serial": task.device_serial,
        "status": job.status,
        "task_status": task.status,
        "current_step": job.current_step,
    }


@router.post("/jobs/{job_id}/retry")
def retry_ci_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(CiJob).filter(CiJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "CI流转任务不存在")
    if _is_running_job(job):
        raise HTTPException(400, "CI流转任务仍在运行中，不能重试")
    if not job.artifact_url:
        raise HTTPException(400, "缺少构建产物下载地址，不能重试")

    req = _build_request_from_job(job)
    pkg_exists = db.query(Package).filter(Package.id == job.package_id).first() if job.package_id else None
    if job.package_id and not pkg_exists:
        job.package_id = None
        _add_job_event(db, job, "package_missing", "关联测试包不存在，改为重新下载构建产物", "received")

    if job.package_id:
        job.error = ""
        job.callback_error = ""
        job.callback_sent = False
        job.task_id = None
        job.report_id = None
        job.finished_at = None
        _add_job_event(db, job, "retry", "重试：复用已下载测试包并重新跑脚本", "assigning")
        try:
            task = _create_ci_task(db, job, req)
        except HTTPException as e:
            _fail_job(db, job, "retry_failed", str(e.detail))
            raise
        except Exception as e:
            _fail_job(db, job, "retry_failed", str(e))
            raise HTTPException(400, f"重试失败: {e}")
        return {
            "message": "已复用测试包并重新提交脚本",
            "ci_job_id": job.id,
            "task_id": task.id,
            "package_id": job.package_id,
            "device_serial": task.device_serial,
            "status": job.status,
            "task_status": task.status,
            "current_step": job.current_step,
            "mode": "run_script",
        }

    _add_job_event(db, job, "retry", "重试：重新下载构建产物并运行", "received")
    try:
        result = _run_ci_job(db, job, req, manual=True)
    except HTTPException:
        raise
    except Exception as e:
        _fail_job(db, job, "retry_failed", str(e))
        raise HTTPException(400, f"重试失败: {e}")
    result["message"] = "已重新下载并提交脚本"
    result["mode"] = "full"
    return result


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
