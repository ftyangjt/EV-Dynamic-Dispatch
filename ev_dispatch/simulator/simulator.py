from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.energy import EnergyManager
from ev_dispatch.core.interfaces import Action, SimulationFrame, SimulationState
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
        self.frames: List[SimulationFrame] = []

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

    def _build_frame(self, step: int, pending_tasks: List[Task]) -> SimulationFrame:
        return SimulationFrame(
            current_time=self.current_time,
            step=step,
            vehicle_positions={v.id: (v.position.x, v.position.y) for v in self.vehicles},
            vehicle_battery={v.id: v.current_battery for v in self.vehicles},
            pending_task_ids=[t.id for t in pending_tasks],
            completed_task_ids=[t.id for t in self.completed_tasks],
            failed_task_ids=[t.id for t in self.failed_tasks],
        )

    def _execute_action(
        self,
        action: Action,
        task_map: Dict[str, Task],
    ) -> Dict[str, float]:
        score_delta = 0.0
        distance_delta = 0.0
        time_delta_hours = 0.0

        vehicle = next((v for v in self.vehicles if v.id == action.vehicle_id), None)
        if vehicle is None:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}

        if action.type != "assign_task" or action.task_id is None:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}

        task = task_map.get(action.task_id)
        if task is None or task.completed:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}

        # 路网距离与行驶时间（拥堵随当前时刻变化）
        depart_time = self.current_time
        dist_to_pickup = self.network.shortest_distance(vehicle.position, task.origin, method="dijkstra")
        t_to_pickup = self.network.shortest_travel_time_hours(vehicle.position, task.origin, start_time=depart_time)

        # 近似：到达取货点后的时间 = depart_time + t_to_pickup（拥堵按到达时刻重新取一片）
        arrive_pickup_time = depart_time + timedelta(hours=t_to_pickup)
        dist_delivery = self.network.shortest_distance(task.origin, task.destination, method="dijkstra")
        t_delivery = self.network.shortest_travel_time_hours(task.origin, task.destination, start_time=arrive_pickup_time)

        dist = dist_to_pickup + dist_delivery
        time_delta_hours = t_to_pickup + t_delivery
        energy_used = EnergyManager.calculate_consumption(dist, task.weight)
        distance_delta += dist

        if energy_used <= vehicle.current_battery:
            vehicle.current_battery -= energy_used
            vehicle.position = task.destination
            task.completed = True
            self.completed_tasks.append(task)
            # 简单收益：路程越短越好 + 时间越短越好
            score_delta += 100 - (dist * 0.1) - (time_delta_hours * 2.0)
        else:
            self.failed_tasks.append(task)
            score_delta -= 50

        return {"distance": distance_delta, "score": score_delta, "time_hours": time_delta_hours}

    def run_simulation(self, num_steps: int = 100, tasks_per_step: int = 3) -> Dict[str, float]:
        total_distance = 0.0
        total_time_hours = 0.0
        total_score = 0.0

        for step in range(num_steps):
            new_tasks = [self.generate_random_task(self.current_time) for _ in range(tasks_per_step)]
            task_map = {t.id: t for t in new_tasks}

            state = SimulationState(
                current_time=self.current_time,
                tasks=new_tasks,
                vehicles=self.vehicles,
                charging_stations=self.charging_stations,
                network=self.network,
                extra_info={"step": step},
            )
            actions = self.dispatcher.generate_actions(state)

            for action in actions:
                delta = self._execute_action(action, task_map)
                total_distance += delta["distance"]
                total_time_hours += delta["time_hours"]
                total_score += delta["score"]

            self.current_time += timedelta(hours=1)

            for vehicle in self.vehicles:
                if vehicle.current_battery < 30:
                    vehicle.current_battery = vehicle.battery_capacity

            pending = [t for t in new_tasks if not t.completed and t not in self.failed_tasks]
            self.frames.append(self._build_frame(step=step, pending_tasks=pending))

        return {
            "completed": len(self.completed_tasks),
            "failed": len(self.failed_tasks),
            "total_distance": total_distance,
            "total_time_hours": total_time_hours,
            "total_score": total_score,
            "avg_score_per_task": total_score / (len(self.completed_tasks) + 1),
        }

    def get_frames(self) -> List[SimulationFrame]:
        """可视化模块读取全量轨迹帧。"""
        return list(self.frames)

    def get_latest_frame(self) -> Optional[SimulationFrame]:
        """可视化模块读取最新一帧状态。"""
        if not self.frames:
            return None
        return self.frames[-1]
