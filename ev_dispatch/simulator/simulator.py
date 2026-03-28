from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.energy import EnergyManager
from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.task import Task
from ev_dispatch.core.vehicle import Vehicle


class Simulator:
    """Main simulation loop for dynamic dispatch."""

    def __init__(
        self,
        network: RoadNetwork,
        vehicles: List[Vehicle],
        charging_stations: List[ChargingStation],
        dispatcher: Dispatcher,
    ):
        self.network = network
        self.vehicles = vehicles
        self.charging_stations = charging_stations
        self.dispatcher = dispatcher

        self.current_time = datetime.now()
        self.completed_tasks: List[Task] = []
        self.failed_tasks: List[Task] = []

    def generate_random_task(self, current_time: datetime) -> Task:
        task_id = f"task_{len(self.completed_tasks) + len(self.failed_tasks)}"
        origin = Location(
            np.random.uniform(0, self.network.width),
            np.random.uniform(0, self.network.height),
            f"origin_{task_id}",
        )
        destination = Location(
            np.random.uniform(0, self.network.width),
            np.random.uniform(0, self.network.height),
            f"dest_{task_id}",
        )
        weight = float(np.random.uniform(10, 500))
        deadline = current_time + timedelta(hours=float(np.random.uniform(1, 8)))

        return Task(
            id=task_id,
            origin=origin,
            destination=destination,
            weight=weight,
            created_time=current_time,
            deadline=deadline,
        )

    def run_simulation(self, num_steps: int = 100, tasks_per_step: int = 3) -> Dict[str, float]:
        total_distance = 0.0
        total_score = 0.0

        for _ in range(num_steps):
            new_tasks = [self.generate_random_task(self.current_time) for _ in range(tasks_per_step)]
            assignments = self.dispatcher.assign_tasks(new_tasks, self.vehicles)

            for vehicle_id, tasks_list in assignments.items():
                vehicle = next(v for v in self.vehicles if v.id == vehicle_id)
                for task in tasks_list:
                    dist = vehicle.position.distance_to(task.origin) + task.get_delivery_distance()
                    energy_used = EnergyManager.calculate_consumption(dist, task.weight)
                    total_distance += dist

                    if energy_used <= vehicle.current_battery:
                        vehicle.current_battery -= energy_used
                        vehicle.position = task.destination
                        self.completed_tasks.append(task)
                        total_score += 100 - (dist * 0.1)
                    else:
                        self.failed_tasks.append(task)
                        total_score -= 50

            self.current_time += timedelta(hours=1)

            for vehicle in self.vehicles:
                if vehicle.current_battery < 30:
                    vehicle.current_battery = vehicle.battery_capacity

        return {
            "completed": len(self.completed_tasks),
            "failed": len(self.failed_tasks),
            "total_distance": total_distance,
            "total_score": total_score,
            "avg_score_per_task": total_score / (len(self.completed_tasks) + 1),
        }
