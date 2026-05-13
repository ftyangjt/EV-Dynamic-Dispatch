from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from enum import Enum

from ev_dispatch.core.location import Location


class CargoType(Enum):
    """货物类型
    
    TYPE_1: 通用货物（所有车都能装）
    TYPE_2, TYPE_3, ...: 专用货物（仅对应类型的车能装）
    """
    TYPE_1 = "type_1"  # 通用
    TYPE_2 = "type_2"  # 专用：仅compact车
    TYPE_3 = "type_3"  # 专用：仅standard车
    TYPE_4 = "type_4"  # 专用：仅large车


@dataclass
class Task:
    """Delivery task generated during simulation."""

    id: str
    origin: Location
    destination: Location
    weight: float
    created_time: datetime
    deadline: datetime
    # 任务开始进入运输/调度系统的时间（用于区分“创建时间”和“进入运输队列/可被调度时间”）
    start_transport_time: Optional[datetime] = None
    cargo_type: CargoType = CargoType.TYPE_1  # 货物类型
    priority: float = 1.0
    completed: bool = False
    assigned_vehicles: List[str] = field(default_factory=list)
    volume: float = 0.5  # m^3，货物体积
    failed: bool = False
    failure_reason: str = ""

    def get_delivery_distance(self) -> float:
        return self.origin.distance_to(self.destination)

    def is_overdue(self, current_time: datetime) -> bool:
        return current_time > self.deadline
