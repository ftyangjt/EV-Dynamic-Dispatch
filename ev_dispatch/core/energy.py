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
        City EV cargo energy model (coarse but physically plausible).

        Args:
            distance: Travel distance (km)
            load: Cargo weight (kg)
            speed_kmh: Travel speed (km/h)
            efficiency: Base no-load consumption (kWh/km)
            weather_factor: Weather multiplier (1.0 = normal)

        Returns:
            Energy needed (kWh)
        """
        distance = max(0.0, float(distance))
        load = max(0.0, float(load))
        speed_kmh = float(max(5.0, min(90.0, speed_kmh)))
        weather_factor = max(0.8, min(1.4, float(weather_factor)))

        # Payload effect: saturating growth, stronger than the toy linear model.
        # 500kg -> +11%, 1000kg -> +14.7%, 2000kg -> +17.6% (with this formulation)
        payload_factor = 1.0 + 0.22 * (load / (load + 500.0))

        # Best urban efficiency around 35 km/h.
        speed_offset = speed_kmh - 35.0
        speed_factor = 1.0 + (speed_offset * speed_offset) / 2800.0

        return float(distance * efficiency * payload_factor * speed_factor * weather_factor)

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
