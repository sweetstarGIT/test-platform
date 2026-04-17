"""测试执行引擎 - 后台线程池运行测试任务

核心思路：
- RPK 包：以子进程方式运行 testcase/main.py，和本地手动执行完全一致
- APK 包：基础测试流程（ADB 安装 → 启动 → 截图验证）
- 设备级并行：每台设备一个独立线程池，同一设备串行，不同设备并行
"""
import json
import time
import os
import subprocess
import threading
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Task, Package, Report
from app.services import device_service
from app.config import REPORT_DIR, TESTCASE_PROJECT_DIR

# ==================== 设备级并行执行器 ====================
# 每个设备一个独立的单线程执行器，保证同一设备串行，不同设备并行
_device_executors: Dict[str, ThreadPoolExecutor] = {}
_device_executor_lock = threading.Lock()

# 最大并行设备数（可根据服务器性能调整）
MAX_PARALLEL_DEVICES = 8

# 任务日志缓存（供 SSE 实时推送）
_task_logs: Dict[int, List[str]] = {}
_task_logs_lock = threading.Lock()

# 已取消的任务：task_id -> subprocess.Popen
_running_processes: Dict[int, subprocess.Popen] = {}
_processes_lock = threading.Lock()

# 被标记取消的任务ID集合（用于批量取消等场景）
_cancelled_task_ids: set[int] = set()
_cancelled_lock = threading.Lock()


def _get_or_create_executor(device_serial: str) -> ThreadPoolExecutor:
    """获取或创建设备的执行器（每个设备独立单线程）"""
    with _device_executor_lock:
        if device_serial not in _device_executors:
            _device_executors[device_serial] = ThreadPoolExecutor(max_workers=1)
            print(f"[Executor] 为设备 {device_serial} 创建执行器")
        return _device_executors[device_serial]


def _cleanup_executor(device_serial: str):
    """清理空闲设备的执行器"""
    with _device_executor_lock:
        executor = _device_executors.get(device_serial)
        if executor:
            executor.shutdown(wait=False)
            del _device_executors[device_serial]
            print(f"[Executor] 清理设备 {device_serial} 执行器")


def get_executor_stats() -> Dict:
    """获取执行器统计信息（用于监控）"""
    with _device_executor_lock:
        return {
            "active_devices": list(_device_executors.keys()),
            "executor_count": len(_device_executors),
        }


