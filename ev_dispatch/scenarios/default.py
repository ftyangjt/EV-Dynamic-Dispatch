from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.vehicle import Vehicle, VehicleType


@dataclass
class CargoConfig:
    """Cargo type distribution configuration."""

    num_types: int = 4
    type_1_ratio: float = 0.7

    def get_cargo_type_distribution(self) -> Dict[str, float]:
        k = int(max(1, self.num_types))
        t = float(np.clip(self.type_1_ratio, 0.0, 1.0))
        if k == 1:
            return {"type_1": 1.0}

        other = (1.0 - t) / (k - 1)
        dist = {"type_1": t}
        for m in range(2, k + 1):
            dist[f"type_{m}"] = other
        return dist


VEHICLE_TYPES = {
    "compact": VehicleType(
        name="Compact",
        battery_capacity=60.0,
        load_capacity=500.0,
        volume_capacity=2.5,
        max_speed_kmh=80.0,
        charging_speed_kwh_per_hour=30.0,
        efficiency=0.12,
        min_battery_reserve_ratio=0.22,
        min_battery_reserve_kwh=12.0,
        supported_cargo_types=["type_1", "type_2"],
    ),
    "standard": VehicleType(
        name="Standard",
        battery_capacity=100.0,
        load_capacity=1000.0,
        volume_capacity=5.0,
        max_speed_kmh=100.0,
        charging_speed_kwh_per_hour=50.0,
        efficiency=0.15,
        min_battery_reserve_ratio=0.20,
        min_battery_reserve_kwh=15.0,
        supported_cargo_types=["type_1", "type_3"],
    ),
    "large": VehicleType(
        name="Large",
        battery_capacity=150.0,
        load_capacity=2000.0,
        volume_capacity=10.0,
        max_speed_kmh=90.0,
        charging_speed_kwh_per_hour=75.0,
        efficiency=0.20,
        min_battery_reserve_ratio=0.18,
        min_battery_reserve_kwh=20.0,
        supported_cargo_types=["type_1", "type_4"],
    ),
}


def build_default_scenario(
    width: float = 20,
    height: float = 20,
    num_nodes: int = 25,
    num_vehicles: int = 5,
    num_stations: int = 3,
    vehicle_mix: Dict[str, int] = None,
) -> Tuple[RoadNetwork, List[Vehicle], List[ChargingStation]]:
    """Build a default city dispatch scenario."""
    network = RoadNetwork(width=width, height=height, num_nodes=num_nodes)

    if vehicle_mix is None:
        vehicle_mix = {"compact": 1, "standard": 3, "large": 1}

    vehicles: List[Vehicle] = []
    vehicle_index = 0
    for vehicle_type_name, count in vehicle_mix.items():
        if vehicle_type_name not in VEHICLE_TYPES:
            continue
        vehicle_type = VEHICLE_TYPES[vehicle_type_name]
        for _ in range(count):
            vehicles.append(
                Vehicle(
                    id=f"vehicle_{vehicle_index}",
                    position=Location(width / 2, height / 2, "depot"),
                    vehicle_type=vehicle_type,
                )
            )
            vehicle_index += 1

    while len(vehicles) < num_vehicles:
        vehicles.append(
            Vehicle(
                id=f"vehicle_{len(vehicles)}",
                position=Location(width / 2, height / 2, "depot"),
                vehicle_type=VEHICLE_TYPES["standard"],
            )
        )

    station_profiles = [
        {"num_chargers": 4, "charging_power": 120.0},
        {"num_chargers": 3, "charging_power": 90.0},
        {"num_chargers": 2, "charging_power": 60.0},
    ]
    charging_stations: List[ChargingStation] = []
    for i in range(num_stations):
        profile = station_profiles[i % len(station_profiles)]
        charging_stations.append(
            ChargingStation(
                id=f"station_{i}",
                position=Location(
                    (i + 1) * (width / (num_stations + 1)),
                    (i + 1) * (height / (num_stations + 1)),
                ),
                num_chargers=int(profile["num_chargers"]),
                charging_power=float(profile["charging_power"]),
            )
        )

    return network, vehicles, charging_stations
