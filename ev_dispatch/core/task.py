from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from ev_dispatch.core.location import Location


@dataclass
class Task:
    """Delivery task generated during simulation."""

    id: str
    origin: Location
    destination: Location
    weight: float
    created_time: datetime
    deadline: datetime
    priority: float = 1.0
    completed: bool = False
    assigned_vehicles: List[str] = field(default_factory=list)

    def get_delivery_distance(self) -> float:
        return self.origin.distance_to(self.destination)

    def is_overdue(self, current_time: datetime) -> bool:
        return current_time > self.deadline
