from dataclasses import dataclass
from typing import List, Optional, Tuple

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.assignment import AssignmentEstimate, estimate_task_assignment
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
    "tuned": CompositeScoreConfig(
        name="tuned",
        base_reward=120.0,
        priority_weight=15.0,
        urgency_weight=24.8888,
        urgency_floor_hours=1.51409,
        load_fit_weight=9.10928,
        pickup_distance_weight=4.0,
        delivery_distance_weight=0.878372,
        travel_time_weight=12.5993,
        energy_weight=1.53703,
        lateness_weight=30.8163,
        station_wait_weight=5.95514,
        station_distance_weight=0.484857,
        low_battery_ratio_threshold=0.287597,
        low_battery_penalty_weight=103.097,
        reserve_energy_kwh=15.1639,
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
                metric = d + (s.get_wait_time(state.current_time) / 60.0)
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
            for t in state.task_pool("deadline"):
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

        unassigned_tasks = state.task_pool("deadline")
        planned_loads = {v.id: v.current_load for v in state.vehicles}
        planned_volumes = {v.id: v.current_volume for v in state.vehicles}
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
                for task in unassigned_tasks.ordered_tasks():
                    estimate = estimate_task_assignment(
                        network=state.network,
                        vehicle=vehicle,
                        task=task,
                        depart_time=state.current_time,
                        planned_load=planned_loads[vehicle.id],
                        planned_volume=planned_volumes[vehicle.id],
                    )
                    if estimate.feasible and estimate.pickup_distance < best_distance:
                        best_distance = estimate.pickup_distance
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
            unassigned_tasks.remove(task.id)
            planned_loads[vehicle.id] += task.weight
            planned_volumes[vehicle.id] += task.volume
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
                metric = d + (s.get_wait_time(state.current_time) / 60.0)
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
            for t in state.task_pool("deadline"):
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
        planned_volumes = {v.id: v.current_volume for v in state.vehicles}
        task_pool = state.task_pool("weight")

        while task_pool:
            task = task_pool.pop()
            if task is None:
                break
            best_vehicle = None
            best_distance = float("inf")

            for vehicle in state.vehicles:
                if vehicle.id in vehicles_to_charge or vehicle.status != VehicleStatus.IDLE:
                    continue
                estimate = estimate_task_assignment(
                    network=state.network,
                    vehicle=vehicle,
                    task=task,
                    depart_time=state.current_time,
                    planned_load=planned_loads[vehicle.id],
                    planned_volume=planned_volumes[vehicle.id],
                )
                if estimate.feasible and estimate.pickup_distance < best_distance:
                    best_distance = estimate.pickup_distance
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
                planned_volumes[best_vehicle.id] += task.volume
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
            metric = distance + station.get_wait_time(state.current_time) / 60.0
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
    ) -> Optional[Tuple[float, AssignmentEstimate]]:
        nearest_station = self._nearest_station_for(state, vehicle)
        cfg = self.config
        estimate = estimate_task_assignment(
            network=state.network,
            vehicle=vehicle,
            task=task,
            depart_time=state.current_time,
            planned_load=planned_load,
            planned_volume=planned_volume,
            reserve_energy_kwh=cfg.reserve_energy_kwh,
            station_after_delivery=nearest_station,
        )
        if not estimate.feasible:
            return None

        slack_hours = (task.deadline - estimate.planned_completion_time).total_seconds() / 3600.0
        lateness_hours = max(0.0, -slack_hours)
        urgency_bonus = 1.0 / max(
            cfg.urgency_floor_hours,
            max(0.0, slack_hours) + cfg.urgency_floor_hours,
        )
        battery_after_ratio = (vehicle.current_battery - estimate.energy_used) / max(1e-9, vehicle.battery_capacity)
        load_fit = task.weight / max(1e-9, vehicle.load_capacity)
        volume_fit = task.volume / max(1e-9, vehicle.volume_capacity)

        score = (
            cfg.base_reward
            + task.priority * cfg.priority_weight
            + urgency_bonus * cfg.urgency_weight
            + max(load_fit, volume_fit) * cfg.load_fit_weight
            - estimate.pickup_distance * cfg.pickup_distance_weight
            - estimate.delivery_distance * cfg.delivery_distance_weight
            - estimate.total_hours * cfg.travel_time_weight
            - estimate.energy_used * cfg.energy_weight
            - lateness_hours * cfg.lateness_weight
            - estimate.station_wait_hours * cfg.station_wait_weight
            - estimate.station_distance_after_delivery * cfg.station_distance_weight
            - max(0.0, cfg.low_battery_ratio_threshold - battery_after_ratio)
            * cfg.low_battery_penalty_weight
        )

        return score, estimate

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

        unassigned_tasks = state.task_pool("priority_deadline")
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
            candidate_tasks = unassigned_tasks.ordered_tasks()

            for vehicle in available_vehicles:
                for task in candidate_tasks:
                    estimate = self._estimate_assignment(
                        state=state,
                        vehicle=vehicle,
                        task=task,
                        planned_load=planned_loads[vehicle.id],
                        planned_volume=planned_volumes[vehicle.id],
                    )
                    if estimate is None:
                        continue
                    score, assignment_estimate = estimate
                    if score > best_score:
                        best_score = score
                        best_assignment = (vehicle, task, assignment_estimate)

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
            unassigned_tasks.remove(task.id)
            planned_loads[vehicle.id] += task.weight
            planned_volumes[vehicle.id] += task.volume
            available_vehicles.remove(vehicle)

        return actions
