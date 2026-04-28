from typing import List, Tuple, Dict
import numpy as np
from dataclasses import dataclass

from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.vehicle import Vehicle, VehicleType
from ev_dispatch.core.task import CargoType


@dataclass
class CargoConfig:
    """货物分布配置

    - k 类货物：type_1...type_k
    - type_1 为通用货物，占比 t（默认 0.7）
    - 其余 type_2..type_k 平分剩余占比
    """

    num_types: int = 4
    type_1_ratio: float = 0.7  # t：通用货物占比，默认70%

    def get_cargo_type_distribution(self) -> Dict[str, float]:
        """获得各货物类型的分布比例（返回 cargo_type 字符串 -> 概率）"""
        k = int(max(1, self.num_types))
        t = float(np.clip(self.type_1_ratio, 0.0, 1.0))
        if k == 1:
            return {"type_1": 1.0}

        other = (1.0 - t) / (k - 1)
        dist = {"type_1": t}
        for m in range(2, k + 1):
            dist[f"type_{m}"] = other
        return dist


# 车型模板库
VEHICLE_TYPES = {
    "compact": VehicleType(
        name="Compact",
        battery_capacity=60.0,
        load_capacity=500.0,
        volume_capacity=2.5,
        max_speed_kmh=80.0,
        charging_speed_kwh_per_hour=30.0,
        efficiency=0.12,
        supported_cargo_types=["type_1", "type_2"],  # 通用 + 专用
    ),
    "standard": VehicleType(
        name="Standard",
        battery_capacity=100.0,
        load_capacity=1000.0,
        volume_capacity=5.0,
        max_speed_kmh=100.0,
        charging_speed_kwh_per_hour=50.0,
        efficiency=0.15,
        supported_cargo_types=["type_1", "type_3"],  # 通用 + 专用
    ),
    "large": VehicleType(
        name="Large",
        battery_capacity=150.0,
        load_capacity=2000.0,
        volume_capacity=10.0,
        max_speed_kmh=90.0,
        charging_speed_kwh_per_hour=75.0,
        efficiency=0.20,
        supported_cargo_types=["type_1", "type_4"],  # 通用 + 专用
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
    """
    构建下预设场景
    
    Args:
        width: 网络宽度
        height: 网络高度
        num_nodes: 路网节点数
        num_vehicles: 车辆总数
        num_stations: 充电站数
        vehicle_mix: 车奋配置 {"compact": 1, "standard": 3, "large": 1}
    """
    network = RoadNetwork(width=width, height=height, num_nodes=num_nodes)
    
    # 设置默认车队配置
    if vehicle_mix is None:
        vehicle_mix = {"compact": 1, "standard": 3, "large": 1}
    
    # 根据车队配置创建车辆
    vehicles = []
    vehicle_index = 0
    
    for vehicle_type_name, count in vehicle_mix.items():
        if vehicle_type_name not in VEHICLE_TYPES:
            continue
        
        vehicle_type = VEHICLE_TYPES[vehicle_type_name]
        for i in range(count):
            vehicle = Vehicle(
                id=f"vehicle_{vehicle_index}",
                position=Location(width / 2, height / 2, "depot"),
                vehicle_type=vehicle_type,
            )
            vehicles.append(vehicle)
            vehicle_index += 1
    
    # 如果车辆数量不符，补充负载
    while len(vehicles) < num_vehicles:
        vehicle_type = VEHICLE_TYPES["standard"]
        vehicle = Vehicle(
            id=f"vehicle_{len(vehicles)}",
            position=Location(width / 2, height / 2, "depot"),
            vehicle_type=vehicle_type,
        )
        vehicles.append(vehicle)

    charging_stations = [
        ChargingStation(
            id=f"station_{i}",
            position=Location(
                (i + 1) * (width / (num_stations + 1)),
                (i + 1) * (height / (num_stations + 1))
            ),
        )
        for i in range(num_stations)
    ]

    return network, vehicles, charging_stations
