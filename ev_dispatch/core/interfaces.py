import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.task import Task
from ev_dispatch.core.vehicle import Vehicle
from ev_dispatch.core.charging import ChargingStation


@dataclass
class Action:
    """调度器输出给仿真器的动作指令。"""

    type: str
    vehicle_id: str
    task_id: Optional[str] = None
    station_id: Optional[str] = None
    note: str = ""


class SimulationState:
    """仿真引擎传给调度器的只读环境快照。"""

    def __init__(
        self,
        current_time: datetime,
        tasks: List[Task],
        vehicles: List[Vehicle],
        charging_stations: List[ChargingStation],
        network: RoadNetwork,
        extra_info: Optional[Dict[str, Any]] = None,
    ):
        self.current_time = current_time
        self.pending_tasks = [t for t in tasks if not t.completed]
        self.vehicles = vehicles
        self.charging_stations = charging_stations
        self.network = network
        self.extra_info = extra_info or {}


@dataclass
class SimulationFrame:
    """给可视化层使用的一帧快照。"""

    current_time: datetime
    step: int
    vehicle_positions: Dict[str, tuple]
    vehicle_battery: Dict[str, float]
    pending_task_ids: List[str]
    completed_task_ids: List[str]
    failed_task_ids: List[str]


class AbstractDispatcher(abc.ABC):
    """
    统一的调度算法基类
    算法负责人(A)的任何新策略都应当继承此基类。
    仿真系统(B)通过调用 `generate_actions` 获取当前帧决策。
    """
    
    def __init__(self, network: RoadNetwork):
        self.network = network

    @abc.abstractmethod
    def generate_actions(self, state: SimulationState) -> List[Action]:
        """
        根据当前状态生成动作指令
        
        Args:
            state: 当前仿真时刻的环境快照
            
        Returns:
            一系列具体操作指令的列表
        """
        raise NotImplementedError
