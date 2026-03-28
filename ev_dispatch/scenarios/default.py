from typing import List, Tuple

from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.vehicle import Vehicle


def build_default_scenario(
    width: float = 20,
    height: float = 20,
    num_nodes: int = 25,
    num_vehicles: int = 5,
    num_stations: int = 3,
) -> Tuple[RoadNetwork, List[Vehicle], List[ChargingStation]]:
    network = RoadNetwork(width=width, height=height, num_nodes=num_nodes)

    vehicles = [
        Vehicle(
            id=f"vehicle_{i}",
            position=Location(width / 2, height / 2, "depot"),
            battery_capacity=100,
            current_battery=100,
        )
        for i in range(num_vehicles)
    ]

    charging_stations = [
        ChargingStation(
            id=f"station_{i}",
            position=Location((i + 1) * (width / (num_stations + 1)), (i + 1) * (height / (num_stations + 1))),
        )
        for i in range(num_stations)
    ]

    return network, vehicles, charging_stations
