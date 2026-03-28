from typing import List, Optional

from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.location import Location


class EnergyManager:
    """Energy consumption and charging heuristics."""

    @staticmethod
    def calculate_consumption(
        distance: float,
        load: float,
        efficiency: float = 0.15,
        weather_factor: float = 1.0,
    ) -> float:
        load_factor = 1.0 + load / 10000
        return float(distance * efficiency * load_factor * weather_factor)

    @staticmethod
    def find_nearest_charging_station(
        current_pos: Location,
        stations: List[ChargingStation],
        required_distance: float,
        current_battery: float,
    ) -> Optional[ChargingStation]:
        del required_distance  # Reserved for future strategy.

        reachable = []
        for station in stations:
            dist = current_pos.distance_to(station.position)
            if dist <= current_battery:
                reachable.append(station)

        if not reachable:
            return None

        return min(
            reachable,
            key=lambda s: current_pos.distance_to(s.position) + s.get_wait_time(),
        )
