"""包管理路由"""
import os
import subprocess
import shutil
import time
from typing import List
from concurrent.futures import ThreadPoolExecutor
import aiofiles
from fastapi import APIRouter, UploadFile, File, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Package
from app.config import UPLOAD_DIR, API_KEY, AUTO_PUSH_TO_DEVICE
from app.services.package_service import parse_package_name, get_file_type
from app.services.device_service import list_devices

# 后台线程池用于异步解析包名
_package_parser_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pkg_parser")


def _update_package_name_async(pkg_id: int, file_path: str):
    """后台异步解析并更新包名"""
    try:
        package_name = parse_package_name(file_path)
        if package_name:
            db = SessionLocal()
            try:
                pkg = db.query(Package).filter(Package.id == pkg_id).first()
                if pkg:
                    pkg.package_name = package_name
                    db.commit()
                    print(f"[PackageParser] 包名解析完成: {pkg_id} -> {package_name}")
            finally:
                db.close()
    except Exception as e:
        print(f"[PackageParser] 解析失败: {pkg_id}, error: {e}")

# 手机端存放包的目录
DEVICE_PKG_DIR = "/sdcard/快应用"

router = APIRouter()


class BatchDeleteRequest(BaseModel):
    ids: List[int]


class PushToDeviceRequest(BaseModel):
    device_serials: List[str] = []  # 为空时推送到所有设备


