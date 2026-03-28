from collections import defaultdict
from typing import Dict, List

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.task import Task
from ev_dispatch.core.vehicle import Vehicle


class DispatcherNearestFirst(Dispatcher):
    """Greedy strategy: assign nearest feasible task first."""

    def assign_tasks(self, tasks: List[Task], vehicles: List[Vehicle]) -> Dict[str, List[Task]]:
        assignments = defaultdict(list)
        unassigned_tasks = list(tasks)
        available_vehicles = [
            v
            for v in vehicles
            if v.current_battery > 10 and v.get_available_capacity() > 0
        ]

        while unassigned_tasks and available_vehicles:
            best_assignment = None
            best_distance = float("inf")

            for vehicle in available_vehicles:
                for task in unassigned_tasks:
                    if vehicle.get_available_capacity() >= task.weight:
                        dist = vehicle.position.distance_to(task.origin)
                        if dist < best_distance:
                            best_distance = dist
                            best_assignment = (vehicle, task)

            if best_assignment is None:
                break

            vehicle, task = best_assignment
            assignments[vehicle.id].append(task)
            unassigned_tasks.remove(task)
            vehicle.current_load += task.weight
            if vehicle.get_available_capacity() <= 0:
                available_vehicles.remove(vehicle)

        return dict(assignments)


class DispatcherLargestFirst(Dispatcher):
    """Greedy strategy: assign heavier tasks first."""

    def assign_tasks(self, tasks: List[Task], vehicles: List[Vehicle]) -> Dict[str, List[Task]]:
        assignments = defaultdict(list)
        sorted_tasks = sorted(tasks, key=lambda t: t.weight, reverse=True)

        for task in sorted_tasks:
            best_vehicle = None
            best_distance = float("inf")

            for vehicle in vehicles:
                if (
                    vehicle.get_available_capacity() >= task.weight
                    and vehicle.can_reach(task.origin)
                ):
                    dist = vehicle.position.distance_to(task.origin)
                    if dist < best_distance:
                        best_distance = dist
                        best_vehicle = vehicle

            if best_vehicle is not None:
                assignments[best_vehicle.id].append(task)
                best_vehicle.current_load += task.weight

        return dict(assignments)
