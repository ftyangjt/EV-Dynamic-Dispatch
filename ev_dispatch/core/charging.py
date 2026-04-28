from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime
from enum import Enum

from ev_dispatch.core.location import Location


class QueuePolicy(Enum):
    """排队规则"""
    FIFO = "fifo"              # First-In-First-Out (先来先服务)


@dataclass
class ChargingRecord:
    """充电车辆记录"""
    vehicle_id: str
    start_time: datetime
    duration_minutes: float      # 充电耗时（分钟）
    target_energy: float        # 目标电量(kWh)
    end_time: Optional[datetime] = None


@dataclass
class ChargingStation:
    """Charging station with queue management (FIFO)."""

    id: str
    position: Location
    num_chargers: int = 2                               # 充电器数量（容量）
    charging_power: float = 50.0                        # kWh/h
    
    # 排队管理
    queue_policy: QueuePolicy = QueuePolicy.FIFO
    waiting_queue: List[str] = field(default_factory=list)  # 等待排队
    charging_vehicles: Dict[str, ChargingRecord] = field(default_factory=dict)  # 正在充电

    def enqueue(self, vehicle_id: str) -> int:
        """
        将车加入排队（先来先服务）
        
        Args:
            vehicle_id: 车辆ID
        
        Returns:
            车辆在排队中的位置 (0 = 下一个需要充电)
        """
        if vehicle_id not in self.waiting_queue:
            self.waiting_queue.append(vehicle_id)
        return self.get_queue_position(vehicle_id)

    def dequeue(self) -> Optional[str]:
        """
        从排队出队，并转为充电状态
        
        Returns:
            易出队的车ID，如果排队为空则返回 None
        """
        if self.waiting_queue and len(self.charging_vehicles) < self.num_chargers:
            vehicle_id = self.waiting_queue.pop(0)
            return vehicle_id
        return None

    def get_queue_position(self, vehicle_id: str) -> int:
        """
        查询车辆在排队中的位置
        
        Args:
            vehicle_id: 车辆ID
        
        Returns:
            位置 (0=即将开始，1=排第二，-1=不在队中)
        """
        if vehicle_id in self.charging_vehicles:
            return -2  # 已经在充电
        
        if vehicle_id in self.waiting_queue:
            return self.waiting_queue.index(vehicle_id)
        
        return -1  # 不在会场

    def start_charging(
        self,
        vehicle_id: str,
        target_energy: float,
        start_time: datetime,
    ) -> bool:
        """
        开始给车辆充电
        
        Args:
            vehicle_id: 车辆ID
            target_energy: 目标电量 (kWh)
            start_time: 开始时间
        
        Returns:
            是否成功（容量未盛时返回 False）
        """
        # 检查是否有空充电器
        if len(self.charging_vehicles) >= self.num_chargers:
            return False
        
        # 计算充电时间
        duration_hours = target_energy / self.charging_power
        duration_minutes = duration_hours * 60
        
        # 记录充电信息
        self.charging_vehicles[vehicle_id] = ChargingRecord(
            vehicle_id=vehicle_id,
            start_time=start_time,
            duration_minutes=duration_minutes,
            target_energy=target_energy,
        )
        
        return True

    def complete_charging(self, vehicle_id: str, end_time: datetime) -> Optional[ChargingRecord]:
        """
        完成车辆的充电
        
        Args:
            vehicle_id: 车辆ID
            end_time: 结束时间
        
        Returns:
            充电记录，如果车没有在充电则返回 None
        """
        if vehicle_id not in self.charging_vehicles:
            return None
        
        record = self.charging_vehicles.pop(vehicle_id)
        record.end_time = end_time
        return record

    def get_wait_time(self) -> float:
        """
        Calculate estimated wait time (minutes).
        
        Returns:
            Maximum waiting time among vehicles in queue
        """
        # If there are available chargers, no waiting
        if len(self.charging_vehicles) < self.num_chargers:
            return 0.0
        
        # Otherwise, check the minimum finish time among charging vehicles
        if not self.charging_vehicles:
            return 0.0
        
        # Wait time = time until earliest vehicle finishes
        min_duration = min(r.duration_minutes for r in self.charging_vehicles.values())
        return max(0.0, min_duration)

    def get_charge_time(self, energy_needed: float) -> float:
        """
        Calculate charging time for specific energy amount.
        
        Args:
            energy_needed: Required energy (kWh)
        
        Returns:
            Charging time (minutes)
        """
        hours = energy_needed / self.charging_power
        return float(hours * 60)

    def get_queue_length(self) -> int:
        """Get total queue length (including vehicles being charged)."""
        return len(self.waiting_queue) + len(self.charging_vehicles)

    def get_status(self) -> Dict:
        """Get charging station status."""
        return {
            "station_id": self.id,
            "chargers_available": self.num_chargers - len(self.charging_vehicles),
            "charging_now": len(self.charging_vehicles),
            "waiting_queue_length": len(self.waiting_queue),
            "total_queue_length": self.get_queue_length(),
            "wait_time_minutes": self.get_wait_time(),
        }
    
