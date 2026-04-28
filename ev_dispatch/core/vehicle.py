from dataclasses import dataclass, field
from typing import List, Optional, Set
from datetime import datetime
from enum import Enum

from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork


class VehicleStatus(Enum):
    """车辆状态"""
    IDLE = "idle"              # 闲置，可派遣
    OCCUPIED = "occupied"      # 占用中（管理任务）
    CHARGING = "charging"      # 充电中
    MAINTENANCE = "maintenance"  # 维修中（断源中）
    EN_ROUTE = "en_route"      # 执行任务中


@dataclass
class VehicleType:
    """车型配置（模板）"""
    name: str                       # 车型名称：Compact/Standard/Large
    battery_capacity: float = 100.0  # kWh
    load_capacity: float = 1000.0    # kg
    volume_capacity: float = 5.0     # m^3
    max_speed_kmh: float = 80.0      # km/h
    charging_speed_kwh_per_hour: float = 50.0  # kWh/h
    efficiency: float = 0.15         # kWh/km
    supported_cargo_types: List[str] = field(default_factory=lambda: ["type_1"])  # 支持的货物类型


@dataclass
class Vehicle:
    """Fleet vehicle state."""

    id: str
    position: Location
    vehicle_type: VehicleType
    
    # 电池相关
    battery_capacity: float = field(default=None)
    current_battery: float = field(default=None)
    min_battery_threshold: float = 20.0  # 最低电量阈值（kWh）
    
    # 载控相关
    load_capacity: float = field(default=None)
    current_load: float = 0.0
    volume_capacity: float = field(default=None)
    current_volume: float = 0.0  # 当前体积占用(m^3)
    
    # 速度/效率
    max_speed_kmh: float = field(default=None)
    efficiency: float = field(default=None)
    current_speed_kmh: float = 40.0  # 当前驾行速度
    
    # 充电相关
    charging_speed_kwh_per_hour: float = field(default=None)
    
    # 货物类型限制
    supported_cargo_types: Set[str] = field(default_factory=lambda: {"type_1"})  # 支持的货物类型
    
    # 任务状态
    status: VehicleStatus = VehicleStatus.IDLE
    current_tasks: List[str] = field(default_factory=list)
    
    # 充电状态追踪
    is_charging: bool = False
    charging_station_id: Optional[str] = None
    charging_start_time: Optional[datetime] = None
    charging_duration: float = 0.0  # 分钟
    target_battery: float = 100.0

    def __post_init__(self):
        """Initial setup from vehicle_type defaults."""
        if self.battery_capacity is None:
            self.battery_capacity = self.vehicle_type.battery_capacity
        if self.current_battery is None:
            self.current_battery = self.vehicle_type.battery_capacity
        if self.load_capacity is None:
            self.load_capacity = self.vehicle_type.load_capacity
        if self.volume_capacity is None:
            self.volume_capacity = self.vehicle_type.volume_capacity
        if self.max_speed_kmh is None:
            self.max_speed_kmh = self.vehicle_type.max_speed_kmh
        if self.charging_speed_kwh_per_hour is None:
            self.charging_speed_kwh_per_hour = self.vehicle_type.charging_speed_kwh_per_hour
        if self.efficiency is None:
            self.efficiency = self.vehicle_type.efficiency
        # 初始化支持的货物类型
        self.supported_cargo_types = set(self.vehicle_type.supported_cargo_types)

    def can_reach(
        self,
        location: Location,
        reserve_energy: float = 5.0,
        network: Optional[RoadNetwork] = None,
    ) -> bool:
        """Check if vehicle can reach location with reserve energy."""
        if self.status == VehicleStatus.CHARGING or self.status == VehicleStatus.MAINTENANCE:
            return False
        
        dist = network.shortest_distance(self.position, location) if network else self.position.distance_to(location)
        energy_needed = dist * self.efficiency
        return self.current_battery >= (energy_needed + reserve_energy)

    def can_carry_task(self, weight: float, volume: float = 0.0, cargo_type: str = "type_1") -> bool:
        """Check if vehicle can carry task (weight, volume, and cargo type).
        
        Args:
            weight: Cargo weight (kg)
            volume: Cargo volume (m^3)
            cargo_type: Cargo type (must match supported types)
        
        Returns:
            True if vehicle can carry this task
        """
        # Check cargo type compatibility
        if cargo_type not in self.supported_cargo_types:
            return False
        
        # Check weight and volume
        weight_ok = (self.current_load + weight) <= self.load_capacity
        volume_ok = (self.current_volume + volume) <= self.volume_capacity
        return weight_ok and volume_ok

    def get_available_load_capacity(self) -> float:
        """Get remaining load capacity (kg)."""
        return self.load_capacity - self.current_load
    
    def get_available_volume_capacity(self) -> float:
        """Get remaining volume capacity (m^3)."""
        return self.volume_capacity - self.current_volume
    
    def get_utilization_rate(self) -> float:
        """Get vehicle utilization rate (0-1).
        Consider both weight and volume occupancy.
        """
        weight_util = self.current_load / self.load_capacity if self.load_capacity > 0 else 0
        volume_util = self.current_volume / self.volume_capacity if self.volume_capacity > 0 else 0
        return max(weight_util, volume_util)  # 按控制最严格的一个
    
    def set_status(self, new_status: VehicleStatus) -> None:
        """Update vehicle status."""
        self.status = new_status
    
    def is_available(self) -> bool:
        """Check if vehicle is available for dispatching."""
        return self.status == VehicleStatus.IDLE
    
    def start_charging(self, station_id: str, start_time: datetime) -> None:
        """开始充电"""
        self.is_charging = True
        self.charging_station_id = station_id
        self.charging_start_time = start_time
        self.status = VehicleStatus.CHARGING
        
        # 根据充电速度计算充电时间
        energy_needed = self.battery_capacity - self.current_battery
        hours = energy_needed / self.charging_speed_kwh_per_hour
        self.charging_duration = hours * 60  # 转换为分钟
        self.target_battery = self.battery_capacity
    
    def end_charging(self) -> None:
        """结束充电"""
        self.is_charging = False
        self.charging_station_id = None
        self.charging_start_time = None
        self.current_battery = self.target_battery
        self.status = VehicleStatus.IDLE
