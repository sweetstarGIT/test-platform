"""测试任务路由"""
import asyncio
import json
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Task, Package, Report
from app.services import task_runner
from app.services.load_balancer import load_balancer, auto_assign_device, BalanceStrategy

router = APIRouter()


class CreateTaskRequest(BaseModel):
    package_id: int
    device_serial: Optional[str] = None  # None 表示自动分配
    auto_assign: bool = False  # 是否启用自动分配


class BatchCreateRequest(BaseModel):
    package_ids: List[int]
    device_serial: Optional[str] = None
    auto_distribute: bool = False  # 是否自动分散到多台设备


class BatchDistributeRequest(BaseModel):
    """高级批量任务请求 - 自动负载均衡分散"""
    package_ids: List[int]
    strategy: str = "least_tasks"  # least_tasks / round_robin / weighted


@router.post("")
def create_task(req: CreateTaskRequest, db: Session = Depends(get_db)):
    """创建测试任务（支持自动分配设备）"""
    pkg = db.query(Package).filter(Package.id == req.package_id).first()
    if not pkg:
        raise HTTPException(404, "包不存在")

    # 确定设备
    device_serial = req.device_serial
    if req.auto_assign or not device_serial:
        device_serial = auto_assign_device()
        if not device_serial:
            raise HTTPException(400, "无可用设备，请检查设备连接状态")

    task = Task(
        package_id=req.package_id,
        device_serial=device_serial,
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    task_runner.submit_task(task.id)
    return {
        "id": task.id,
        "status": "pending",
        "device_serial": device_serial,
        "auto_assigned": req.auto_assign or not req.device_serial
    }


@router.get("")
def list_tasks(db: Session = Depends(get_db)):
    """任务列表"""
    tasks = db.query(Task).order_by(Task.created_at.desc()).limit(50).all()
    result = []
    for t in tasks:
        pkg = db.query(Package).filter(Package.id == t.package_id).first()
        result.append({
            "id": t.id,
            "package_name": pkg.package_name if pkg else "",
            "filename": pkg.filename if pkg else "",
            "device_serial": t.device_serial,
            "batch_id": t.batch_id,
            "status": t.status,
            "error": t.error,
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "started_at": t.started_at.isoformat() if t.started_at else "",
            "finished_at": t.finished_at.isoformat() if t.finished_at else "",
        })
    return result


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    """任务详情"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "任务不存在")

    pkg = db.query(Package).filter(Package.id == task.package_id).first()
    logs = task_runner.get_logs(task_id)
    if not logs:
        try:
            logs = json.loads(task.logs) if task.logs else []
        except Exception:
            logs = []

    return {
        "id": task.id,
        "package_name": pkg.package_name if pkg else "",
        "filename": pkg.filename if pkg else "",
        "device_serial": task.device_serial,
        "batch_id": task.batch_id,
        "status": task.status,
        "error": task.error,
        "logs": logs,
        "report_path": task.report_path,
        "created_at": task.created_at.isoformat() if task.created_at else "",
    }


@router.get("/stats/executors")
def get_executor_stats():
    """获取设备执行器统计（用于监控并行状态）"""
    return task_runner.get_executor_stats()


@router.get("/stats/loadbalancer")
def get_load_balancer_stats():
    """获取负载均衡器统计"""
    return load_balancer.get_stats()


@router.post("/strategy")
def set_balance_strategy(strategy: str):
    """设置负载均衡策略: least_tasks, round_robin, weighted"""
    try:
        s = BalanceStrategy(strategy)
        load_balancer.set_strategy(s)
        return {"strategy": strategy}
    except ValueError:
        raise HTTPException(400, f"无效策略: {strategy}")


@router.get("/{task_id}/logs")
async def stream_logs(task_id: int):
    async def event_generator():
        db = SessionLocal()
        offset = 0
        try:
            # 检查任务状态
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                yield f"data: [错误] 任务不存在\n\n"
                return

            # 如果任务正在运行中或等待中，跳过发送旧日志（避免显示上次运行的日志）
            if task.status in ("running", "pending"):
                # 只从内存获取最新日志
                existing = task_runner.get_logs(task_id)
                for log in existing:
                    yield f"data: {log}\n\n"
                offset = len(existing)
            else:
                # 任务已完成/失败，从数据库读取完整日志
                existing = task_runner.get_logs(task_id)
                if not existing and task.logs:
                    try:
                        existing = json.loads(task.logs)
                    except Exception:
                        existing = []
                for log in existing:
                    yield f"data: {log}\n\n"
                offset = len(existing)

            # 如果任务已结束，直接返回
            if task.status in ("done", "failed", "cancelled"):
                yield f"data: [END] 任务状态: {task.status}\n\n"
                return

            # 发送心跳表示连接已建立
            yield f": heartbeat\n\n"

            # 持续轮询新日志
            while True:
                await asyncio.sleep(1)
                logs = task_runner.get_logs(task_id, offset)
                if logs:
                    for log in logs:
                        yield f"data: {log}\n\n"
                    offset += len(logs)

                db.expire_all()
                task = db.query(Task).filter(Task.id == task_id).first()
                if task and task.status in ("done", "failed", "cancelled"):
                    final_logs = task_runner.get_logs(task_id, offset)
                    for log in final_logs:
                        yield f"data: {log}\n\n"
                    yield f"data: [END] 任务状态: {task.status}\n\n"
                    break

                # 每轮发心跳保持连接
                yield f": heartbeat\n\n"
        finally:
            db.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/batch")
def batch_create_tasks(req: BatchCreateRequest, db: Session = Depends(get_db)):
    """批量创建测试任务（支持自动分散到多台设备）"""
    if not req.package_ids:
        raise HTTPException(400, "请至少选择一个包")

    # 刷新设备列表
    load_balancer.refresh_devices()
    load_balancer.sync_task_status()

    batch_id = uuid.uuid4().hex[:16]
    created = []

    for i, pkg_id in enumerate(req.package_ids):
        pkg = db.query(Package).filter(Package.id == pkg_id).first()
        if not pkg:
            continue

        # 确定设备
        if req.auto_distribute:
            # 每次选择当前负载最低的设备
            device_serial = load_balancer.select_device()
        elif req.device_serial:
            device_serial = req.device_serial
        else:
            device_serial = load_balancer.select_device()

        if not device_serial:
            raise HTTPException(400, f"无可用设备（包: {pkg.filename}）")

        task = Task(
            package_id=pkg_id,
            device_serial=device_serial,
            batch_id=batch_id,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_runner.submit_task(task.id)
        created.append({
            "id": task.id,
            "package_name": pkg.package_name,
            "device_serial": device_serial
        })

        # 更新负载均衡器状态
        load_balancer.sync_task_status()

    return {
        "created": len(created),
        "batch_id": batch_id,
        "tasks": created,
        "distributed": req.auto_distribute
    }


@router.post("/batch/distribute")
def batch_distribute_tasks(req: BatchDistributeRequest, db: Session = Depends(get_db)):
    """
    高级批量任务 - 使用指定策略自动分散到多台设备

    策略:
    - least_tasks: 最少任务优先（默认，最均衡）
    - round_robin: 轮询
    - weighted: 加权随机
    """
    if not req.package_ids:
        raise HTTPException(400, "请至少选择一个包")

    try:
        strategy = BalanceStrategy(req.strategy)
    except ValueError:
        raise HTTPException(400, f"无效策略: {req.strategy}")

    # 设置策略
    load_balancer.set_strategy(strategy)

    # 刷新设备
    load_balancer.refresh_devices()
    load_balancer.sync_task_status()

    batch_id = uuid.uuid4().hex[:16]
    created = []
    device_assignment = {}  # 统计每个设备分配的任务数

    for pkg_id in req.package_ids:
        pkg = db.query(Package).filter(Package.id == pkg_id).first()
        if not pkg:
            continue

        device_serial = load_balancer.select_device()
        if not device_serial:
            raise HTTPException(400, f"无可用设备（包: {pkg.filename}）")

        task = Task(
            package_id=pkg_id,
            device_serial=device_serial,
            batch_id=batch_id,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_runner.submit_task(task.id)

        created.append({
            "id": task.id,
            "package_name": pkg.package_name,
            "device_serial": device_serial
        })

        device_assignment[device_serial] = device_assignment.get(device_serial, 0) + 1

        # 更新状态以影响下一次选择
        load_balancer.sync_task_status()

    return {
        "created": len(created),
        "batch_id": batch_id,
        "strategy": req.strategy,
        "tasks": created,
        "device_assignment": device_assignment
    }


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    """取消任务"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status in ("done", "failed"):
        raise HTTPException(400, "任务已结束")

    task_runner.cancel_task(task_id)
    task.status = "cancelled"
    db.commit()
    return {"message": "已取消"}


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    """删除单个任务"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "任务不存在")
    db.delete(task)
    db.commit()
    return {"message": "已删除"}


@router.post("/batch/{batch_id}/cancel")
def cancel_batch_tasks(batch_id: str, db: Session = Depends(get_db)):
    """一键并发取消批量任务中所有未完成的任务"""
    from concurrent.futures import ThreadPoolExecutor

    batch_tasks = db.query(Task).filter(Task.batch_id == batch_id).all()
    if not batch_tasks:
        raise HTTPException(404, "批次不存在")

    to_cancel = [t for t in batch_tasks if t.status in ("pending", "running")]

    # 并发终止子进程（内存/进程操作，不涉及数据库）
    if to_cancel:
        with ThreadPoolExecutor(max_workers=min(16, len(to_cancel))) as pool:
            pool.map(lambda t: task_runner.cancel_task(t.id), to_cancel)

    # 统一在主线程更新数据库状态
    for task in to_cancel:
        task.status = "cancelled"

    db.commit()
    return {"message": f"已取消 {len(to_cancel)} 个任务", "cancelled": len(to_cancel)}


@router.delete("/batch/{batch_id}")
def delete_batch_tasks(batch_id: str, db: Session = Depends(get_db)):
    """按批次删除批量任务"""
    count = db.query(Task).filter(Task.batch_id == batch_id).delete()
    db.commit()
    return {"message": f"已删除 {count} 个任务", "deleted": count}


@router.delete("/all/clear")
def delete_all_tasks(db: Session = Depends(get_db)):
    """删除全部任务"""
    count = db.query(Task).count()
    db.query(Task).delete()
    db.commit()
    return {"message": f"已删除全部 {count} 个任务", "deleted": count}
