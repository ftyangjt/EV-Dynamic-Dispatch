from typing import List, Optional

from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.location import Location


class EnergyManager:
    """Energy consumption and charging heuristics."""

    @staticmethod
    def calculate_consumption(
        distance: float,
        load: float,
        speed_kmh: float = 40.0,
        efficiency: float = 0.15,
        weather_factor: float = 1.0,
    ) -> float:
        """
        Calculate energy consumption with speed factor.
        
        Args:
            distance: Travel distance (km)
            load: Cargo weight (kg)
            speed_kmh: Travel speed (km/h), optimal at 40 km/h
            efficiency: Base energy consumption (kWh/km)
            weather_factor: Weather multiplier (1.0 = normal)
        
        Returns:
            Energy needed (kWh)
        """
        # Base consumption: distance * efficiency * load_factor * weather
        load_factor = 1.0 + load / 10000  # +0.01% per kg
        base = distance * efficiency * load_factor * weather_factor
        
        # Speed factor: optimal at 40 km/h
        # At 40 km/h: factor = 1.0 (100% efficiency)
        # At 60 km/h: factor ≈ 1.1 (10% increase)
        # At 20 km/h: factor ≈ 1.1 (10% increase)
        speed_factor = 1.0 + ((speed_kmh - 40.0) ** 2) / 3200.0
        
        return float(base * speed_factor)

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
