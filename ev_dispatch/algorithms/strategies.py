from typing import List

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.interfaces import Action, SimulationState
from ev_dispatch.core.energy import EnergyManager
from ev_dispatch.core.vehicle import VehicleStatus


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
            if v.id not in vehicles_to_charge and v.status == VehicleStatus.IDLE and v.current_battery > 10 and v.get_available_load_capacity() > 0
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
            if (vehicle.load_capacity - planned_loads[vehicle.id]) <= 0:
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

        return actions