def append_log(task_id: int, message: str):
    """追加日志"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_line = f"[{timestamp}] {message}"
    with _task_logs_lock:
        if task_id not in _task_logs:
            _task_logs[task_id] = []
        _task_logs[task_id].append(log_line)


def get_logs(task_id: int, offset: int = 0) -> List[str]:
    """获取日志（从 offset 开始）"""
    with _task_logs_lock:
        logs = _task_logs.get(task_id, [])
        return logs[offset:]


def cancel_task(task_id: int):
    """取消任务：标记取消并终止子进程"""
    with _cancelled_lock:
        _cancelled_task_ids.add(task_id)
    append_log(task_id, "收到取消指令，正在停止...")
    with _processes_lock:
        proc = _running_processes.get(task_id)
        if proc and proc.poll() is None:
            proc.terminate()
            append_log(task_id, "子进程已终止")


def is_task_cancelled(task_id: int) -> bool:
    with _cancelled_lock:
        return task_id in _cancelled_task_ids


def _clear_cancelled(task_id: int):
    with _cancelled_lock:
        _cancelled_task_ids.discard(task_id)


def submit_task(task_id: int):
    """提交任务到对应设备的线程池

    同一设备的任务串行执行，不同设备的任务并行执行。
    """
    # 清空该任务之前的日志缓存
    with _task_logs_lock:
        _task_logs[task_id] = []

    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            print(f"[Task] 任务 {task_id} 不存在")
            return

        # 清空数据库中的旧日志，避免前端显示历史日志
        task.logs = None
        db.commit()

        device_serial = task.device_serial
        executor = _get_or_create_executor(device_serial)
        executor.submit(_run_task, task_id)
        print(f"[Task] 任务 {task_id} 已提交到设备 {device_serial} 队列")
    finally:
        db.close()


def _run_task(task_id: int):
    """执行测试任务"""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return

        # 如果任务已被取消，直接结束（避免覆盖外部批量取消的状态）
        if is_task_cancelled(task_id):
            _update_task(db, task, "cancelled")
            append_log(task_id, "任务已取消")
            with _task_logs_lock:
                task.logs = json.dumps(_task_logs.get(task_id, []), ensure_ascii=False)
            db.commit()
            _maybe_cleanup_executor(task.device_serial)
            return

        # 清空数据库中的旧日志
        task.logs = None
        db.commit()

        pkg = db.query(Package).filter(Package.id == task.package_id).first()
        if not pkg:
            _update_task(db, task, "failed", error="包不存在")
            return

        # 更新状态
        task.status = "running"
        task.started_at = datetime.now()
        db.commit()

        append_log(task_id, f"开始测试: {pkg.filename}")
        append_log(task_id, f"目标设备: {task.device_serial}")
        append_log(task_id, f"包类型: {pkg.file_type}")

        # 检查设备连接
        append_log(task_id, "检查设备连接...")
        devices = device_service.list_devices()
        serials = [d.get("serial") for d in devices if "error" not in d]
        if task.device_serial not in serials:
            if is_task_cancelled(task_id):
                _update_task(db, task, "cancelled")
            else:
                _update_task(db, task, "failed", error=f"设备 {task.device_serial} 未连接")
            append_log(task_id, f"设备 {task.device_serial} 未连接")
            return
        append_log(task_id, "设备连接正常")

        # 根据包类型选择测试模式
        if pkg.file_type == "rpk":
            test_result = _run_rpk_subprocess(task_id, task, pkg)
        else:
            test_result = _run_apk_basic_test(task_id, task, pkg)

        # 生成单任务报告（非批量时）
        if not task.batch_id:
            append_log(task_id, "生成测试报告...")
            report_path = _generate_report(task_id, pkg, test_result, db, task)
            task.report_path = report_path

        # 完成：正确识别被取消的状态
        if test_result.get("status") == "cancelled":
            final_status = "cancelled"
        elif test_result.get("status") == "failed":
            final_status = "failed"
        else:
            final_status = "done"
        _update_task(db, task, final_status)
        append_log(task_id, f"测试完成! 状态: {final_status}")

        # 先保存日志到数据库（batch 报告需要读取日志）
        with _task_logs_lock:
            task.logs = json.dumps(_task_logs.get(task_id, []), ensure_ascii=False)
        db.commit()

        # 如果是批量任务，检查同批次是否全部完成
        if task.batch_id:
            _check_batch_complete(task.batch_id, db)

    except Exception as e:
        append_log(task_id, f"任务异常: {e}")
        append_log(task_id, traceback.format_exc())
        task = db.query(Task).filter(Task.id == task_id).first()
        if task and not is_task_cancelled(task_id):
            _update_task(db, task, "failed", error=str(e))
    finally:
        # 清理进程记录
        with _processes_lock:
            _running_processes.pop(task_id, None)
        # 保存日志到数据库
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            with _task_logs_lock:
                task.logs = json.dumps(_task_logs.get(task_id, []), ensure_ascii=False)
            db.commit()
            # 检查是否需要清理设备执行器
            _maybe_cleanup_executor(task.device_serial)
        db.close()
        _clear_cancelled(task_id)


def _maybe_cleanup_executor(device_serial: str):
    """检查设备是否还有运行中的任务，没有则清理执行器"""
    db = SessionLocal()
    try:
        # 检查该设备是否还有运行中或待执行的任务
        active_tasks = db.query(Task).filter(
            Task.device_serial == device_serial,
            Task.status.in_(["pending", "running"])
        ).count()

        if active_tasks == 0:
            # 没有活跃任务，清理执行器
            _cleanup_executor(device_serial)
    finally:
        db.close()


# ============================================================
# RPK 测试 —— 子进程执行 testcase/main.py
# 和本地 `python main.py` 完全一致
# ============================================================

def _run_rpk_subprocess(task_id: int, task: Task, pkg: Package) -> Dict:
    """
    RPK 测试：以子进程运行 testcase/main.py
    CWD、Python环境、模块加载、print 输出全部和本地一样
    """
    import re  # 添加正则解析

    append_log(task_id, "=== RPK 测试（子进程模式，和本地执行完全一致）===")

    result = {
        "steps": [],
        "status": "success",
        "app_type": "unknown",
        "module_results": {},
        "tab_results": [],
    }

    # 添加设备信息
    result["device_serial"] = task.device_serial

    # 解析格式: ✅ 功能名 -> 模块名 : success/failed (兼容多种符号)
    # 匹配包含 -> 和 : 的行，更宽松
    module_pattern = re.compile(r'(.+?)\s*->\s*(\S+)\s*:\s*(\w+)')

    main_py = os.path.join(TESTCASE_PROJECT_DIR, "main.py")
    if not os.path.exists(main_py):
        append_log(task_id, f"找不到 testcase 入口: {main_py}")
        result["status"] = "failed"
        result["steps"].append({"name": "查找框架", "status": "failed"})
        return result

    # 确保 RPK 在设备上
    _push_rpk_to_device(task.device_serial, pkg, task_id)
    result["steps"].append({"name": "推送RPK", "status": "success"})

    # 创建临时包列表文件（只包含当前包）
    pkg_list_file = os.path.join(TESTCASE_PROJECT_DIR, f"_platform_task_{task_id}.txt")
    try:
        with open(pkg_list_file, "w", encoding="utf-8") as f:
            f.write(pkg.filename + "\n")

        # 构建命令：用和平台相同的 Python 解释器，避免版本/环境不一致
        import sys
        python_exe = sys.executable

        cmd = [
            python_exe, "-u", main_py,  # -u = unbuffered，确保实时输出
            "--packages", pkg_list_file,
            "--device", task.device_serial,
            "--report", os.path.join(TESTCASE_PROJECT_DIR, "reports", f"report_task_{task_id}.html"),
        ]

        append_log(task_id, f"启动子进程: {python_exe} -u main.py --device {task.device_serial}")
        append_log(task_id, f"工作目录: {TESTCASE_PROJECT_DIR}")

        # 启动子进程（CWD 设为 testcase 目录，强制 UTF-8 避免 GBK 编码崩溃）
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"  # 禁用缓冲，stdout 实时输出

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=TESTCASE_PROJECT_DIR,
            env=env,
            bufsize=1,  # 行缓冲，实时输出
        )

        # 记录进程（用于取消）
        with _processes_lock:
            _running_processes[task_id] = proc

        # 实时读取子进程输出并追加到日志（用 readline 替代 for 迭代，避免内部缓冲）
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            line = line.rstrip("\n\r")
            if line:
                append_log(task_id, line)
                # 解析功能模块结果
                match = module_pattern.search(line)
                if match and '->' in line and ':' in line:
                    tab_name = match.group(1).strip()
                    # 清理开头的符号
                    tab_name = re.sub(r'^[✅✓✗✔✕\s]+', '', tab_name)
                    module_name = match.group(2).strip()
                    status = match.group(3).strip()
                    result["module_results"][tab_name] = {
                        "module": module_name,
                        "status": status,
                        "message": ""
                    }

        exit_code = proc.returncode

        # 判断测试结果：检查退出码和模块结果
        has_failed_module = any(
            m.get("status") == "failed"
            for m in result["module_results"].values()
        )

        if exit_code == 0 and not has_failed_module:
            result["status"] = "success"
            result["steps"].append({"name": "执行测试", "status": "success"})
        elif exit_code == -15:
            result["status"] = "cancelled"
            result["steps"].append({"name": "执行测试", "status": "cancelled"})
        else:
            result["status"] = "failed"
            error_msg = f"退出码: {exit_code}" if exit_code != 0 else "部分功能模块测试失败"
            result["steps"].append({"name": "执行测试", "status": "failed", "error": error_msg})

        # 尝试读取 testcase 生成的报告来提取结果详情
        report_html = os.path.join(TESTCASE_PROJECT_DIR, "reports", f"report_task_{task_id}.html")
        if os.path.exists(report_html):
            result["steps"].append({"name": "生成报告", "status": "success"})

    except Exception as e:
        append_log(task_id, f"子进程执行异常: {e}")
        append_log(task_id, traceback.format_exc())
        result["status"] = "failed"
        result["steps"].append({"name": "执行测试", "status": "failed", "error": str(e)})
    finally:
        # 清理临时包列表文件
        try:
            if os.path.exists(pkg_list_file):
                os.remove(pkg_list_file)
        except Exception:
            pass
        # 回到桌面
        try:
            subprocess.run(
                ["adb", "-s", task.device_serial, "shell", "input", "keyevent", "3"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
            )
        except Exception:
            pass

    return result


def _push_rpk_to_device(serial: str, pkg: Package, task_id: int):
    """推送 RPK 文件到设备"""
    try:
        subprocess.run(
            ["adb", "-s", serial, "push", pkg.file_path, "/sdcard/"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )
        append_log(task_id, f"已推送 {pkg.filename} 到设备 /sdcard/")
    except Exception as e:
        append_log(task_id, f"RPK 推送失败: {e}")


# ============================================================
# APK 基础测试
# ============================================================

def _run_apk_basic_test(task_id: int, task: Task, pkg: Package) -> Dict:
    """APK 基础测试流程"""
    append_log(task_id, "=== APK 基础测试模式 ===")

    result = {"steps": [], "status": "success"}

    # 安装
    append_log(task_id, f"安装 APK: {pkg.filename}")
    install_result = device_service.install_apk(task.device_serial, pkg.file_path)
    if not install_result["success"]:
        append_log(task_id, f"安装失败: {install_result['message']}")
        result["status"] = "failed"
        result["steps"].append({"name": "安装APK", "status": "failed", "error": install_result["message"]})
        return result
    append_log(task_id, "APK 安装成功")
    result["steps"].append({"name": "安装APK", "status": "success"})

    # 启动
    if pkg.package_name:
        append_log(task_id, f"启动应用: {pkg.package_name}")
        try:
            subprocess.run(
                ["adb", "-s", task.device_serial, "shell", "monkey",
                 "-p", pkg.package_name, "-c", "android.intent.category.LAUNCHER", "1"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
            )
            append_log(task_id, "应用已启动")
            result["steps"].append({"name": "启动应用", "status": "success"})
        except Exception as e:
            append_log(task_id, f"启动失败: {e}")
            result["steps"].append({"name": "启动应用", "status": "failed", "error": str(e)})

    time.sleep(3)

    # 截图
    try:
        screenshot_path = os.path.join(REPORT_DIR, f"screenshot_{task_id}.png")
        with open(screenshot_path, "wb") as f:
            proc = subprocess.run(
                ["adb", "-s", task.device_serial, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=10
            )
            f.write(proc.stdout)
        append_log(task_id, "截图完成")
        result["steps"].append({"name": "截图验证", "status": "success"})
    except Exception as e:
        append_log(task_id, f"截图失败: {e}")
        result["steps"].append({"name": "截图验证", "status": "failed"})

    # Activity 检测
    try:
        proc = subprocess.run(
            ["adb", "-s", task.device_serial, "shell", "dumpsys", "activity", "activities"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        for line in proc.stdout.split("\n"):
            if "mResumedActivity" in line:
                append_log(task_id, f"当前 Activity: {line.strip()[:100]}")
                break
        result["steps"].append({"name": "Activity检测", "status": "success"})
    except Exception:
        result["steps"].append({"name": "Activity检测", "status": "failed"})

    return result


# ============================================================
# 通用工具
# ============================================================

def _update_task(db: Session, task: Task, status: str, error: str = ""):
    """更新任务状态"""
    task.status = status
    task.error = error
    if status in ("done", "failed", "cancelled"):
        task.finished_at = datetime.now()
    db.commit()


def _generate_report(task_id: int, pkg: Package, test_result: Dict, db: Session, task: Task) -> str:
    """生成 HTML 报告"""
    from app.services.report_service import generate_html_report

    # 获取设备型号
    device_model = ""
    try:
        device_info = device_service.get_device_info(task.device_serial)
        device_model = device_info.get("model", "")
    except Exception:
        pass

    # 获取日志用于解析功能模块
    with _task_logs_lock:
        logs = _task_logs.get(task_id, [])

    # testcase 生成的报告路径
    testcase_report_path = os.path.join(TESTCASE_PROJECT_DIR, "reports", f"report_task_{task_id}.html")

    report_path = generate_html_report(
        task_id, pkg, test_result,
        device_serial=task.device_serial,
        device_model=device_model,
        logs=logs,
        testcase_report_path=testcase_report_path
    )

    report = Report(
        task_id=task_id,
        package_name=pkg.package_name,
        status=test_result.get("status", "unknown"),
        html_path=report_path,
        summary=json.dumps(test_result, ensure_ascii=False),
    )
    db.add(report)
    db.commit()

    return report_path


def _check_batch_complete(batch_id: str, db: Session):
    """检查同批次任务是否全部完成，如果是则生成汇总报告"""
    batch_tasks = db.query(Task).filter(Task.batch_id == batch_id).all()
    if not batch_tasks:
        return

    # 检查是否都结束了
    all_finished = all(t.status in ("done", "failed", "cancelled") for t in batch_tasks)
    if not all_finished:
        return

    # 已有汇总报告则跳过
    existing = db.query(Report).filter(Report.batch_id == batch_id).first()
    if existing:
        return

    # 收集结果生成汇总报告
    from app.services.report_service import generate_batch_report

    pkg_results = []
    for t in batch_tasks:
        pkg = db.query(Package).filter(Package.id == t.package_id).first()
        logs = []
        try:
            logs = json.loads(t.logs) if t.logs else []
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

    report_path = generate_batch_report(batch_id, pkg_results)

    # 汇总状态
    all_done = all(r["status"] == "done" for r in pkg_results)
    any_done = any(r["status"] == "done" for r in pkg_results)
    overall_status = "success" if all_done else ("partial" if any_done else "failed")

    pkg_names = ", ".join(r["package_name"] for r in pkg_results)
    report = Report(
        batch_id=batch_id,
        package_name=f"批量测试 ({len(pkg_results)}个包)",
        status=overall_status,
        html_path=report_path,
        summary=json.dumps({"packages": pkg_names, "total": len(pkg_results)}, ensure_ascii=False),
    )
    db.add(report)
    db.commit()
    print(f"[Batch] 汇总报告已生成: {batch_id}")
