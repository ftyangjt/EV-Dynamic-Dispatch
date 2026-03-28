from dataclasses import dataclass, field
from typing import List

from ev_dispatch.core.location import Location


@dataclass
class ChargingStation:
    """Charging station with limited chargers and queue."""

    id: str
    position: Location
    num_chargers: int = 2
    charging_power: float = 50.0
    queue: List[str] = field(default_factory=list)
    current_charge_count: int = 0

    def get_wait_time(self) -> float:
        return float(max(0, (len(self.queue) - self.num_chargers) * 30))

    def get_charge_time(self, energy_needed: float) -> float:
        return float((energy_needed / self.charging_power) * 60)
