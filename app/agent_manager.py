"""Agent 连接管理器 - 中央服务器端"""
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class AgentInfo:
    agent_id: str
    hostname: str
    devices: List[Dict] = field(default_factory=list)
    last_heartbeat: datetime = field(default_factory=datetime.now)
    websocket: Any = field(default=None)


class AgentManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.agents = {}
            cls._instance.device_to_agent = {}
            cls._instance.lock = asyncio.Lock()
        return cls._instance

    async def register(self, agent_id: str, hostname: str, websocket=None) -> AgentInfo:
        async with self.lock:
            agent = AgentInfo(agent_id=agent_id, hostname=hostname, websocket=websocket)
            self.agents[agent_id] = agent
            return agent

    async def unregister(self, agent_id: str):
        async with self.lock:
            if agent_id in self.agents:
                agent = self.agents[agent_id]
                for device in agent.devices:
                    serial = device.get("serial")
                    if serial and self.device_to_agent.get(serial) == agent_id:
                        del self.device_to_agent[serial]
                del self.agents[agent_id]

    async def update_devices(self, agent_id: str, devices: List[Dict]):
        async with self.lock:
            if agent_id not in self.agents:
                return
            agent = self.agents[agent_id]
            agent.devices = devices
            agent.last_heartbeat = datetime.now()

            for device in devices:
                serial = device.get("serial")
                if serial:
                    self.device_to_agent[serial] = agent_id

    async def heartbeat(self, agent_id: str):
        async with self.lock:
            if agent_id in self.agents:
                self.agents[agent_id].last_heartbeat = datetime.now()

    async def get_all_devices(self) -> List[Dict]:
        async with self.lock:
            result = []
            for agent in self.agents.values():
                for device in agent.devices:
                    device_copy = device.copy()
                    device_copy["agent_id"] = agent.agent_id
                    device_copy["agent_hostname"] = agent.hostname
                    result.append(device_copy)
            return result

    async def get_device_agent(self, serial: str) -> Optional[AgentInfo]:
        async with self.lock:
            agent_id = self.device_to_agent.get(serial)
            if agent_id:
                return self.agents.get(agent_id)
            return None

    async def get_online_agents(self) -> List[Dict]:
        async with self.lock:
            return [
                {
                    "agent_id": a.agent_id,
                    "hostname": a.hostname,
                    "device_count": len(a.devices),
                    "last_heartbeat": a.last_heartbeat.isoformat(),
                }
                for a in self.agents.values()
            ]

    async def cleanup_stale_agents(self, timeout_seconds: int = 90):
        async with self.lock:
            now = datetime.now()
            stale = [
                aid for aid, agent in self.agents.items()
                if (now - agent.last_heartbeat).total_seconds() > timeout_seconds
            ]
            for aid in stale:
                await self.unregister(aid)
            return len(stale)


agent_manager = AgentManager()
