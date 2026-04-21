from typing import List

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.interfaces import Action, SimulationState


class DispatcherNearestFirst(Dispatcher):
    """Greedy strategy: assign nearest feasible task first."""

    def generate_actions(self, state: SimulationState) -> List[Action]:
        actions: List[Action] = []
        unassigned_tasks = list(state.pending_tasks)
        planned_loads = {v.id: v.current_load for v in state.vehicles}
        available_vehicles = [
            v
            for v in state.vehicles
            if v.current_battery > 10 and v.get_available_capacity() > 0
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
        planned_loads = {v.id: v.current_load for v in state.vehicles}
        sorted_tasks = sorted(state.pending_tasks, key=lambda t: t.weight, reverse=True)

        for task in sorted_tasks:
            best_vehicle = None
            best_distance = float("inf")

            for vehicle in state.vehicles:
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
