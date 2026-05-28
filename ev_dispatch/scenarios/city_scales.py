from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.vehicle import Vehicle
from ev_dispatch.scenarios.default import build_default_scenario

CityScaleName = Literal["small_city", "medium_city", "large_city", "mega_city"]


@dataclass(frozen=True)
class CityScaleConfig:
    """
    Scenario + simulation configuration for one city scale.

    Notes:
    - `tasks_per_step` is tuned to avoid unrealistically overloaded task streams.
    - One simulation step schedules new task arrivals; vehicle and charging updates are event-driven within each hour.
    """

    name: CityScaleName
    width: float
    height: float
    num_nodes: int
    num_vehicles: int
    num_stations: int
    vehicle_mix: Dict[str, int]
    num_steps: int
    tasks_per_step: int

    @property
    def avg_tasks_per_vehicle_per_hour(self) -> float:
        return self.tasks_per_step / max(1, self.num_vehicles)


CITY_SCALE_CONFIGS: Dict[CityScaleName, CityScaleConfig] = {
    # Small city: moderate density, lower traffic complexity.
    "small_city": CityScaleConfig(
        name="small_city",
        width=28.0,
        height=28.0,
        num_nodes=64,  # 8x8 grid
        num_vehicles=12,
        num_stations=4,
        vehicle_mix={"compact": 3, "standard": 7, "large": 2},
        num_steps=24,  # 1 day
        tasks_per_step=4,
    ),
    # Medium city: denser road interactions and more mixed demand.
    "medium_city": CityScaleConfig(
        name="medium_city",
        width=52.0,
        height=52.0,
        num_nodes=144,  # 12x12 grid
        num_vehicles=32,
        num_stations=10,
        vehicle_mix={"compact": 8, "standard": 19, "large": 5},
        num_steps=36,
        tasks_per_step=11,
    ),
    # Large city: stronger demand and congestion effects.
    "large_city": CityScaleConfig(
        name="large_city",
        width=85.0,
        height=85.0,
        num_nodes=256,  # 16x16 grid
        num_vehicles=90,
        num_stations=24,
        vehicle_mix={"compact": 20, "standard": 54, "large": 16},
        num_steps=48,
        tasks_per_step=30,
    ),
    # Mega city: very large network and heavy request arrivals.
    "mega_city": CityScaleConfig(
        name="mega_city",
        width=130.0,
        height=130.0,
        num_nodes=400,  # 20x20 grid
        num_vehicles=220,
        num_stations=60,
        vehicle_mix={"compact": 48, "standard": 132, "large": 40},
        num_steps=72,
        tasks_per_step=72,
    ),
}


def list_city_scales() -> List[CityScaleName]:
    return list(CITY_SCALE_CONFIGS.keys())


def get_city_scale_config(scale: CityScaleName) -> CityScaleConfig:
    return CITY_SCALE_CONFIGS[scale]


def build_city_scale_scenario(
    scale: CityScaleName,
    random_seed: Optional[int] = None,
) -> Tuple[RoadNetwork, List[Vehicle], List[ChargingStation], CityScaleConfig]:
    cfg = get_city_scale_config(scale)
    network, vehicles, charging_stations = build_default_scenario(
        width=cfg.width,
        height=cfg.height,
        num_nodes=cfg.num_nodes,
        num_vehicles=cfg.num_vehicles,
        num_stations=cfg.num_stations,
        vehicle_mix=cfg.vehicle_mix,
        random_seed=random_seed,
    )
    return network, vehicles, charging_stations, cfg
