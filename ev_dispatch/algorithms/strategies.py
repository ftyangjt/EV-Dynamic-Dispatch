from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional, Tuple

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.interfaces import Action, SimulationState
from ev_dispatch.core.energy import EnergyManager
from ev_dispatch.core.vehicle import VehicleStatus


@dataclass(frozen=True)
class CompositeScoreConfig:
    """Tunable weights for DispatcherCompositeScore."""

    name: str = "balanced"
    base_reward: float = 120.0
    priority_weight: float = 15.0
    urgency_weight: float = 8.0
    urgency_floor_hours: float = 0.25
    load_fit_weight: float = 10.0
    pickup_distance_weight: float = 1.2
    delivery_distance_weight: float = 0.25
    travel_time_weight: float = 5.0
    energy_weight: float = 1.8
    lateness_weight: float = 60.0
    station_wait_weight: float = 4.0
    station_distance_weight: float = 0.15
    low_battery_ratio_threshold: float = 0.25
    low_battery_penalty_weight: float = 80.0
    reserve_energy_kwh: float = 5.0


COMPOSITE_CONFIG_PRESETS = {
    "balanced": CompositeScoreConfig(name="balanced"),
    "deadline": CompositeScoreConfig(
        name="deadline",
        urgency_weight=14.0,
        lateness_weight=100.0,
        travel_time_weight=6.5,
        pickup_distance_weight=1.0,
    ),
    "cost": CompositeScoreConfig(
        name="cost",
        pickup_distance_weight=1.7,
        delivery_distance_weight=0.45,
        energy_weight=2.4,
        station_distance_weight=0.25,
    ),
    "energy": CompositeScoreConfig(
        name="energy",
        energy_weight=3.0,
        reserve_energy_kwh=8.0,
        low_battery_ratio_threshold=0.35,
        low_battery_penalty_weight=130.0,
    ),
}


class DispatcherNearestFirst(Dispatcher):
    """Greedy strategy: assign nearest feasible task first."""

    def generate_actions(self, state: SimulationState) -> List[Action]:
        actions: List[Action] = []

        def nearest_station_for(vehicle):
            if not state.charging_stations:
                return None
            best = None
            best_metric = float("inf")
            for s in state.charging_stations:
                d = state.network.shortest_distance(vehicle.position, s.position)
                metric = d + (s.get_wait_time() / 60.0)
                if metric < best_metric:
                    best_metric = metric
                    best = s
            return best

        vehicles_to_charge = set()
        for v in state.vehicles:
            if v.status != VehicleStatus.IDLE:
                continue
            st = nearest_station_for(v)
            if st is None:
                continue

            if v.current_battery <= v.min_battery_threshold:
                actions.append(Action(type="go_charge", vehicle_id=v.id, station_id=st.id, note="low_battery"))
                vehicles_to_charge.add(v.id)
                continue

            can_finish_one_and_reach_station = False
            for t in state.pending_tasks:
                dist_to_pick = state.network.shortest_distance(v.position, t.origin)
                dist_delivery = state.network.shortest_distance(t.origin, t.destination)
                dist_to_station = state.network.shortest_distance(t.destination, st.position)
                energy_task = EnergyManager.calculate_consumption(
                    distance=dist_to_pick + dist_delivery,
                    load=t.weight,
                    speed_kmh=v.current_speed_kmh,
                    efficiency=v.efficiency,
                )
                energy_to_station = EnergyManager.calculate_consumption(
                    distance=dist_to_station,
                    load=0.0,
                    speed_kmh=v.current_speed_kmh,
                    efficiency=v.efficiency,
                )
                reserve = 5.0
                if v.current_battery >= (energy_task + energy_to_station + reserve):
                    can_finish_one_and_reach_station = True
                    break

            if not can_finish_one_and_reach_station and state.pending_tasks:
                actions.append(Action(type="go_charge", vehicle_id=v.id, station_id=st.id, note="cannot_reach_station_after_task"))
                vehicles_to_charge.add(v.id)

        unassigned_tasks = list(state.pending_tasks)
        planned_loads = {v.id: v.current_load for v in state.vehicles}
        available_vehicles = [
            v
            for v in state.vehicles
            if (
                v.id not in vehicles_to_charge
                and v.status == VehicleStatus.IDLE
                and v.current_battery > v.min_battery_threshold
                and v.get_available_load_capacity() > 0
            )
        ]

        while unassigned_tasks and available_vehicles:
            best_assignment = None
            best_distance = float("inf")

            for vehicle in available_vehicles:
                for task in unassigned_tasks:
                    available_capacity = vehicle.load_capacity - planned_loads[vehicle.id]
                    if available_capacity >= task.weight:
                        dist = state.network.shortest_distance(vehicle.position, task.origin)
                        if dist < best_distance:
                            best_distance = dist
                            best_assignment = (vehicle, task)

            if best_assignment is None:
                break

            vehicle, task = best_assignment
            actions.append(
                Action(
                    type="assign_task",
                    vehicle_id=vehicle.id,
                    task_id=task.id,
                    note="nearest_first",
                )
            )
            unassigned_tasks.remove(task)
            planned_loads[vehicle.id] += task.weight
            available_vehicles.remove(vehicle)

        return actions


