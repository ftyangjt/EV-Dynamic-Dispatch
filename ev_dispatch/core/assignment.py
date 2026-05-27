from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.energy import EnergyManager
from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.task import Task
from ev_dispatch.core.vehicle import Vehicle


@dataclass(frozen=True)
class AssignmentEstimate:
    """Shared feasibility and route estimate for assigning one task to one vehicle."""

    feasible: bool
    reason: str
    cargo_type: str
    depart_time: datetime
    transport_start_position: Location
    planned_pickup_time: datetime
    planned_completion_time: datetime
    pickup_distance: float = 0.0
    delivery_distance: float = 0.0
    total_distance: float = 0.0
    pickup_hours: float = 0.0
    delivery_hours: float = 0.0
    total_hours: float = 0.0
    pickup_road_metrics: Dict[str, float] = field(default_factory=dict)
    delivery_road_metrics: Dict[str, float] = field(default_factory=dict)
    road_energy_factor: float = 1.0
    avg_route_speed: float = 0.0
    energy_used: float = 0.0
    energy_to_station: float = 0.0
    required_energy: float = 0.0
    station_distance_after_delivery: float = 0.0
    station_wait_hours: float = 0.0
    cost: float = 0.0
    execution_score: float = 0.0
    pickup_path: List[Location] = field(default_factory=list)
    delivery_path: List[Location] = field(default_factory=list)
    pickup_timed_path: List[tuple] = field(default_factory=list)
    delivery_timed_path: List[tuple] = field(default_factory=list)


def _vehicle_cost_multiplier(vehicle: Vehicle) -> float:
    vehicle_type_name = (vehicle.vehicle_type.name or "").strip().lower()
    if "compact" in vehicle_type_name:
        return 1.10
    if "large" in vehicle_type_name:
        return 1.25
    if "standard" in vehicle_type_name:
        return 1.00
    return 1.10


def _transport_cost(
    total_distance: float,
    task_weight: float,
    energy_used: float,
    pickup_road_metrics: Dict[str, float],
    delivery_road_metrics: Dict[str, float],
    vehicle: Vehicle,
) -> float:
    base_cost_per_km = 1.0
    weight_cost_per_kg_km = 0.002
    energy_cost_per_kwh = 0.8

    distance_cost = total_distance * base_cost_per_km
    weight_cost = total_distance * task_weight * weight_cost_per_kg_km
    energy_cost = energy_used * energy_cost_per_kwh
    road_cost = (
        pickup_road_metrics["toll_cost"]
        + delivery_road_metrics["toll_cost"]
        + pickup_road_metrics["risk_cost"]
        + delivery_road_metrics["risk_cost"]
    )
    restriction_penalty = (
        pickup_road_metrics["restricted_distance_km"]
        + delivery_road_metrics["restricted_distance_km"]
    ) * 2.0
    return float(
        (distance_cost + weight_cost + energy_cost + road_cost + restriction_penalty)
        * _vehicle_cost_multiplier(vehicle)
    )


