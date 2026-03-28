from dataclasses import dataclass, field
from typing import List

from ev_dispatch.core.location import Location


@dataclass
class Vehicle:
    """Fleet vehicle state."""

    id: str
    position: Location
    battery_capacity: float = 100.0
    current_battery: float = 100.0
    load_capacity: float = 1000.0
    current_load: float = 0.0
    efficiency: float = 0.15
    current_tasks: List[str] = field(default_factory=list)

    def can_reach(self, location: Location, reserve_energy: float = 5.0) -> bool:
        dist = self.position.distance_to(location)
        energy_needed = dist * self.efficiency
        return self.current_battery >= (energy_needed + reserve_energy)

    def get_available_capacity(self) -> float:
        return self.load_capacity - self.current_load
