"""设备管理服务 - ADB 设备检测和连接"""
import subprocess
import re
from typing import List, Dict


def list_devices() -> List[Dict]:
    """列出所有已连接的 ADB 设备（自动去重：同一物理设备只显示一次）"""
    try:
        result = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        output = result.stdout or ""
        raw_devices = []
        for line in output.strip().split("\n")[1:]:
            line = line.strip()
            if not line or "offline" in line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                status = parts[1]
                # 解析额外信息
                model = ""
                device = ""
                model_match = re.search(r"model:(\S+)", line)
                if model_match:
                    model = model_match.group(1)
                device_match = re.search(r"device:(\S+)", line)
                if device_match:
                    device = device_match.group(1)
                # 判断连接方式：IP:端口 或 _tcp 结尾的是 WiFi，其余为 USB
                if ':' in serial or '_tcp' in serial:
                    transport = "wifi"
                else:
                    transport = "usb"
                raw_devices.append({
                    "serial": serial,
                    "status": status,
                    "model": model,
                    "device": device,
                    "transport": transport,
                })

        # 去重：按 model+device 分组，优先保留标准 IP 格式的连接
        device_groups = {}
        for d in raw_devices:
            key = f"{d['model']}:{d['device']}" if d['device'] else d['serial']
            if key not in device_groups:
                device_groups[key] = d
            else:
                # 已存在，保留 IP 格式的（更直观稳定）
                existing = device_groups[key]
                if ':' in d['serial'] and d['serial'].replace('.', '').replace(':', '').isdigit():
                    # 新的是 IP:端口 格式，优先使用
                    device_groups[key] = d

        # 返回去重后的列表，移除内部字段
        devices = [{k: v for k, v in d.items() if k != 'device'}
                   for d in device_groups.values()]
        return devices
    except Exception as e:
        return [{"error": str(e)}]


def connect_wifi(address: str) -> Dict:
    """WiFi ADB 连接设备"""
    try:
        result = subprocess.run(
            ["adb", "connect", address],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
        )
        output = (result.stdout or "").strip() + (result.stderr or "").strip()
        success = "connected" in output.lower()
        return {"success": success, "message": output or "无输出"}
    except FileNotFoundError:
        return {"success": False, "message": "未找到 adb 命令，请确认本机已安装 ADB"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def disconnect_wifi(address: str) -> Dict:
    """断开 WiFi ADB 连接"""
    try:
        result = subprocess.run(
            ["adb", "disconnect", address],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        output = (result.stdout or "").strip()
        return {"success": True, "message": output or "已断开"}
    except FileNotFoundError:
        return {"success": False, "message": "未找到 adb 命令"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_device_info(serial: str) -> Dict:
    """获取设备详细信息"""
    info = {"serial": serial}
    props = {
        "model": "ro.product.model",
        "brand": "ro.product.brand",
        "android_version": "ro.build.version.release",
        "sdk_version": "ro.build.version.sdk",
    }
    for key, prop in props.items():
        try:
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "getprop", prop],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
            )
            info[key] = (result.stdout or "").strip()
        except Exception:
            info[key] = ""
    return info


def install_apk(serial: str, apk_path: str) -> Dict:
    """安装 APK 到设备"""
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "install", "-r", apk_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
        success = "success" in (result.stdout or "").lower()
        return {"success": success, "message": (result.stdout or "").strip() or "无输出"}
    except Exception as e:
        return {"success": False, "message": str(e)}
