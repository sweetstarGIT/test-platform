"""设备负载均衡器 - 智能分配任务到最优设备

策略：
- least_tasks: 最少运行中任务优先
- round_robin: 轮询
- weighted: 权重（可结合设备性能）
"""
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from enum import Enum

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Task
from app.services import device_service


class BalanceStrategy(Enum):
    LEAST_TASKS = "least_tasks"  # 最少任务优先（默认）
    ROUND_ROBIN = "round_robin"  # 轮询
    WEIGHTED = "weighted"        # 权重（预留）


@dataclass
class DeviceStats:
    """设备实时状态"""
    serial: str
    model: str = ""
    running_tasks: int = 0
    queued_tasks: int = 0
    total_tasks: int = 0
    last_used: float = field(default_factory=time.time)
    weight: int = 1  # 权重，高性能设备可设为更高


class DeviceLoadBalancer:
    """设备负载均衡器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._devices: Dict[str, DeviceStats] = {}
        self._round_robin_queue: deque = deque()
        self._strategy: BalanceStrategy = BalanceStrategy.LEAST_TASKS
        self._task_count = 0  # 用于轮询计数

    def set_strategy(self, strategy: BalanceStrategy):
        """设置负载均衡策略"""
        with self._lock:
            self._strategy = strategy
            print(f"[LoadBalancer] 策略切换为: {strategy.value}")

    def refresh_devices(self) -> List[str]:
        """刷新在线设备列表，返回新发现的设备"""
        devices = device_service.list_devices()
        online_serials = set()
        new_devices = []

        for d in devices:
            if "error" in d or d.get("status") != "device":
                continue
            serial = d["serial"]
            online_serials.add(serial)

            with self._lock:
                if serial not in self._devices:
                    stats = DeviceStats(
                        serial=serial,
                        model=d.get("model", ""),
                    )
                    self._devices[serial] = stats
                    self._round_robin_queue.append(serial)
                    new_devices.append(serial)
                    print(f"[LoadBalancer] 发现新设备: {serial} ({stats.model})")

        # 清理离线设备
        with self._lock:
            offline = set(self._devices.keys()) - online_serials
            for serial in offline:
                del self._devices[serial]
                # 从轮询队列移除
                try:
                    self._round_robin_queue.remove(serial)
                except ValueError:
                    pass
                print(f"[LoadBalancer] 设备离线移除: {serial}")

        return new_devices

    def sync_task_status(self):
        """从数据库同步任务状态，确保数据一致性"""
        db = SessionLocal()
        try:
            running_tasks = db.query(Task).filter(Task.status == "running").all()
            pending_tasks = db.query(Task).filter(Task.status == "pending").all()

            # 重置所有设备计数
            with self._lock:
                for stats in self._devices.values():
                    stats.running_tasks = 0
                    stats.queued_tasks = 0

                # 统计运行中
                for t in running_tasks:
                    if t.device_serial in self._devices:
                        self._devices[t.device_serial].running_tasks += 1

                # 统计队列中（pending 但未开始）
                for t in pending_tasks:
                    if t.device_serial in self._devices:
                        self._devices[t.device_serial].queued_tasks += 1

                # 计算总负载
                for stats in self._devices.values():
                    stats.total_tasks = stats.running_tasks + stats.queued_tasks

        finally:
            db.close()

    def select_device(self, exclude_devices: List[str] = None) -> Optional[str]:
        """
        选择最优设备

        Args:
            exclude_devices: 排除的设备（用于重试时避免重复选择）

        Returns:
            选中的设备 serial，无可用设备返回 None
        """
        exclude = set(exclude_devices or [])

        with self._lock:
            candidates = [
                d for d in self._devices.values()
                if d.serial not in exclude
            ]

            if not candidates:
                return None

            if self._strategy == BalanceStrategy.LEAST_TASKS:
                # 最少任务优先，相同则选最近最少使用
                best = min(candidates, key=lambda d: (d.total_tasks, time.time() - d.last_used))
                best.last_used = time.time()
                return best.serial

            elif self._strategy == BalanceStrategy.ROUND_ROBIN:
                # 轮询：找到下一个可用设备
                attempts = 0
                while attempts < len(self._round_robin_queue):
                    serial = self._round_robin_queue.popleft()
                    self._round_robin_queue.append(serial)
                    if serial in self._devices and serial not in exclude:
                        return serial
                    attempts += 1
                return None

            elif self._strategy == BalanceStrategy.WEIGHTED:
                # 加权随机（简单实现：按权重重复设备后随机选）
                weighted_list = []
                for d in candidates:
                    weighted_list.extend([d.serial] * d.weight)
                import random
                return random.choice(weighted_list) if weighted_list else None

            return None

    def get_device_load(self) -> List[Dict]:
        """获取所有设备负载情况"""
        with self._lock:
            return [
                {
                    "serial": d.serial,
                    "model": d.model,
                    "running": d.running_tasks,
                    "queued": d.queued_tasks,
                    "total": d.total_tasks,
                    "weight": d.weight,
                    "utilization": min(100, d.total_tasks * 20),  # 估算利用率
                }
                for d in self._devices.values()
            ]

    def set_device_weight(self, serial: str, weight: int):
        """设置设备权重（用于加权策略）"""
        with self._lock:
            if serial in self._devices:
                self._devices[serial].weight = max(1, weight)

    def get_stats(self) -> Dict:
        """获取负载均衡器统计"""
        # 先刷新设备列表，确保数据最新
        self.refresh_devices()
        self.sync_task_status()

        with self._lock:
            loads = [d.total_tasks for d in self._devices.values()]
            devices_load = [
                {
                    "serial": d.serial,
                    "model": d.model,
                    "running": d.running_tasks,
                    "queued": d.queued_tasks,
                    "total": d.total_tasks,
                    "weight": d.weight,
                    "utilization": min(100, d.total_tasks * 20),
                }
                for d in self._devices.values()
            ]
            return {
                "strategy": self._strategy.value,
                "online_devices": len(self._devices),
                "avg_load": sum(loads) / len(loads) if loads else 0,
                "max_load": max(loads) if loads else 0,
                "devices": devices_load,
            }


# 全局负载均衡器实例
load_balancer = DeviceLoadBalancer()


def auto_assign_device(exclude_devices: List[str] = None) -> Optional[str]:
    """
    自动分配设备的便捷函数

    使用示例:
        serial = auto_assign_device()
        if not serial:
            raise HTTPException(400, "无可用设备")
    """
    # 先刷新设备列表
    load_balancer.refresh_devices()

    # 同步最新任务状态
    load_balancer.sync_task_status()

    # 选择最优设备
    return load_balancer.select_device(exclude_devices)
