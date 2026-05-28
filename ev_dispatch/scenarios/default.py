from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    random_seed: Optional[int] = None,
    diagonal_connection_probability: float = 0.3,
) -> Tuple[RoadNetwork, List[Vehicle], List[ChargingStation]]:
    """Build a default city dispatch scenario.
    
    所有车辆和充电站必须放在网络节点上！
    """
    network = RoadNetwork(
        width=width,
        height=height,
        num_nodes=num_nodes,
        random_seed=random_seed,
        diagonal_connection_probability=diagonal_connection_probability,
    )

    if vehicle_mix is None:
        vehicle_mix = {"compact": 1, "standard": 3, "large": 1}

    # 所有车辆从第一个网络节点出发（depot）
    depot_node_id, depot_location = network.nodes[0]
    
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
                    position=depot_location,  # 使用网络节点的位置
                    vehicle_type=vehicle_type,
                )
            )
            vehicle_index += 1

    while len(vehicles) < num_vehicles:
        vehicles.append(
            Vehicle(
                id=f"vehicle_{len(vehicles)}",
                position=depot_location,  # 使用网络节点的位置
                vehicle_type=VEHICLE_TYPES["standard"],
            )
        )

    # 充电站分散放在网络的不同节点上
    # 在网格上均匀分布，避免对角线或聚集问题
    grid_size = int(np.sqrt(len(network.nodes)))
    station_profiles = [
        {"num_chargers": 4, "charging_power": 120.0},
        {"num_chargers": 3, "charging_power": 90.0},
        {"num_chargers": 2, "charging_power": 60.0},
    ]
    charging_stations: List[ChargingStation] = []
    
    # 在网格坐标上选择充电站位置（基于网格坐标的均匀分布）
    rng_local = np.random.default_rng(random_seed)
    selected_grid_coords = []
    
    # 策略：根据网格大小均匀分割，选择代表点
    if grid_size >= 1:
        # 将网格分成 num_stations 个区域，从每个区域选择一个点
        cells_per_station = grid_size / np.sqrt(min(num_stations, grid_size * grid_size))
        idx = 0
        for si in range(int(np.ceil(np.sqrt(min(num_stations, grid_size * grid_size))))):
            for sj in range(int(np.ceil(np.sqrt(min(num_stations, grid_size * grid_size))))):
                if idx >= num_stations:
                    break
                # 从这个区域内随机选择一个网格点
                i_min = int(si * cells_per_station)
                i_max = int((si + 1) * cells_per_station)
                j_min = int(sj * cells_per_station)
                j_max = int((sj + 1) * cells_per_station)
                
                i_min = min(max(0, i_min), grid_size - 1)
                i_max = min(max(1, i_max), grid_size)
                j_min = min(max(0, j_min), grid_size - 1)
                j_max = min(max(1, j_max), grid_size)
                
                if i_min < i_max and j_min < j_max:
                    selected_i = int(rng_local.integers(i_min, i_max))
                    selected_j = int(rng_local.integers(j_min, j_max))
                    grid_idx = selected_i * grid_size + selected_j
                    if grid_idx < len(network.nodes) and (selected_i, selected_j) not in selected_grid_coords:
                        selected_grid_coords.append((selected_i, selected_j))
                        idx += 1
            if idx >= num_stations:
                break
    
    for i, (grid_i, grid_j) in enumerate(selected_grid_coords):
        profile = station_profiles[i % len(station_profiles)]
        node_idx = grid_i * grid_size + grid_j
        if node_idx < len(network.nodes):
            station_node_id, station_location = network.nodes[node_idx]
            
            charging_stations.append(
                ChargingStation(
                    id=f"station_{i}",
                    position=station_location,  # 使用网络节点的位置
                    num_chargers=int(profile["num_chargers"]),
                    charging_power=float(profile["charging_power"]),
                )
            )

    return network, vehicles, charging_stations