def estimate_task_assignment(
    network: RoadNetwork,
    vehicle: Vehicle,
    task: Task,
    depart_time: datetime,
    planned_load: Optional[float] = None,
    planned_volume: Optional[float] = None,
    reserve_energy_kwh: float = 0.0,
    station_after_delivery: Optional[ChargingStation] = None,
    include_paths: bool = False,
    method: str = "dijkstra",
) -> AssignmentEstimate:
    """Estimate feasibility, route, energy, and cost for a vehicle-task pair."""
    cargo_type = getattr(task.cargo_type, "value", task.cargo_type)
    transport_start_position = Location(vehicle.position.x, vehicle.position.y, vehicle.position.name)

    if cargo_type not in vehicle.supported_cargo_types:
        return AssignmentEstimate(
            feasible=False,
            reason="cargo_type_mismatch",
            cargo_type=cargo_type,
            depart_time=depart_time,
            transport_start_position=transport_start_position,
            planned_pickup_time=depart_time,
            planned_completion_time=depart_time,
        )

    load_before = vehicle.current_load if planned_load is None else float(planned_load)
    volume_before = vehicle.current_volume if planned_volume is None else float(planned_volume)
    if load_before + task.weight > vehicle.load_capacity or volume_before + task.volume > vehicle.volume_capacity:
        return AssignmentEstimate(
            feasible=False,
            reason="capacity_or_energy_insufficient",
            cargo_type=cargo_type,
            depart_time=depart_time,
            transport_start_position=transport_start_position,
            planned_pickup_time=depart_time,
            planned_completion_time=depart_time,
        )

    pickup_distance = network.shortest_distance(vehicle.position, task.origin, method=method)
    pickup_road_metrics = network.path_road_metrics(
        vehicle.position,
        task.origin,
        start_time=depart_time,
        vehicle_max_speed_kmh=vehicle.max_speed_kmh,
        method=method,
    )
    pickup_hours = network.shortest_travel_time_hours(
        vehicle.position,
        task.origin,
        start_time=depart_time,
        vehicle_max_speed_kmh=vehicle.max_speed_kmh,
    )
    planned_pickup_time = depart_time + timedelta(hours=pickup_hours)

    delivery_distance = network.shortest_distance(task.origin, task.destination, method=method)
    delivery_road_metrics = network.path_road_metrics(
        task.origin,
        task.destination,
        start_time=planned_pickup_time,
        vehicle_max_speed_kmh=vehicle.max_speed_kmh,
        method=method,
    )
    delivery_hours = network.shortest_travel_time_hours(
        task.origin,
        task.destination,
        start_time=planned_pickup_time,
        vehicle_max_speed_kmh=vehicle.max_speed_kmh,
    )

    total_distance = pickup_distance + delivery_distance
    total_hours = pickup_hours + delivery_hours
    road_energy_factor = (
        pickup_road_metrics["energy_factor"] * pickup_distance
        + delivery_road_metrics["energy_factor"] * delivery_distance
    ) / max(1e-9, total_distance)
    avg_route_speed = total_distance / max(1e-9, total_hours)
    energy_used = EnergyManager.calculate_consumption(
        distance=total_distance,
        load=task.weight,
        speed_kmh=avg_route_speed,
        efficiency=vehicle.efficiency,
        weather_factor=road_energy_factor,
    )

    station_distance_after_delivery = 0.0
    station_wait_hours = 0.0
    energy_to_station = 0.0
    if station_after_delivery is not None:
        station_distance_after_delivery = network.shortest_distance(
            task.destination,
            station_after_delivery.position,
            method=method,
        )
        station_wait_hours = station_after_delivery.get_wait_time(depart_time) / 60.0
        energy_to_station = EnergyManager.calculate_consumption(
            distance=station_distance_after_delivery,
            load=0.0,
            speed_kmh=vehicle.current_speed_kmh,
            efficiency=vehicle.efficiency,
        )

    required_energy = energy_used + energy_to_station + float(reserve_energy_kwh)
    planned_completion_time = depart_time + timedelta(hours=total_hours)
    cost = _transport_cost(
        total_distance=total_distance,
        task_weight=task.weight,
        energy_used=energy_used,
        pickup_road_metrics=pickup_road_metrics,
        delivery_road_metrics=delivery_road_metrics,
        vehicle=vehicle,
    )
    execution_score = 100 - (total_distance * 0.1) - (total_hours * 2.0) - cost

    pickup_path: List[Location] = []
    delivery_path: List[Location] = []
    pickup_timed_path: List[tuple] = []
    delivery_timed_path: List[tuple] = []
    if include_paths:
        pickup_path = network.shortest_path_locations(
            transport_start_position,
            task.origin,
            method=method,
        )
        pickup_timed_path = network.timed_path_locations(
            transport_start_position,
            task.origin,
            start_time=depart_time,
            vehicle_max_speed_kmh=vehicle.max_speed_kmh,
            method=method,
        )
        delivery_path = network.shortest_path_locations(
            task.origin,
            task.destination,
            method=method,
        )
        delivery_timed_path = network.timed_path_locations(
            task.origin,
            task.destination,
            start_time=planned_pickup_time,
            vehicle_max_speed_kmh=vehicle.max_speed_kmh,
            method=method,
        )

    feasible = required_energy <= vehicle.current_battery
    return AssignmentEstimate(
        feasible=feasible,
        reason="" if feasible else "capacity_or_energy_insufficient",
        cargo_type=cargo_type,
        depart_time=depart_time,
        transport_start_position=transport_start_position,
        planned_pickup_time=planned_pickup_time,
        planned_completion_time=planned_completion_time,
        pickup_distance=pickup_distance,
        delivery_distance=delivery_distance,
        total_distance=total_distance,
        pickup_hours=pickup_hours,
        delivery_hours=delivery_hours,
        total_hours=total_hours,
        pickup_road_metrics=pickup_road_metrics,
        delivery_road_metrics=delivery_road_metrics,
        road_energy_factor=road_energy_factor,
        avg_route_speed=avg_route_speed,
        energy_used=energy_used,
        energy_to_station=energy_to_station,
        required_energy=required_energy,
        station_distance_after_delivery=station_distance_after_delivery,
        station_wait_hours=station_wait_hours,
        cost=cost,
        execution_score=execution_score,
        pickup_path=pickup_path,
        delivery_path=delivery_path,
        pickup_timed_path=pickup_timed_path,
        delivery_timed_path=delivery_timed_path,
    )
