from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Set

from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork


class VehicleStatus(Enum):
    """Vehicle status in the simulation."""

    IDLE = "idle"
    OCCUPIED = "occupied"
    CHARGING = "charging"
    MAINTENANCE = "maintenance"
    EN_ROUTE = "en_route"


@dataclass
class VehicleType:
    """Vehicle type template."""

    name: str
    battery_capacity: float = 100.0  # kWh
    load_capacity: float = 1000.0  # kg
    volume_capacity: float = 5.0  # m^3
    max_speed_kmh: float = 80.0
    charging_speed_kwh_per_hour: float = 50.0
    efficiency: float = 0.15  # kWh/km
    # City operation reserve policy.
    min_battery_reserve_ratio: float = 0.20
    min_battery_reserve_kwh: float = 12.0
    supported_cargo_types: List[str] = field(default_factory=lambda: ["type_1"])


@dataclass
class Vehicle:
    """Fleet vehicle state."""

    id: str
    position: Location
    vehicle_type: VehicleType

    # Battery
    battery_capacity: Optional[float] = field(default=None)
    current_battery: Optional[float] = field(default=None)
    min_battery_threshold: Optional[float] = None

    # Payload
    load_capacity: Optional[float] = field(default=None)
    current_load: float = 0.0
    volume_capacity: Optional[float] = field(default=None)
    current_volume: float = 0.0

    # Vehicle dynamics
    max_speed_kmh: Optional[float] = field(default=None)
    efficiency: Optional[float] = field(default=None)
    current_speed_kmh: float = 40.0

    # Charging
    charging_speed_kwh_per_hour: Optional[float] = field(default=None)

    # Cargo constraints
    supported_cargo_types: Set[str] = field(default_factory=lambda: {"type_1"})

    # Task status
    status: VehicleStatus = VehicleStatus.IDLE
    current_tasks: List[str] = field(default_factory=list)

    # Charging tracking
    is_charging: bool = False
    charging_station_id: Optional[str] = None
    charging_start_time: Optional[datetime] = None
    charging_duration: float = 0.0  # minutes
    target_battery: float = 100.0

    def __post_init__(self) -> None:
        """Initialize runtime defaults from the vehicle type."""
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

        if self.min_battery_threshold is None:
            reserve_from_ratio = self.battery_capacity * float(self.vehicle_type.min_battery_reserve_ratio)
            reserve = max(float(self.vehicle_type.min_battery_reserve_kwh), reserve_from_ratio)
            self.min_battery_threshold = min(reserve, self.battery_capacity * 0.45)
        self.min_battery_threshold = max(0.0, min(float(self.min_battery_threshold), self.battery_capacity))

        self.supported_cargo_types = set(self.vehicle_type.supported_cargo_types)

    def can_reach(
        self,
        location: Location,
        reserve_energy: float = 5.0,
        network: Optional[RoadNetwork] = None,
    ) -> bool:
        """Check whether the vehicle can reach a location with reserve."""
        if self.status in (VehicleStatus.CHARGING, VehicleStatus.MAINTENANCE):
            return False

        dist = network.shortest_distance(self.position, location) if network else self.position.distance_to(location)
        energy_needed = dist * self.efficiency
        return self.current_battery >= (energy_needed + reserve_energy)

    def can_carry_task(self, weight: float, volume: float = 0.0, cargo_type: str = "type_1") -> bool:
        """Check cargo type, weight, and volume constraints."""
        if cargo_type not in self.supported_cargo_types:
            return False
        weight_ok = (self.current_load + weight) <= self.load_capacity
        volume_ok = (self.current_volume + volume) <= self.volume_capacity
        return weight_ok and volume_ok

    def get_available_load_capacity(self) -> float:
        return self.load_capacity - self.current_load

    def get_available_volume_capacity(self) -> float:
        return self.volume_capacity - self.current_volume

    def get_utilization_rate(self) -> float:
        """Use the stricter one between weight and volume utilization."""
        weight_util = self.current_load / self.load_capacity if self.load_capacity > 0 else 0.0
        volume_util = self.current_volume / self.volume_capacity if self.volume_capacity > 0 else 0.0
        return max(weight_util, volume_util)

    def set_status(self, new_status: VehicleStatus) -> None:
        self.status = new_status

    def is_available(self) -> bool:
        return self.status == VehicleStatus.IDLE

    def start_charging(self, station_id: str, start_time: datetime) -> None:
        self.is_charging = True
        self.charging_station_id = station_id
        self.charging_start_time = start_time
        self.status = VehicleStatus.CHARGING

        energy_needed = self.battery_capacity - self.current_battery
        hours = energy_needed / self.charging_speed_kwh_per_hour
        self.charging_duration = hours * 60.0
        self.target_battery = self.battery_capacity

    def end_charging(self) -> None:
        self.is_charging = False
        self.charging_station_id = None
        self.charging_start_time = None
        self.current_battery = self.target_battery
        self.status = VehicleStatus.IDLE
