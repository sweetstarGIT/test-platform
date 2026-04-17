"""FastAPI 入口"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket
import os
import asyncio
import json

from app.database import init_db
from app.config import BASE_DIR
from app.routers import packages, devices, tasks, reports
from app.agent_manager import agent_manager

app = FastAPI(title="自动化测试平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(packages.router, prefix="/api/packages", tags=["包管理"])
app.include_router(devices.router, prefix="/api/devices", tags=["设备管理"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["测试任务"])
app.include_router(reports.router, prefix="/api/reports", tags=["测试报告"])

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(agent_cleanup_loop())


async def agent_cleanup_loop():
    while True:
        await asyncio.sleep(60)
        count = await agent_manager.cleanup_stale_agents()
        if count > 0:
            print(f"清理了 {count} 个离线 Agent")


@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    agent_id = None
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "register":
                agent_id = msg.get("agent_id")
                hostname = msg.get("hostname", "unknown")
                await agent_manager.register(agent_id, hostname, websocket)
                print(f"Agent 注册: {agent_id} ({hostname})")

            elif msg.get("type") == "heartbeat":
                agent_id = msg.get("agent_id")
                await agent_manager.heartbeat(agent_id)

            elif msg.get("type") == "device_update":
                agent_id = msg.get("agent_id")
                devices_list = msg.get("devices", [])
                await agent_manager.update_devices(agent_id, devices_list)

            elif msg.get("type") == "task_log":
                pass

            elif msg.get("type") == "task_result":
                pass

    except Exception as e:
        print(f"WebSocket 错误: {e}")
    finally:
        if agent_id:
            await agent_manager.unregister(agent_id)
            print(f"Agent 断开: {agent_id}")


@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))
