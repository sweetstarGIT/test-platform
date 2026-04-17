"""设备管理路由"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Dict
import json

from app.services import device_service
from app.agent_manager import agent_manager

router = APIRouter()


class ConnectRequest(BaseModel):
    address: str


class AgentConnectRequest(BaseModel):
    agent_id: str


@router.get("")
async def list_devices():
    agent_devices = await agent_manager.get_all_devices()
    if agent_devices:
        return agent_devices
    return device_service.list_devices()


@router.get("/agents")
async def list_agents():
    return await agent_manager.get_online_agents()


@router.post("/connect")
def connect_device(req: ConnectRequest):
    return device_service.connect_wifi(req.address)


@router.post("/disconnect")
def disconnect_device(req: ConnectRequest):
    return device_service.disconnect_wifi(req.address)


@router.get("/{serial}/info")
async def device_info(serial: str):
    agent = await agent_manager.get_device_agent(serial)
    if agent:
        for device in agent.devices:
            if device.get("serial") == serial:
                return {
                    "serial": serial,
                    "agent_id": agent.agent_id,
                    "agent_hostname": agent.hostname,
                    **device
                }
    return device_service.get_device_info(serial)