class DispatcherLargestFirst(Dispatcher):
    """Greedy strategy: assign heavier tasks first."""

    def generate_actions(self, state: SimulationState) -> List[Action]:
        actions: List[Action] = []

        def nearest_station_for(vehicle):
            if not state.charging_stations:
                return None
            best = None
            best_metric = float("inf")
            for s in state.charging_stations:
                d = state.network.shortest_distance(vehicle.position, s.position)
                metric = d + (s.get_wait_time() / 60.0)
                if metric < best_metric:
                    best_metric = metric
                    best = s
            return best

        vehicles_to_charge = set()
        for v in state.vehicles:
            if v.status != VehicleStatus.IDLE:
                continue
            st = nearest_station_for(v)
            if st is None:
                continue
            if v.current_battery <= v.min_battery_threshold:
                actions.append(Action(type="go_charge", vehicle_id=v.id, station_id=st.id, note="low_battery"))
                vehicles_to_charge.add(v.id)
                continue

            can_finish_one_and_reach_station = False
            for t in state.pending_tasks:
                dist_to_pick = state.network.shortest_distance(v.position, t.origin)
                dist_delivery = state.network.shortest_distance(t.origin, t.destination)
                dist_to_station = state.network.shortest_distance(t.destination, st.position)
                energy_task = EnergyManager.calculate_consumption(
                    distance=dist_to_pick + dist_delivery,
                    load=t.weight,
                    speed_kmh=v.current_speed_kmh,
                    efficiency=v.efficiency,
                )
                energy_to_station = EnergyManager.calculate_consumption(
                    distance=dist_to_station,
                    load=0.0,
                    speed_kmh=v.current_speed_kmh,
                    efficiency=v.efficiency,
                )
                reserve = 5.0
                if v.current_battery >= (energy_task + energy_to_station + reserve):
                    can_finish_one_and_reach_station = True
                    break

            if not can_finish_one_and_reach_station and state.pending_tasks:
                actions.append(Action(type="go_charge", vehicle_id=v.id, station_id=st.id, note="cannot_reach_station_after_task"))
                vehicles_to_charge.add(v.id)

        planned_loads = {v.id: v.current_load for v in state.vehicles}
        sorted_tasks = sorted(state.pending_tasks, key=lambda t: t.weight, reverse=True)

        for task in sorted_tasks:
            best_vehicle = None
            best_distance = float("inf")

            for vehicle in state.vehicles:
                if vehicle.id in vehicles_to_charge or vehicle.status != VehicleStatus.IDLE:
                    continue
                available_capacity = vehicle.load_capacity - planned_loads[vehicle.id]
                if (
                    available_capacity >= task.weight
                    and vehicle.can_reach(task.origin, network=state.network)
                ):
                    dist = state.network.shortest_distance(vehicle.position, task.origin)
                    if dist < best_distance:
                        best_distance = dist
                        best_vehicle = vehicle

            if best_vehicle is not None:
                actions.append(
                    Action(
                        type="assign_task",
                        vehicle_id=best_vehicle.id,
                        task_id=task.id,
                        note="largest_first",
                    )
                )
                planned_loads[best_vehicle.id] += task.weight
                sorted_tasks = [t for t in sorted_tasks if t.id != task.id]
                vehicles_to_charge.add(best_vehicle.id)

        return actions


