from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import json
import os

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.assignment import estimate_task_assignment
from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.energy import EnergyManager
from ev_dispatch.core.interfaces import Action, SimulationFrame, SimulationState
from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.task import Task, CargoType
from ev_dispatch.core.vehicle import VehicleStatus
from ev_dispatch.core.vehicle import Vehicle
from ev_dispatch.scenarios.default import CargoConfig


DEFAULT_SIMULATION_START_TIME = datetime(2026, 1, 1, 8, 0, 0)


def parse_simulation_start_time(raw: Optional[str]) -> datetime:
    """Parse an ISO datetime, falling back to the reproducible default."""
    if raw is None or not str(raw).strip():
        return DEFAULT_SIMULATION_START_TIME

    value = str(raw).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "Invalid start time. Use ISO format, for example: 2026-01-01T08:00:00"
        ) from exc


class Simulator:
    """Main simulation loop for dynamic dispatch."""

    def __init__(
        self,
        network: RoadNetwork,
        vehicles: List[Vehicle],
        charging_stations: List[ChargingStation],
        dispatcher: Dispatcher,
        cargo_config: CargoConfig = None,
        random_seed: Optional[int] = None,
        start_time: Optional[datetime] = None,
        debug_run_id: str = "pre",
    ):
        self.network = network
        self.vehicles = vehicles
        self.charging_stations = charging_stations
        self.dispatcher = dispatcher
        self.cargo_config = cargo_config or CargoConfig()  # 默认配置
        self.random_seed = random_seed
        self.rng = np.random.default_rng(random_seed)
        self.debug_run_id = debug_run_id
        self._task_seq = 0

        self.current_time = start_time or DEFAULT_SIMULATION_START_TIME
        self.completed_tasks: List[Task] = []
        self.failed_tasks: List[Task] = []
        self.all_tasks: List[Task] = []
        self._completed_task_ids = set()
        self._failed_task_ids = set()
        self.frames: List[SimulationFrame] = []

    def _mark_task_failed(self, task: Task, reason: str) -> None:
        task.failed = True
        task.failed_time = self.current_time
        task.failure_reason = reason
        if task.id not in self._failed_task_ids and task.id not in self._completed_task_ids:
            self.failed_tasks.append(task)
            self._failed_task_ids.add(task.id)

    def _mark_task_completed(self, task: Task, completed_time: datetime) -> None:
        task.completed = True
        task.completed_time = completed_time
        task.failed = False
        task.failure_reason = ""
        if task.id in self._failed_task_ids:
            self._failed_task_ids.remove(task.id)
            self.failed_tasks = [t for t in self.failed_tasks if t.id != task.id]
        if task.id not in self._completed_task_ids:
            self.completed_tasks.append(task)
            self._completed_task_ids.add(task.id)

    def _advance_vehicle_tasks(self) -> Dict[str, float]:
        distance_delta = 0.0
        time_delta_hours = 0.0
        cost_delta = 0.0
        score_delta = 0.0
        now = self.current_time

        for vehicle in self.vehicles:
            if vehicle.status not in (VehicleStatus.EN_ROUTE, VehicleStatus.OCCUPIED):
                continue
            if vehicle.charge_destination_station_id is not None:
                continue
            if vehicle.available_at is None or vehicle.available_at > now:
                continue

            task_ids = list(vehicle.current_tasks)
            vehicle.current_tasks.clear()
            vehicle.current_load = 0.0
            vehicle.current_volume = 0.0
            vehicle.available_at = None
            vehicle.status = VehicleStatus.IDLE

            for task_id in task_ids:
                task = next((t for t in self.all_tasks if t.id == task_id), None)
                if task is None or task.completed or task.failed:
                    continue
                vehicle.position = task.destination
                self._mark_task_completed(task, completed_time=task.planned_completion_time or now)
                distance_delta += task.planned_distance
                time_delta_hours += task.planned_time_hours
                cost_delta += task.planned_cost
                score_delta += task.planned_score

        return {
            "distance": distance_delta,
            "time_hours": time_delta_hours,
            "cost": cost_delta,
            "score": score_delta,
        }

    def _expire_overdue_tasks(self) -> Dict[str, float]:
        score_delta = 0.0
        for task in self.all_tasks:
            if task.completed or task.failed or task.assigned_vehicles:
                continue
            if task.deadline <= self.current_time:
                self._mark_task_failed(task, "deadline_expired")
                score_delta -= 30.0
        return {"distance": 0.0, "time_hours": 0.0, "cost": 0.0, "score": score_delta}

    @staticmethod
    def _interpolate_position(start: Location, end: Location, ratio: float) -> tuple:
        ratio = max(0.0, min(1.0, ratio))
        return (
            start.x + (end.x - start.x) * ratio,
            start.y + (end.y - start.y) * ratio,
        )

    def _interpolate_path_position(self, path: List[Location], ratio: float) -> tuple:
        if not path:
            return (0.0, 0.0)
        if len(path) == 1:
            return (path[0].x, path[0].y)

        ratio = max(0.0, min(1.0, ratio))
        segment_lengths = [
            path[i].distance_to(path[i + 1])
            for i in range(len(path) - 1)
        ]
        total_length = sum(segment_lengths)
        if total_length <= 1e-9:
            return (path[-1].x, path[-1].y)

        target_distance = total_length * ratio
        covered = 0.0
        for i, segment_length in enumerate(segment_lengths):
            if covered + segment_length >= target_distance:
                local_ratio = (target_distance - covered) / max(1e-9, segment_length)
                return self._interpolate_position(path[i], path[i + 1], local_ratio)
            covered += segment_length

        return (path[-1].x, path[-1].y)

    def _path_position_at_distance(self, path: List[Location], target_distance: float) -> tuple:
        if not path:
            return (0.0, 0.0)
        if len(path) == 1:
            return (path[0].x, path[0].y)

        target_distance = max(0.0, float(target_distance))
        covered = 0.0
        for i in range(len(path) - 1):
            segment_length = path[i].distance_to(path[i + 1])
            if segment_length <= 1e-9:
                continue
            if covered + segment_length >= target_distance:
                local_ratio = (target_distance - covered) / segment_length
                return self._interpolate_position(path[i], path[i + 1], local_ratio)
            covered += segment_length

        return (path[-1].x, path[-1].y)

    def _interpolate_timed_path_position(self, timed_path: List[tuple], elapsed_hours: float) -> tuple:
        if not timed_path:
            return (0.0, 0.0)
        if len(timed_path) == 1:
            loc, _hours = timed_path[0]
            return (loc.x, loc.y)

        elapsed_hours = max(0.0, float(elapsed_hours))
        first_loc, first_hours = timed_path[0]
        if elapsed_hours <= float(first_hours):
            return (first_loc.x, first_loc.y)

        for index in range(1, len(timed_path)):
            prev_loc, prev_hours = timed_path[index - 1]
            next_loc, next_hours = timed_path[index]
            prev_hours = float(prev_hours)
            next_hours = float(next_hours)
            if elapsed_hours <= next_hours:
                segment_hours = max(1e-9, next_hours - prev_hours)
                local_ratio = (elapsed_hours - prev_hours) / segment_hours
                return self._interpolate_position(prev_loc, next_loc, local_ratio)

        last_loc, _last_hours = timed_path[-1]
        return (last_loc.x, last_loc.y)

    def _vehicle_display_position(self, vehicle: Vehicle, display_time: Optional[datetime] = None) -> tuple:
        if vehicle.status not in (VehicleStatus.EN_ROUTE, VehicleStatus.OCCUPIED):
            return (vehicle.position.x, vehicle.position.y)
        now = display_time or self.current_time

        if vehicle.charge_destination_station_id:
            _phase, route, route_progress, _route_length = self._vehicle_phase_route_progress(vehicle, now)
            route_locations = [
                Location(float(point["x"]), float(point["y"]), str(point.get("name") or ""))
                for point in route
                if point is not None
            ]
            if route_locations:
                return self._path_position_at_distance(route_locations, route_progress)
            return (vehicle.position.x, vehicle.position.y)

        if not vehicle.current_tasks:
            return (vehicle.position.x, vehicle.position.y)

        task = next((t for t in self.all_tasks if t.id == vehicle.current_tasks[0]), None)
        if (
            task is None
            or task.transport_start_position is None
            or task.start_transport_time is None
            or task.planned_completion_time is None
        ):
            return (vehicle.position.x, vehicle.position.y)

        _phase, route, route_progress, _route_length = self._vehicle_phase_route_progress(vehicle, now)
        route_locations = [
            Location(float(point["x"]), float(point["y"]), str(point.get("name") or ""))
            for point in route
            if point is not None
        ]
        if route_locations:
            return self._path_position_at_distance(route_locations, route_progress)

        if now <= task.start_transport_time:
            return (task.transport_start_position.x, task.transport_start_position.y)
        if now >= task.planned_completion_time:
            return (task.destination.x, task.destination.y)
        return (vehicle.position.x, vehicle.position.y)

    @staticmethod
    def _location_snapshot(location: Optional[Location]) -> Optional[Dict[str, object]]:
        if location is None:
            return None
        return {
            "name": location.name,
            "x": float(location.x),
            "y": float(location.y),
        }

    @classmethod
    def _path_snapshot(cls, path: List[Location]) -> List[Dict[str, object]]:
        return [cls._location_snapshot(loc) for loc in path if loc is not None]

    @classmethod
    def _combine_paths(cls, *paths: List[Location]) -> List[Location]:
        combined: List[Location] = []
        for path in paths:
            for loc in path or []:
                if loc is None:
                    continue
                if combined and combined[-1].distance_to(loc) <= 1e-9:
                    continue
                combined.append(loc)
        return combined

    @classmethod
    def _combine_path_snapshot(cls, *paths: List[Location]) -> List[Dict[str, object]]:
        combined = cls._combine_paths(*paths)
        return cls._path_snapshot(combined)

    @staticmethod
    def _path_length(path: List[Location]) -> float:
        if not path or len(path) < 2:
            return 0.0
        return float(sum(path[i].distance_to(path[i + 1]) for i in range(len(path) - 1)))

    def _timed_path_progress(self, timed_path: List[tuple], elapsed_hours: float) -> float:
        if not timed_path or len(timed_path) < 2:
            return 0.0

        elapsed_hours = max(0.0, float(elapsed_hours))
        progress = 0.0
        for index in range(1, len(timed_path)):
            prev_loc, prev_hours = timed_path[index - 1]
            next_loc, next_hours = timed_path[index]
            segment_length = prev_loc.distance_to(next_loc)
            prev_hours = float(prev_hours)
            next_hours = float(next_hours)
            if elapsed_hours <= next_hours:
                segment_hours = max(1e-9, next_hours - prev_hours)
                local_ratio = max(0.0, min(1.0, (elapsed_hours - prev_hours) / segment_hours))
                return float(progress + segment_length * local_ratio)
            progress += segment_length

        return float(progress)

    def _task_status(self, task: Task) -> str:
        if task.completed:
            return "completed"
        if task.failed:
            return "failed"
        if task.assigned_vehicles:
            return "in_progress"
        return "pending"

    def _vehicle_phase_route_progress(self, vehicle: Vehicle, display_time: datetime) -> tuple:
        if vehicle.status == VehicleStatus.CHARGING:
            return "charging", [], 0.0, 0.0
        if vehicle.charge_destination_station_id:
            route_locations = [loc for loc, _hours in vehicle.charge_timed_path] if vehicle.charge_timed_path else []
            if not route_locations:
                route_locations = vehicle.charge_route_path or []
            if not route_locations:
                route_locations = [
                    loc
                    for loc in (vehicle.charge_route_start_position, vehicle.position)
                    if loc is not None
                ]
            route = self._path_snapshot(route_locations)
            route_length = self._path_length(route_locations)
            if route_length <= 1e-9 and vehicle.charge_route_distance > 0:
                route_length = float(vehicle.charge_route_distance)
            if (
                vehicle.charge_route_start_time is None
                or vehicle.charge_arrival_time is None
                or display_time <= vehicle.charge_route_start_time
            ):
                return "to_charge", route, 0.0, route_length
            if display_time >= vehicle.charge_arrival_time:
                return "to_charge", route, route_length, route_length

            elapsed = (display_time - vehicle.charge_route_start_time).total_seconds() / 3600.0
            if vehicle.charge_timed_path:
                progress = self._timed_path_progress(vehicle.charge_timed_path, elapsed)
            else:
                duration_hours = max(
                    1e-9,
                    (vehicle.charge_arrival_time - vehicle.charge_route_start_time).total_seconds() / 3600.0,
                )
                progress = route_length * max(0.0, min(1.0, elapsed / duration_hours))
            return "to_charge", route, min(progress, route_length), route_length
        if vehicle.status not in (VehicleStatus.EN_ROUTE, VehicleStatus.OCCUPIED) or not vehicle.current_tasks:
            return "idle", [], 0.0, 0.0

        task = next((t for t in self.all_tasks if t.id == vehicle.current_tasks[0]), None)
        if (
            task is None
            or task.transport_start_position is None
            or task.start_transport_time is None
            or task.planned_completion_time is None
        ):
            return vehicle.status.value, [], 0.0, 0.0

        pickup_time = task.planned_pickup_time or task.start_transport_time
        pickup_path = (
            [loc for loc, _hours in task.pickup_timed_path]
            if task.pickup_timed_path
            else task.pickup_path or [task.transport_start_position, task.origin]
        )
        delivery_path = (
            [loc for loc, _hours in task.delivery_timed_path]
            if task.delivery_timed_path
            else task.delivery_path or [task.origin, task.destination]
        )
        full_route_locations = self._combine_paths(pickup_path, delivery_path)
        full_route = self._path_snapshot(full_route_locations)
        route_length = self._path_length(full_route_locations)
        pickup_length = self._path_length(pickup_path)
        delivery_length = self._path_length(delivery_path)

        if display_time <= task.start_transport_time:
            return "to_pickup", full_route, 0.0, route_length
        if display_time >= task.planned_completion_time:
            return "to_delivery", full_route, route_length, route_length

        if pickup_time is not None and display_time < pickup_time:
            elapsed = (display_time - task.start_transport_time).total_seconds() / 3600.0
            if task.pickup_timed_path:
                progress = self._timed_path_progress(task.pickup_timed_path, elapsed)
            else:
                duration_hours = max(1e-9, (pickup_time - task.start_transport_time).total_seconds() / 3600.0)
                progress = pickup_length * max(0.0, min(1.0, elapsed / duration_hours))
            return "to_pickup", full_route, min(progress, route_length), route_length

        delivery_start = max(pickup_time, task.start_transport_time)
        elapsed = (display_time - delivery_start).total_seconds() / 3600.0
        if task.delivery_timed_path:
            delivery_progress = self._timed_path_progress(task.delivery_timed_path, elapsed)
        else:
            duration_hours = max(1e-9, (task.planned_completion_time - delivery_start).total_seconds() / 3600.0)
            delivery_progress = delivery_length * max(0.0, min(1.0, elapsed / duration_hours))
        progress = pickup_length + delivery_progress
        return "to_delivery", full_route, min(progress, route_length), route_length

    def _vehicle_snapshot(self, vehicle: Vehicle, display_time: datetime) -> Dict[str, object]:
        phase, route, route_progress, route_length = self._vehicle_phase_route_progress(vehicle, display_time)
        return {
            "id": vehicle.id,
            "type": vehicle.vehicle_type.name,
            "status": vehicle.status.value,
            "phase": phase,
            "battery": float(vehicle.current_battery),
            "battery_capacity": float(vehicle.battery_capacity),
            "battery_ratio": float(vehicle.current_battery) / max(1e-9, float(vehicle.battery_capacity)),
            "min_battery_threshold": float(vehicle.min_battery_threshold),
            "current_load": float(vehicle.current_load),
            "load_capacity": float(vehicle.load_capacity),
            "current_volume": float(vehicle.current_volume),
            "volume_capacity": float(vehicle.volume_capacity),
            "max_speed_kmh": float(vehicle.max_speed_kmh),
            "current_tasks": list(vehicle.current_tasks),
            "available_at": vehicle.available_at.isoformat() if vehicle.available_at else None,
            "supported_cargo_types": sorted(vehicle.supported_cargo_types),
            "charging_station_id": vehicle.charging_station_id,
            "charge_destination_station_id": vehicle.charge_destination_station_id,
            "charge_arrival_time": vehicle.charge_arrival_time.isoformat() if vehicle.charge_arrival_time else None,
            "charging_duration": float(vehicle.charging_duration),
            "target_battery": float(vehicle.target_battery),
            "route": route,
            "route_progress": float(route_progress),
            "route_length": float(route_length),
        }

    def _task_snapshot(self, task: Task) -> Dict[str, object]:
        cargo_type = getattr(task.cargo_type, "value", task.cargo_type)
        return {
            "id": task.id,
            "status": self._task_status(task),
            "origin": self._location_snapshot(task.origin),
            "destination": self._location_snapshot(task.destination),
            "weight": float(task.weight),
            "volume": float(task.volume),
            "cargo_type": cargo_type,
            "priority": float(task.priority),
            "created_time": task.created_time.isoformat(),
            "deadline": task.deadline.isoformat(),
            "assigned_vehicles": list(task.assigned_vehicles),
            "start_transport_time": task.start_transport_time.isoformat() if task.start_transport_time else None,
            "planned_pickup_time": task.planned_pickup_time.isoformat() if task.planned_pickup_time else None,
            "planned_completion_time": task.planned_completion_time.isoformat() if task.planned_completion_time else None,
            "completed_time": task.completed_time.isoformat() if task.completed_time else None,
            "failed_time": task.failed_time.isoformat() if task.failed_time else None,
            "failure_reason": task.failure_reason,
            "planned_distance": float(task.planned_distance),
            "planned_time_hours": float(task.planned_time_hours),
            "planned_cost": float(task.planned_cost),
            "planned_score": float(task.planned_score),
            "pickup_path": self._path_snapshot(task.pickup_path),
            "delivery_path": self._path_snapshot(task.delivery_path),
        }

    def _station_snapshot(self, station: ChargingStation) -> Dict[str, object]:
        status = station.get_status()
        return {
            "id": station.id,
            "position": self._location_snapshot(station.position),
            "num_chargers": int(station.num_chargers),
            "charging_power": float(station.charging_power),
            "waiting_queue": list(station.waiting_queue),
            "charging_vehicles": {
                vid: {
                    "start_time": rec.start_time.isoformat(),
                    "duration_minutes": float(rec.duration_minutes),
                    "target_energy": float(rec.target_energy),
                    "end_time": rec.end_time.isoformat() if rec.end_time else None,
                }
                for vid, rec in station.charging_vehicles.items()
            },
            **status,
        }

    # #region agent log
    def _dbg(self, hypothesisId: str, location: str, message: str, data: dict) -> None:
        try:
            payload = {
                "sessionId": "e24f56",
                "runId": self.debug_run_id,
                "hypothesisId": hypothesisId,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(datetime.now().timestamp() * 1000),
            }
            with open(os.path.join(os.getcwd(), "debug-e24f56.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass
    # #endregion

    def generate_random_task(self, current_time: datetime) -> Task:
        """Generate random task with cargo type distribution.
        
        Cargo types:
        - TYPE_1: 通用货物（所有车都能装），占比 70%（默认）
        - TYPE_2: 专用货物（仅Compact车），占比 10%
        - TYPE_3: 专用货物（仅Standard车），占比 10%
        - TYPE_4: 专用货物（仅Large车），占比 10%
        
        所有任务的 origin/destination 都必须是网络节点！
        """
        task_id = f"task_{self._task_seq}"
        self._task_seq += 1
        
        # 从网络节点中随机选择起点和终点
        num_nodes = len(self.network.nodes)
        origin_idx = int(self.rng.integers(0, num_nodes))
        destination_idx = int(self.rng.integers(0, num_nodes))
        
        # 确保起点和终点不同
        while destination_idx == origin_idx and num_nodes > 1:
            destination_idx = int(self.rng.integers(0, num_nodes))
        
        origin_node_id, origin_location = self.network.nodes[origin_idx]
        dest_node_id, dest_location = self.network.nodes[destination_idx]
        
        origin = origin_location
        destination = dest_location
        
        weight = float(self.rng.uniform(10, 500))
        volume = float(self.rng.uniform(0.1, 3.0))  # m^3，随机体积
        deadline = current_time + timedelta(hours=float(self.rng.uniform(1, 8)))
        
        # 按比例生成货物类型 - 转换为 CargoType enum
        cargo_distribution = self.cargo_config.get_cargo_type_distribution()
        cargo_type_str = self.rng.choice(
            list(cargo_distribution.keys()),
            p=list(cargo_distribution.values())
        )
        # 将字符串映射到 CargoType enum
        cargo_type_map = {
            "type_1": CargoType.TYPE_1,
            "type_2": CargoType.TYPE_2,
            "type_3": CargoType.TYPE_3,
            "type_4": CargoType.TYPE_4,
        }
        cargo_type = cargo_type_map.get(cargo_type_str, CargoType.TYPE_1)

        # #region agent log
        self._dbg(
            "H1",
            "simulator.py:generate_random_task",
            "generated_task",
            {
                "task_id": task_id,
                "cargo_type": cargo_type.value,
                "weight": weight,
                "volume": volume,
                "created_time": current_time.isoformat(),
                "deadline": deadline.isoformat(),
                "seed": self.random_seed,
                "origin_node": origin_node_id,
                "dest_node": dest_node_id,
            },
        )
        # #endregion

        return Task(
            id=task_id,
            origin=origin,
            destination=destination,
            weight=weight,
            volume=volume,
            cargo_type=cargo_type,
            created_time=current_time,
            deadline=deadline,
            start_transport_time=current_time,
            priority=1.0,
        )

    def _build_frame(
        self,
        step: int,
        pending_tasks: List[Task],
        frame_time: Optional[datetime] = None,
    ) -> SimulationFrame:
        display_time = frame_time or self.current_time
        return SimulationFrame(
            current_time=display_time,
            step=step,
            vehicle_positions={v.id: self._vehicle_display_position(v, display_time) for v in self.vehicles},
            vehicle_battery={v.id: v.current_battery for v in self.vehicles},
            pending_task_ids=[t.id for t in pending_tasks],
            completed_task_ids=[t.id for t in self.completed_tasks],
            failed_task_ids=[t.id for t in self.failed_tasks],
            vehicle_details={v.id: self._vehicle_snapshot(v, display_time) for v in self.vehicles},
            task_details={t.id: self._task_snapshot(t) for t in self.all_tasks},
            station_details={s.id: self._station_snapshot(s) for s in self.charging_stations},
        )

    def _find_station(self, station_id: str) -> Optional[ChargingStation]:
        return next((s for s in self.charging_stations if s.id == station_id), None)

    def _advance_charge_routes(self) -> Dict[str, float]:
        distance_delta = 0.0
        time_delta_hours = 0.0
        now = self.current_time

        for vehicle in self.vehicles:
            if (
                vehicle.status != VehicleStatus.EN_ROUTE
                or vehicle.charge_destination_station_id is None
                or vehicle.charge_arrival_time is None
                or vehicle.charge_arrival_time > now
            ):
                continue

            station = self._find_station(vehicle.charge_destination_station_id)
            if station is None:
                vehicle.clear_charge_route()
                vehicle.available_at = None
                vehicle.status = VehicleStatus.IDLE
                continue

            vehicle.position = station.position
            distance_delta += float(vehicle.charge_route_distance)
            if vehicle.charge_route_start_time is not None:
                time_delta_hours += (
                    vehicle.charge_arrival_time - vehicle.charge_route_start_time
                ).total_seconds() / 3600.0
            vehicle.clear_charge_route()
            vehicle.available_at = None
            vehicle.status = VehicleStatus.IDLE
            station.enqueue(vehicle.id)

        return {"distance": distance_delta, "time_hours": time_delta_hours, "cost": 0.0, "score": 0.0}

    def _advance_charging_stations(self) -> None:
        """Advance charging completion and start queued vehicles with heap-backed station state."""
        now = self.current_time

        for station in self.charging_stations:
            for record in station.complete_due_charging(now):
                vehicle = next((v for v in self.vehicles if v.id == record.vehicle_id), None)
                if vehicle is not None and vehicle.status == VehicleStatus.CHARGING:
                    vehicle.end_charging()

        for station in self.charging_stations:
            while len(station.charging_vehicles) < station.num_chargers:
                vehicle_id = station.dequeue()
                if vehicle_id is None:
                    break
                vehicle = next((v for v in self.vehicles if v.id == vehicle_id), None)
                if vehicle is None:
                    continue
                if vehicle.status in (VehicleStatus.MAINTENANCE, VehicleStatus.EN_ROUTE, VehicleStatus.OCCUPIED):
                    continue

                effective_power = min(float(vehicle.charging_speed_kwh_per_hour), float(station.charging_power))
                energy_needed = max(0.0, float(vehicle.battery_capacity - vehicle.current_battery))
                duration_minutes = (
                    0.0
                    if effective_power <= 1e-9
                    else (energy_needed / effective_power) * 60.0
                )

                if not station.start_charging(vehicle_id=vehicle_id, target_energy=energy_needed, start_time=now):
                    continue
                station.update_charging_duration(vehicle_id, duration_minutes)

                vehicle.is_charging = True
                vehicle.charging_station_id = station.id
                vehicle.charging_start_time = now
                vehicle.charging_duration = float(duration_minutes)
                vehicle.target_battery = vehicle.battery_capacity
                vehicle.status = VehicleStatus.CHARGING

    def _next_event_time(self, horizon_time: datetime) -> Optional[datetime]:
        """Return the next known event time before or at the simulation horizon."""
        candidates: List[datetime] = []

        for vehicle in self.vehicles:
            if (
                vehicle.status in (VehicleStatus.EN_ROUTE, VehicleStatus.OCCUPIED)
                and vehicle.available_at is not None
                and self.current_time < vehicle.available_at <= horizon_time
            ):
                candidates.append(vehicle.available_at)

        for station in self.charging_stations:
            for record in station.charging_vehicles.values():
                finish_time = station._record_finish_time(record)
                if self.current_time < finish_time <= horizon_time:
                    candidates.append(finish_time)

        for task in self.all_tasks:
            if task.completed or task.failed or task.assigned_vehicles:
                continue
            if self.current_time < task.deadline <= horizon_time:
                candidates.append(task.deadline)

        return min(candidates) if candidates else None

    def _advance_due_events(self) -> Dict[str, float]:
        """Process every event whose timestamp is due at the current simulation time."""
        total = {"distance": 0.0, "time_hours": 0.0, "cost": 0.0, "score": 0.0}

        while True:
            before = (
                len(self._completed_task_ids),
                len(self._failed_task_ids),
                sum(len(s.charging_vehicles) for s in self.charging_stations),
                sum(len(s.waiting_queue) for s in self.charging_stations),
                tuple((v.id, v.status.value, v.available_at) for v in self.vehicles),
            )

            for delta in (self._advance_charge_routes(), self._advance_vehicle_tasks(), self._expire_overdue_tasks()):
                total["distance"] += delta["distance"]
                total["time_hours"] += delta["time_hours"]
                total["score"] += delta["score"]
                total["cost"] += delta.get("cost", 0.0)
            self._advance_charging_stations()

            after = (
                len(self._completed_task_ids),
                len(self._failed_task_ids),
                sum(len(s.charging_vehicles) for s in self.charging_stations),
                sum(len(s.waiting_queue) for s in self.charging_stations),
                tuple((v.id, v.status.value, v.available_at) for v in self.vehicles),
            )
            if after == before:
                break

        return total

    def _pending_tasks(self) -> List[Task]:
        return [
            t
            for t in self.all_tasks
            if not t.completed and not t.failed and not t.assigned_vehicles
        ]

    def _append_frame(self, step: int, frame_time: Optional[datetime] = None) -> None:
        self.frames.append(
            self._build_frame(
                step=step,
                pending_tasks=self._pending_tasks(),
                frame_time=frame_time or self.current_time,
            )
        )

    def _add_delta(self, totals: Dict[str, float], delta: Dict[str, float]) -> None:
        totals["distance"] += delta["distance"]
        totals["time_hours"] += delta["time_hours"]
        totals["score"] += delta["score"]
        totals["cost"] += delta.get("cost", 0.0)

    def _execute_action(
        self,
        action: Action,
        task_map: Dict[str, Task],
    ) -> Dict[str, float]:
        score_delta = 0.0
        distance_delta = 0.0
        time_delta_hours = 0.0
        cost_delta = 0.0

        vehicle = next((v for v in self.vehicles if v.id == action.vehicle_id), None)
        if vehicle is None:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}

        # 充电动作：去充电站（进入 FIFO 排队，可能开始充电）
        if action.type == "go_charge" and action.station_id is not None:
            station = self._find_station(action.station_id)
            if station is None:
                return {"distance": 0.0, "score": 0.0, "time_hours": 0.0, "cost": 0.0}

            # 先推进站点状态，避免“本步一开始其实有人已充满但未释放”的假排队
            self._advance_charge_routes()
            self._advance_charging_stations()

            depart_time = self.current_time
            dist_to_station = self.network.shortest_distance(vehicle.position, station.position, method="dijkstra")
            station_road_metrics = self.network.path_road_metrics(
                vehicle.position,
                station.position,
                start_time=depart_time,
                vehicle_max_speed_kmh=vehicle.max_speed_kmh,
            )
            t_to_station = self.network.shortest_travel_time_hours(
                vehicle.position,
                station.position,
                start_time=depart_time,
                vehicle_max_speed_kmh=vehicle.max_speed_kmh,
            )

            # 去充电站的能耗（无载）
            energy_to_station = EnergyManager.calculate_consumption(
                distance=dist_to_station,
                load=0.0,
                speed_kmh=station_road_metrics["avg_speed_kmph"],
                efficiency=vehicle.efficiency,
                weather_factor=station_road_metrics["energy_factor"],
            )

            if energy_to_station > vehicle.current_battery:
                score_delta -= 20  # 电量不足以到达充电站，惩罚
                return {"distance": dist_to_station, "score": score_delta, "time_hours": t_to_station, "cost": 0.0}

            vehicle.current_battery -= energy_to_station
            vehicle.charge_destination_station_id = station.id
            vehicle.charge_route_start_time = depart_time
            vehicle.charge_arrival_time = depart_time + timedelta(hours=t_to_station)
            vehicle.charge_route_start_position = Location(
                vehicle.position.x,
                vehicle.position.y,
                vehicle.position.name,
            )
            vehicle.charge_route_path = self.network.shortest_path_locations(
                vehicle.position,
                station.position,
                method="dijkstra",
            )
            vehicle.charge_timed_path = self.network.timed_path_locations(
                vehicle.position,
                station.position,
                start_time=depart_time,
                vehicle_max_speed_kmh=vehicle.max_speed_kmh,
                method="dijkstra",
            )
            vehicle.charge_route_distance = float(dist_to_station)
            vehicle.available_at = vehicle.charge_arrival_time
            vehicle.status = VehicleStatus.EN_ROUTE

            return {"distance": distance_delta, "score": score_delta, "time_hours": time_delta_hours, "cost": 0.0}

        if action.type != "assign_task" or action.task_id is None:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}

        task = task_map.get(action.task_id)
        if task is None or task.completed or task.failed:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}
        if task.assigned_vehicles:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0, "cost": 0.0}
        if vehicle.status != VehicleStatus.IDLE or vehicle.available_at is not None:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0, "cost": 0.0}

        depart_time = self.current_time
        estimate = estimate_task_assignment(
            network=self.network,
            vehicle=vehicle,
            task=task,
            depart_time=depart_time,
            include_paths=True,
        )
        if not estimate.feasible:
            self._mark_task_failed(task, estimate.reason)
            score_delta -= 50
            return {"distance": 0.0, "score": score_delta, "time_hours": 0.0, "cost": 0.0}

        vehicle.current_battery -= estimate.energy_used
        vehicle.current_load += task.weight
        vehicle.current_volume += task.volume
        vehicle.current_tasks.append(task.id)
        vehicle.available_at = estimate.planned_completion_time
        vehicle.status = VehicleStatus.EN_ROUTE
        task.assigned_vehicles.append(vehicle.id)
        task.start_transport_time = depart_time
        task.transport_start_position = estimate.transport_start_position
        task.planned_pickup_time = estimate.planned_pickup_time
        task.planned_completion_time = vehicle.available_at
        task.pickup_path = estimate.pickup_path
        task.delivery_path = estimate.delivery_path
        task.pickup_timed_path = estimate.pickup_timed_path
        task.delivery_timed_path = estimate.delivery_timed_path
        task.planned_distance = estimate.total_distance
        task.planned_time_hours = estimate.total_hours
        task.planned_cost = estimate.cost
        score_delta += estimate.execution_score
        task.planned_score = score_delta

        return {"distance": 0.0, "score": 0.0, "time_hours": 0.0, "cost": 0.0}

    def _build_results(
        self,
        total_generated: int,
        total_distance: float,
        total_time_hours: float,
        total_cost: float,
        total_score: float,
    ) -> Dict[str, float]:
        pending_tasks = [
            t
            for t in self.all_tasks
            if not t.completed and not t.failed and not t.assigned_vehicles
        ]
        in_progress_tasks = [
            t
            for t in self.all_tasks
            if not t.completed and not t.failed and t.assigned_vehicles
        ]
        completed_count = len(self._completed_task_ids)
        failed_count = len(self._failed_task_ids)
        finished_count = completed_count + failed_count

        on_time_count = 0
        total_delay_hours = 0.0
        completed_service_hours = []
        for task in self.completed_tasks:
            if task.completed_time is None:
                continue
            if task.completed_time <= task.deadline:
                on_time_count += 1
            else:
                total_delay_hours += (task.completed_time - task.deadline).total_seconds() / 3600.0
            completed_service_hours.append(
                (task.completed_time - task.created_time).total_seconds() / 3600.0
            )

        avg_battery_ratio = 0.0
        low_battery_vehicle_count = 0
        charging_vehicle_count = 0
        avg_load_utilization = 0.0
        if self.vehicles:
            battery_ratios = [
                float(v.current_battery) / max(1e-9, float(v.battery_capacity))
                for v in self.vehicles
            ]
            avg_battery_ratio = float(sum(battery_ratios) / len(battery_ratios))
            low_battery_vehicle_count = sum(
                1 for v in self.vehicles if v.current_battery <= v.min_battery_threshold
            )
            charging_vehicle_count = sum(1 for v in self.vehicles if v.status == VehicleStatus.CHARGING)
            avg_load_utilization = float(
                sum(v.get_utilization_rate() for v in self.vehicles) / len(self.vehicles)
            )

        station_waits = [s.get_wait_time() for s in self.charging_stations]
        avg_station_wait_minutes = float(sum(station_waits) / len(station_waits)) if station_waits else 0.0
        max_station_wait_minutes = float(max(station_waits)) if station_waits else 0.0

        return {
            "completed": completed_count,
            "failed": failed_count,
            "generated": total_generated,
            "pending": len(pending_tasks),
            "in_progress": len(in_progress_tasks),
            "completion_rate": completed_count / max(1, total_generated),
            "failure_rate": failed_count / max(1, total_generated),
            "finished_rate": finished_count / max(1, total_generated),
            "on_time_completed": on_time_count,
            "on_time_rate": on_time_count / max(1, completed_count),
            "avg_delay_hours": total_delay_hours / max(1, completed_count),
            "avg_service_hours": (
                float(sum(completed_service_hours) / len(completed_service_hours))
                if completed_service_hours
                else 0.0
            ),
            "total_distance": total_distance,
            "total_time_hours": total_time_hours,
            "total_cost": total_cost,
            "total_score": total_score,
            "avg_score_per_task": total_score / max(1, completed_count),
            "distance_per_completed_task": total_distance / max(1, completed_count),
            "cost_per_completed_task": total_cost / max(1, completed_count),
            "avg_battery_ratio": avg_battery_ratio,
            "low_battery_vehicle_count": low_battery_vehicle_count,
            "charging_vehicle_count": charging_vehicle_count,
            "avg_load_utilization": avg_load_utilization,
            "avg_station_wait_minutes": avg_station_wait_minutes,
            "max_station_wait_minutes": max_station_wait_minutes,
        }

    def _run_simulation_legacy(self, num_steps: int = 100, tasks_per_step: int = 3) -> Dict[str, float]:
        total_distance = 0.0
        total_time_hours = 0.0
        total_score = 0.0
        total_cost = 0.0
        total_generated = 0

        # #region agent log
        self._dbg(
            "H1",
            "simulator.py:run_simulation",
            "simulation_start",
            {
                "num_steps": num_steps,
                "tasks_per_step": tasks_per_step,
                "seed": self.random_seed,
                "dispatcher": type(self.dispatcher).__name__,
            },
        )
        # #endregion

        for step in range(num_steps):
            # 先推进充电队列/完成充电，再生成任务与调度
            for delta in (self._advance_charge_routes(),):
                total_distance += delta["distance"]
                total_time_hours += delta["time_hours"]
                total_score += delta["score"]
                total_cost += delta.get("cost", 0.0)
            self._advance_charging_stations()
            for delta in (self._advance_vehicle_tasks(), self._expire_overdue_tasks()):
                total_distance += delta["distance"]
                total_time_hours += delta["time_hours"]
                total_score += delta["score"]
                total_cost += delta.get("cost", 0.0)

            new_tasks = [self.generate_random_task(self.current_time) for _ in range(tasks_per_step)]
            self.all_tasks.extend(new_tasks)
            total_generated += len(new_tasks)
            task_map = {t.id: t for t in self.all_tasks}

            state = SimulationState(
                current_time=self.current_time,
                tasks=self.all_tasks,
                vehicles=self.vehicles,
                charging_stations=self.charging_stations,
                network=self.network,
                extra_info={"step": step},
            )
            actions = self.dispatcher.generate_actions(state)

            # #region agent log
            self._dbg(
                "H2",
                "simulator.py:run_simulation",
                "step_actions",
                {
                    "step": step,
                    "generated_tasks": [t.id for t in new_tasks],
                    "generated_cargo_types": {t.id: t.cargo_type for t in new_tasks},
                    "pending_task_ids": [t.id for t in state.pending_tasks],
                    "actions": [
                        {"type": a.type, "vehicle_id": a.vehicle_id, "task_id": a.task_id, "note": a.note}
                        for a in actions
                    ],
                },
            )
            # #endregion

            for action in actions:
                delta = self._execute_action(action, task_map)
                total_distance += delta["distance"]
                total_time_hours += delta["time_hours"]
                total_score += delta["score"]
                total_cost += delta.get("cost", 0.0)

            pending = [
                t
                for t in self.all_tasks
                if not t.completed and not t.failed and not t.assigned_vehicles
            ]
            for substep in range(0, 4):
                frame_time = self.current_time + timedelta(hours=substep / 4)
                self.frames.append(
                    self._build_frame(
                        step=step,
                        pending_tasks=pending,
                        frame_time=frame_time,
                    )
                )

            self.current_time += timedelta(hours=1)
            for delta in (self._advance_vehicle_tasks(), self._expire_overdue_tasks()):
                total_distance += delta["distance"]
                total_time_hours += delta["time_hours"]
                total_score += delta["score"]
                total_cost += delta.get("cost", 0.0)

            pending = [
                t
                for t in self.all_tasks
                if not t.completed and not t.failed and not t.assigned_vehicles
            ]
            self.frames.append(
                self._build_frame(
                    step=step,
                    pending_tasks=pending,
                    frame_time=self.current_time,
                )
            )

        return self._build_results(
            total_generated=total_generated,
            total_distance=total_distance,
            total_time_hours=total_time_hours,
            total_cost=total_cost,
            total_score=total_score,
        )

    def run_simulation(self, num_steps: int = 100, tasks_per_step: int = 3) -> Dict[str, float]:
        totals = {"distance": 0.0, "time_hours": 0.0, "score": 0.0, "cost": 0.0}
        total_generated = 0
        start_time = self.current_time

        self._dbg(
            "H1",
            "simulator.py:run_simulation",
            "event_driven_simulation_start",
            {
                "num_steps": num_steps,
                "tasks_per_step": tasks_per_step,
                "seed": self.random_seed,
                "dispatcher": type(self.dispatcher).__name__,
            },
        )

        for step in range(num_steps):
            step_time = start_time + timedelta(hours=step)
            step_end_time = start_time + timedelta(hours=step + 1)
            self.current_time = step_time
            self._add_delta(totals, self._advance_due_events())

            new_tasks = [self.generate_random_task(self.current_time) for _ in range(tasks_per_step)]
            self.all_tasks.extend(new_tasks)
            total_generated += len(new_tasks)
            task_map = {t.id: t for t in self.all_tasks}

            state = SimulationState(
                current_time=self.current_time,
                tasks=self.all_tasks,
                vehicles=self.vehicles,
                charging_stations=self.charging_stations,
                network=self.network,
                extra_info={"step": step},
            )
            actions = self.dispatcher.generate_actions(state)

            self._dbg(
                "H2",
                "simulator.py:run_simulation",
                "event_driven_step_actions",
                {
                    "step": step,
                    "generated_tasks": [t.id for t in new_tasks],
                    "generated_cargo_types": {t.id: t.cargo_type for t in new_tasks},
                    "pending_task_ids": [t.id for t in state.pending_tasks],
                    "actions": [
                        {"type": a.type, "vehicle_id": a.vehicle_id, "task_id": a.task_id, "note": a.note}
                        for a in actions
                    ],
                },
            )

            for action in actions:
                self._add_delta(totals, self._execute_action(action, task_map))

            self._append_frame(step)

            while True:
                next_event_time = self._next_event_time(step_end_time)
                if next_event_time is None:
                    break
                self.current_time = next_event_time
                self._add_delta(totals, self._advance_due_events())
                self._append_frame(step)

            self.current_time = step_end_time
            self._add_delta(totals, self._advance_due_events())
            self._append_frame(step)

        return self._build_results(
            total_generated=total_generated,
            total_distance=totals["distance"],
            total_time_hours=totals["time_hours"],
            total_cost=totals["cost"],
            total_score=totals["score"],
        )

    def get_frames(self) -> List[SimulationFrame]:
        """可视化模块读取全量轨迹帧。"""
        return list(self.frames)

    def get_latest_frame(self) -> Optional[SimulationFrame]:
        """可视化模块读取最新一帧状态。"""
        if not self.frames:
            return None
        return self.frames[-1]
