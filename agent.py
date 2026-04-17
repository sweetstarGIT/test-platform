"""
分布式测试 Agent - 运行在用户本地机器上
负责检测本地 ADB 设备并与中央服务器保持 WebSocket 连接
"""
import asyncio
import json
import uuid
import subprocess
import re
import sys
from datetime import datetime
from websockets.asyncio.client import connect

SERVER_URL = "ws://localhost:8000/ws/agent"
HEARTBEAT_INTERVAL = 30
DEVICE_CHECK_INTERVAL = 10

class TestAgent:
    def __init__(self, server_url: str = None):
        self.agent_id = str(uuid.uuid4())[:8]
        self.server_url = server_url or SERVER_URL
        self.websocket = None
        self.running = True
        self.devices = []
        self.hostname = subprocess.run(
            ["hostname"], capture_output=True, text=True
        ).stdout.strip() or f"agent-{self.agent_id}"

    async def run(self):
        while self.running:
            try:
                await self.connect_and_run()
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 连接断开: {e}, 5秒后重连...")
                await asyncio.sleep(5)

    async def connect_and_run(self):
        async with connect(self.server_url) as ws:
            self.websocket = ws
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 已连接到服务器")

            await self.register()

            tasks = [
                asyncio.create_task(self.send_heartbeat()),
                asyncio.create_task(self.check_devices_loop()),
                asyncio.create_task(self.receive_commands()),
            ]

            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

    async def register(self):
        await self.send({
            "type": "register",
            "agent_id": self.agent_id,
            "hostname": self.hostname,
        })

    async def send_heartbeat(self):
        while self.running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self.websocket:
                try:
                    await self.send({
                        "type": "heartbeat",
                        "agent_id": self.agent_id,
                        "timestamp": datetime.now().isoformat(),
                    })
                except Exception:
                    break

    async def check_devices_loop(self):
        while self.running:
            await self.report_devices()
            await asyncio.sleep(DEVICE_CHECK_INTERVAL)

    async def report_devices(self):
        devices = self.get_adb_devices()
        if devices != self.devices or devices:
            self.devices = devices
            await self.send({
                "type": "device_update",
                "agent_id": self.agent_id,
                "devices": devices,
            })

    def get_adb_devices(self):
        try:
            result = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True, text=True, timeout=10
            )
            devices = []
            for line in (result.stdout or "").strip().split("\n")[1:]:
                line = line.strip()
                if not line or "offline" in line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    serial = parts[0]
                    status = parts[1]
                    device_info = {"serial": serial, "status": status}
                    model_match = re.search(r"model:(\S+)", line)
                    if model_match:
                        device_info["model"] = model_match.group(1)
                    transport = "usb" if "usb:" in line else "wifi"
                    device_info["transport"] = transport
                    device_info.update(self.get_device_props(serial))
                    devices.append(device_info)
            return devices
        except Exception as e:
            print(f"检测设备失败: {e}")
            return []

    def get_device_props(self, serial: str) -> dict:
        props = {}
        prop_map = {
            "brand": "ro.product.brand",
            "android_version": "ro.build.version.release",
            "sdk_version": "ro.build.version.sdk",
        }
        for key, prop in prop_map.items():
            try:
                result = subprocess.run(
                    ["adb", "-s", serial, "shell", "getprop", prop],
                    capture_output=True, text=True, timeout=3
                )
                props[key] = (result.stdout or "").strip()
            except Exception:
                props[key] = ""
        return props

    async def receive_commands(self):
        while self.running and self.websocket:
            try:
                message = await self.websocket.recv()
                await self.handle_command(json.loads(message))
            except Exception:
                break

    async def handle_command(self, cmd: dict):
        cmd_type = cmd.get("type")
        if cmd_type == "execute_task":
            await self.execute_task(cmd)
        elif cmd_type == "ping":
            await self.send({"type": "pong", "agent_id": self.agent_id})
        elif cmd_type == "disconnect":
            self.running = False

    async def execute_task(self, cmd: dict):
        task_id = cmd.get("task_id")
        device_serial = cmd.get("device_serial")
        package_path = cmd.get("package_path")
        print(f"执行任务 {task_id}: 设备={device_serial}, 包={package_path}")

        logs = []
        try:
            await self.send_log(task_id, "正在安装 APK...")
            result = subprocess.run(
                ["adb", "-s", device_serial, "install", "-r", package_path],
                capture_output=True, text=True, timeout=120
            )
            install_output = (result.stdout or "").strip()
            await self.send_log(task_id, install_output or "安装完成")

            if "success" in install_output.lower():
                await self.send_task_result(task_id, "done", logs, "测试完成")
            else:
                await self.send_task_result(task_id, "failed", logs, install_output)
        except Exception as e:
            await self.send_task_result(task_id, "failed", logs, str(e))

    async def send_log(self, task_id: int, message: str):
        if self.websocket:
            await self.send({
                "type": "task_log",
                "task_id": task_id,
                "log": message,
                "timestamp": datetime.now().isoformat(),
            })

    async def send_task_result(self, task_id: int, status: str, logs: list, error: str = ""):
        if self.websocket:
            await self.send({
                "type": "task_result",
                "task_id": task_id,
                "status": status,
                "logs": logs,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            })

    async def send(self, data: dict):
        if self.websocket:
            await self.websocket.send(json.dumps(data))


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="分布式测试 Agent")
    parser.add_argument("--server", "-s", default=SERVER_URL, help="中央服务器 WebSocket 地址")
    args = parser.parse_args()

    agent = TestAgent(server_url=args.server)
    print(f"=" * 50)
    print(f"测试 Agent 启动")
    print(f"Agent ID: {agent.agent_id}")
    print(f"服务器: {agent.server_url}")
    print(f"=" * 50)
    await agent.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAgent 已停止")