class DispatcherCompositeScore(Dispatcher):
    """
    Greedy strategy with a richer assignment score.

    Compared with the toy baselines, this strategy filters infeasible pairs first
    and then balances pickup distance, delivery time, deadline risk, energy risk,
    charging access, and vehicle/task fit.
    """

    def __init__(
        self,
        network,
        config: Optional[CompositeScoreConfig] = None,
    ):
        super().__init__(network)
        self.config = config or COMPOSITE_CONFIG_PRESETS["balanced"]

    def _nearest_station_for(self, state: SimulationState, vehicle) -> Optional[object]:
        if not state.charging_stations:
            return None
        best_station = None
        best_metric = float("inf")
        for station in state.charging_stations:
            distance = state.network.shortest_distance(vehicle.position, station.position)
            metric = distance + station.get_wait_time() / 60.0
            if metric < best_metric:
                best_metric = metric
                best_station = station
        return best_station

    def _estimate_assignment(
        self,
        state: SimulationState,
        vehicle,
        task,
        planned_load: float,
        planned_volume: float,
    ) -> Optional[Tuple[float, dict]]:
        cargo_type = getattr(task.cargo_type, "value", task.cargo_type)
        if cargo_type not in vehicle.supported_cargo_types:
            return None
        if planned_load + task.weight > vehicle.load_capacity:
            return None
        if planned_volume + task.volume > vehicle.volume_capacity:
            return None

        pickup_distance = state.network.shortest_distance(vehicle.position, task.origin)
        delivery_distance = state.network.shortest_distance(task.origin, task.destination)
        depart_time = state.current_time
        pickup_hours = state.network.shortest_travel_time_hours(
            vehicle.position,
            task.origin,
            start_time=depart_time,
            vehicle_max_speed_kmh=vehicle.max_speed_kmh,
        )
        delivery_hours = state.network.shortest_travel_time_hours(
            task.origin,
            task.destination,
            start_time=depart_time + timedelta(hours=pickup_hours),
            vehicle_max_speed_kmh=vehicle.max_speed_kmh,
        )
        total_distance = pickup_distance + delivery_distance
        total_hours = pickup_hours + delivery_hours
        energy = EnergyManager.calculate_consumption(
            distance=total_distance,
            load=task.weight,
            speed_kmh=vehicle.current_speed_kmh,
            efficiency=vehicle.efficiency,
        )

        nearest_station = self._nearest_station_for(state, vehicle)
        station_distance_after_delivery = 0.0
        station_wait_hours = 0.0
        energy_to_station = 0.0
        if nearest_station is not None:
            station_distance_after_delivery = state.network.shortest_distance(
                task.destination,
                nearest_station.position,
            )
            station_wait_hours = nearest_station.get_wait_time() / 60.0
            energy_to_station = EnergyManager.calculate_consumption(
                distance=station_distance_after_delivery,
                load=0.0,
                speed_kmh=vehicle.current_speed_kmh,
                efficiency=vehicle.efficiency,
            )

        cfg = self.config
        required_energy = energy + energy_to_station + cfg.reserve_energy_kwh
        if required_energy > vehicle.current_battery:
            return None

        completion_time = depart_time + timedelta(hours=total_hours)
        slack_hours = (task.deadline - completion_time).total_seconds() / 3600.0
        lateness_hours = max(0.0, -slack_hours)
        urgency_bonus = 1.0 / max(
            cfg.urgency_floor_hours,
            max(0.0, slack_hours) + cfg.urgency_floor_hours,
        )
        battery_after_ratio = (vehicle.current_battery - energy) / max(1e-9, vehicle.battery_capacity)
        load_fit = task.weight / max(1e-9, vehicle.load_capacity)
        volume_fit = task.volume / max(1e-9, vehicle.volume_capacity)

        score = (
            cfg.base_reward
            + task.priority * cfg.priority_weight
            + urgency_bonus * cfg.urgency_weight
            + max(load_fit, volume_fit) * cfg.load_fit_weight
            - pickup_distance * cfg.pickup_distance_weight
            - delivery_distance * cfg.delivery_distance_weight
            - total_hours * cfg.travel_time_weight
            - energy * cfg.energy_weight
            - lateness_hours * cfg.lateness_weight
            - station_wait_hours * cfg.station_wait_weight
            - station_distance_after_delivery * cfg.station_distance_weight
            - max(0.0, cfg.low_battery_ratio_threshold - battery_after_ratio)
            * cfg.low_battery_penalty_weight
        )

        return score, {
            "energy": energy,
            "pickup_distance": pickup_distance,
            "delivery_distance": delivery_distance,
            "total_hours": total_hours,
            "slack_hours": slack_hours,
        }

    def generate_actions(self, state: SimulationState) -> List[Action]:
        actions: List[Action] = []
        vehicles_to_charge = set()

        for vehicle in state.vehicles:
            if vehicle.status != VehicleStatus.IDLE:
                continue
            station = self._nearest_station_for(state, vehicle)
            if station is not None and vehicle.current_battery <= vehicle.min_battery_threshold:
                actions.append(
                    Action(
                        type="go_charge",
                        vehicle_id=vehicle.id,
                        station_id=station.id,
                        note=f"composite_{self.config.name}_low_battery",
                    )
                )
                vehicles_to_charge.add(vehicle.id)

        unassigned_tasks = list(state.pending_tasks)
        planned_loads = {v.id: v.current_load for v in state.vehicles}
        planned_volumes = {v.id: v.current_volume for v in state.vehicles}
        available_vehicles = [
            v
            for v in state.vehicles
            if v.status == VehicleStatus.IDLE and v.id not in vehicles_to_charge
        ]

        while unassigned_tasks and available_vehicles:
            best_assignment = None
            best_score = float("-inf")

            for vehicle in available_vehicles:
                for task in unassigned_tasks:
                    estimate = self._estimate_assignment(
                        state=state,
                        vehicle=vehicle,
                        task=task,
                        planned_load=planned_loads[vehicle.id],
                        planned_volume=planned_volumes[vehicle.id],
                    )
                    if estimate is None:
                        continue
                    score, details = estimate
                    if score > best_score:
                        best_score = score
                        best_assignment = (vehicle, task, details)

            if best_assignment is None:
                break

            vehicle, task, _details = best_assignment
            actions.append(
                Action(
                    type="assign_task",
                    vehicle_id=vehicle.id,
                    task_id=task.id,
                    note=f"composite_{self.config.name}",
                )
            )
            unassigned_tasks.remove(task)
            planned_loads[vehicle.id] += task.weight
            planned_volumes[vehicle.id] += task.volume
            available_vehicles.remove(vehicle)

        return actions