@router.post("/upload")
async def upload_package(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Web 上传包"""
    import time
    t0 = time.time()
    ext = get_file_type(file.filename)
    if ext not in ("apk", "rpk"):
        raise HTTPException(400, "仅支持 APK 和 RPK 文件")

    # 保存文件（异步分块写入，避免阻塞）
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    async with aiofiles.open(file_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            await f.write(chunk)
    t1 = time.time()

    file_size = os.path.getsize(file_path)
    t2 = time.time()

    # 先用文件名作为临时包名（快速返回）
    temp_package_name = os.path.splitext(file.filename)[0]
    t3 = time.time()

    # 入库
    pkg = Package(
        filename=file.filename,
        package_name=temp_package_name,
        file_type=ext,
        file_size=file_size,
        file_path=file_path,
        source="upload",
    )
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    t4 = time.time()

    # 后台异步解析真实包名（避免阻塞上传）
    _package_parser_pool.submit(_update_package_name_async, pkg.id, file_path)

    # 根据配置决定是否自动推送
    push_results = []
    if AUTO_PUSH_TO_DEVICE:
        push_results = _push_to_all_devices(file_path, file.filename)
    t5 = time.time()

    print(f"[Upload] {file.filename} ({file_size/1024/1024:.2f}MB): "
          f"保存文件={t1-t0:.2f}s, 获取大小={t2-t1:.2f}s, 准备数据={t3-t2:.2f}s, "
          f"入库={t4-t3:.2f}s, 推送={t5-t4:.2f}s, 总计={t5-t0:.2f}s")

    return {
        "id": pkg.id,
        "filename": pkg.filename,
        "package_name": pkg.package_name,
        "file_type": pkg.file_type,
        "file_size": pkg.file_size,
        "auto_push": AUTO_PUSH_TO_DEVICE,
        "pushed_devices": push_results,
        "timing": {"total": round(t5-t0, 2)},
    }


@router.post("/push")
async def push_package(
    file: UploadFile = File(...),
    x_api_key: str = Header(None),
    db: Session = Depends(get_db),
):
    """CI 系统推送包（需要 API Key）"""
    if x_api_key != API_KEY:
        raise HTTPException(401, "无效的 API Key")

    ext = get_file_type(file.filename)
    if ext not in ("apk", "rpk"):
        raise HTTPException(400, "仅支持 APK 和 RPK 文件")

    file_path = os.path.join(UPLOAD_DIR, file.filename)
    async with aiofiles.open(file_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)

    file_size = os.path.getsize(file_path)

    # 先用文件名作为临时包名（快速返回）
    temp_package_name = os.path.splitext(file.filename)[0]

    pkg = Package(
        filename=file.filename,
        package_name=temp_package_name,
        file_type=ext,
        file_size=file_size,
        file_path=file_path,
        source="ci",
    )
    db.add(pkg)
    db.commit()
    db.refresh(pkg)

    # 后台异步解析真实包名
    _package_parser_pool.submit(_update_package_name_async, pkg.id, file_path)

    # 根据配置决定是否自动推送
    push_results = []
    if AUTO_PUSH_TO_DEVICE:
        push_results = _push_to_all_devices(file_path, file.filename)

    return {"id": pkg.id, "package_name": pkg.package_name, "auto_push": AUTO_PUSH_TO_DEVICE, "pushed_devices": push_results}


@router.get("")
def list_packages(db: Session = Depends(get_db)):
    """列出所有包"""
    packages = db.query(Package).order_by(Package.created_at.desc()).all()
    return [
        {
            "id": p.id,
            "filename": p.filename,
            "package_name": p.package_name,
            "file_type": p.file_type,
            "file_size": p.file_size,
            "source": p.source,
            "created_at": p.created_at.isoformat() if p.created_at else "",
        }
        for p in packages
    ]


@router.post("/batch-delete")
def batch_delete_packages(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    """批量删除包"""
    deleted = 0
    for pkg_id in req.ids:
        pkg = db.query(Package).filter(Package.id == pkg_id).first()
        if pkg:
            if os.path.exists(pkg.file_path):
                os.remove(pkg.file_path)
            db.delete(pkg)
            deleted += 1
    db.commit()
    return {"message": f"已删除 {deleted} 个包", "deleted": deleted}


@router.delete("/all")
def delete_all_packages(db: Session = Depends(get_db)):
    """删除所有包"""
    packages = db.query(Package).all()
    count = len(packages)
    for pkg in packages:
        if os.path.exists(pkg.file_path):
            os.remove(pkg.file_path)
        db.delete(pkg)
    db.commit()
    return {"message": f"已删除全部 {count} 个包", "deleted": count}


@router.delete("/{pkg_id}")
def delete_package(pkg_id: int, db: Session = Depends(get_db)):
    """删除包"""
    pkg = db.query(Package).filter(Package.id == pkg_id).first()
    if not pkg:
        raise HTTPException(404, "包不存在")

    # 删除文件
    if os.path.exists(pkg.file_path):
        os.remove(pkg.file_path)

    db.delete(pkg)
    db.commit()
    return {"message": "已删除"}


@router.post("/{pkg_id}/push")
def push_package_to_devices(
    pkg_id: int,
    req: PushToDeviceRequest,
    db: Session = Depends(get_db)
):
    """手动推送包到指定设备（不传 device_serials 则推送到所有设备）"""
    pkg = db.query(Package).filter(Package.id == pkg_id).first()
    if not pkg:
        raise HTTPException(404, "包不存在")

    if not os.path.exists(pkg.file_path):
        raise HTTPException(404, "包文件不存在")

    results = _push_to_devices(pkg.file_path, pkg.filename, req.device_serials)
    return {
        "package_id": pkg.id,
        "filename": pkg.filename,
        "pushed_devices": results,
    }


def _push_to_all_devices(file_path: str, filename: str) -> list:
    """上传包时自动推送到所有已连接设备的 /sdcard/快应用 目录"""
    return _push_to_devices(file_path, filename, [])


def _push_to_devices(file_path: str, filename: str, device_serials: List[str]) -> list:
    """推送包到指定设备（device_serials 为空时推送到所有设备）"""
    results = []
    try:
        devices = list_devices()
        connected = [d for d in devices if "error" not in d and d.get("status") == "device"]

        # 如果指定了设备序列号，只推送这些设备
        if device_serials:
            connected = [d for d in connected if d["serial"] in device_serials]

        for d in connected:
            serial = d["serial"]
            try:
                # 确保目标目录存在
                subprocess.run(
                    ["adb", "-s", serial, "shell", "mkdir", "-p", DEVICE_PKG_DIR],
                    capture_output=True, timeout=5
                )
                # 推送文件
                dest = f"{DEVICE_PKG_DIR}/{filename}"
                proc = subprocess.run(
                    ["adb", "-s", serial, "push", file_path, dest],
                    capture_output=True, text=True, timeout=120
                )
                if proc.returncode == 0:
                    results.append({"serial": serial, "success": True})
                    print(f"[Push] {filename} -> {serial}:{dest} 成功")
                else:
                    results.append({"serial": serial, "success": False, "error": proc.stderr.strip()})
            except Exception as e:
                results.append({"serial": serial, "success": False, "error": str(e)})
    except Exception:
        pass
    return results
