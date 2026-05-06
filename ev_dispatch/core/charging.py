from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from ev_dispatch.core.location import Location


class QueuePolicy(Enum):
    """Queue policy for charging stations."""

    FIFO = "fifo"


@dataclass
class ChargingRecord:
    """Single charging record."""

    vehicle_id: str
    start_time: datetime
    duration_minutes: float
    target_energy: float
    end_time: Optional[datetime] = None


@dataclass
class ChargingStation:
    """Charging station with simple queueing model."""

    id: str
    position: Location
    # City baseline: more than one pile, with DC charging as default.
    num_chargers: int = 3
    charging_power: float = 90.0  # kWh/h ~= kW
    queue_policy: QueuePolicy = QueuePolicy.FIFO
    waiting_queue: List[str] = field(default_factory=list)
    charging_vehicles: Dict[str, ChargingRecord] = field(default_factory=dict)

    def enqueue(self, vehicle_id: str) -> int:
        if vehicle_id not in self.waiting_queue:
            self.waiting_queue.append(vehicle_id)
        return self.get_queue_position(vehicle_id)

    def dequeue(self) -> Optional[str]:
        if self.waiting_queue and len(self.charging_vehicles) < self.num_chargers:
            return self.waiting_queue.pop(0)
        return None

    def get_queue_position(self, vehicle_id: str) -> int:
        if vehicle_id in self.charging_vehicles:
            return -2
        if vehicle_id in self.waiting_queue:
            return self.waiting_queue.index(vehicle_id)
        return -1

    def start_charging(
        self,
        vehicle_id: str,
        target_energy: float,
        start_time: datetime,
    ) -> bool:
        if len(self.charging_vehicles) >= self.num_chargers:
            return False

        power = max(1e-6, float(self.charging_power))
        duration_hours = max(0.0, float(target_energy)) / power
        duration_minutes = duration_hours * 60.0

        self.charging_vehicles[vehicle_id] = ChargingRecord(
            vehicle_id=vehicle_id,
            start_time=start_time,
            duration_minutes=duration_minutes,
            target_energy=float(target_energy),
        )
        return True

    def complete_charging(self, vehicle_id: str, end_time: datetime) -> Optional[ChargingRecord]:
        if vehicle_id not in self.charging_vehicles:
            return None
        record = self.charging_vehicles.pop(vehicle_id)
        record.end_time = end_time
        return record

    def get_wait_time(self) -> float:
        """
        Estimate waiting time (minutes).
        Includes:
        - residual time of currently charging vehicles
        - additional queue rounds for vehicles not yet charging
        """
        in_service = len(self.charging_vehicles)
        queue_len = len(self.waiting_queue)

        if in_service < self.num_chargers:
            return 0.0
        if in_service == 0:
            return 0.0

        active = sorted(max(0.0, r.duration_minutes) for r in self.charging_vehicles.values())
        earliest_finish = active[0]

        if queue_len <= 0:
            return float(earliest_finish)

        avg_service = sum(active) / max(1, len(active))
        full_rounds = queue_len / max(1, self.num_chargers)
        return float(earliest_finish + full_rounds * avg_service)

    def get_charge_time(self, energy_needed: float) -> float:
        power = max(1e-6, float(self.charging_power))
        return float(max(0.0, energy_needed) / power * 60.0)

    def get_queue_length(self) -> int:
        return len(self.waiting_queue) + len(self.charging_vehicles)

    def get_status(self) -> Dict:
        return {
            "station_id": self.id,
            "chargers_available": self.num_chargers - len(self.charging_vehicles),
            "charging_now": len(self.charging_vehicles),
            "waiting_queue_length": len(self.waiting_queue),
            "total_queue_length": self.get_queue_length(),
            "wait_time_minutes": self.get_wait_time(),
            "charging_power": self.charging_power,
        }
