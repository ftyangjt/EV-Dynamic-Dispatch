from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import json
import os

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.core.charging import ChargingStation
from ev_dispatch.core.energy import EnergyManager
from ev_dispatch.core.interfaces import Action, SimulationFrame, SimulationState
from ev_dispatch.core.location import Location
from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.task import Task, CargoType
from ev_dispatch.core.vehicle import VehicleStatus
from ev_dispatch.core.vehicle import Vehicle
from ev_dispatch.scenarios.default import CargoConfig


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

        self.current_time = datetime.now()
        self.completed_tasks: List[Task] = []
        self.failed_tasks: List[Task] = []
        self._completed_task_ids = set()
        self._failed_task_ids = set()
        self.frames: List[SimulationFrame] = []

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
        """
        task_id = f"task_{self._task_seq}"
        self._task_seq += 1
        origin = Location(
            float(self.rng.uniform(0, self.network.width)),
            float(self.rng.uniform(0, self.network.height)),
            f"origin_{task_id}",
        )
        destination = Location(
            float(self.rng.uniform(0, self.network.width)),
            float(self.rng.uniform(0, self.network.height)),
            f"dest_{task_id}",
        )
        weight = float(self.rng.uniform(10, 500))
        volume = float(self.rng.uniform(0.1, 3.0))  # m^3，随机体积
        deadline = current_time + timedelta(hours=float(self.rng.uniform(1, 8)))
        
        # 按比例生成货物类型
        cargo_distribution = self.cargo_config.get_cargo_type_distribution()
        cargo_type_str = self.rng.choice(
            list(cargo_distribution.keys()),
            p=list(cargo_distribution.values())
        )
        cargo_type = str(cargo_type_str)

        # #region agent log
        self._dbg(
            "H1",
            "simulator.py:generate_random_task",
            "generated_task",
            {
                "task_id": task_id,
                "cargo_type": cargo_type,
                "weight": weight,
                "volume": volume,
                "created_time": current_time.isoformat(),
                "deadline": deadline.isoformat(),
                "seed": self.random_seed,
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

    def _find_station(self, station_id: str) -> Optional[ChargingStation]:
        return next((s for s in self.charging_stations if s.id == station_id), None)

    def _advance_charging_stations(self) -> None:
        """
        在当前仿真时刻推进充电站状态：
        - 结束已充满的车辆
        - 按 FIFO 从 waiting_queue 拉起新充电（受 num_chargers 限制）
        """
        now = self.current_time

        # 1) 完成充电
        for station in self.charging_stations:
            done_ids: List[str] = []
            for vid, rec in list(station.charging_vehicles.items()):
                end_time = rec.start_time + timedelta(minutes=float(rec.duration_minutes))
                if end_time <= now:
                    done_ids.append(vid)

            for vid in done_ids:
                station.complete_charging(vid, end_time=now)
                v = next((x for x in self.vehicles if x.id == vid), None)
                if v is not None and v.status == VehicleStatus.CHARGING:
                    v.end_charging()

        # 2) 拉起排队车辆开始充电（FIFO）
        for station in self.charging_stations:
            while len(station.charging_vehicles) < station.num_chargers:
                vid = station.dequeue()
                if vid is None:
                    break
                v = next((x for x in self.vehicles if x.id == vid), None)
                if v is None:
                    continue
                if v.status in (VehicleStatus.MAINTENANCE, VehicleStatus.EN_ROUTE, VehicleStatus.OCCUPIED):
                    continue

                # 充电时长：受“车充电速度”和“站功率上限”共同限制
                effective_power = min(float(v.charging_speed_kwh_per_hour), float(station.charging_power))
                energy_needed = max(0.0, float(v.battery_capacity - v.current_battery))
                duration_minutes = 0.0 if effective_power <= 1e-9 else (energy_needed / effective_power) * 60.0

                # 站内记录 + 车辆状态
                station.start_charging(vehicle_id=vid, target_energy=energy_needed, start_time=now)
                # 覆盖站内记录的 duration（因为 station.start_charging 默认只用 station.charging_power）
                if vid in station.charging_vehicles:
                    station.charging_vehicles[vid].duration_minutes = float(duration_minutes)

                v.is_charging = True
                v.charging_station_id = station.id
                v.charging_start_time = now
                v.charging_duration = float(duration_minutes)
                v.target_battery = v.battery_capacity
                v.status = VehicleStatus.CHARGING

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
            self._advance_charging_stations()

            depart_time = self.current_time
            dist_to_station = self.network.shortest_distance(vehicle.position, station.position, method="dijkstra")
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
                speed_kmh=vehicle.current_speed_kmh,
                efficiency=vehicle.efficiency,
            )

            distance_delta += dist_to_station
            time_delta_hours += t_to_station

            if energy_to_station > vehicle.current_battery:
                score_delta -= 20  # 电量不足以到达充电站，惩罚
                return {"distance": distance_delta, "score": score_delta, "time_hours": time_delta_hours, "cost": 0.0}

            vehicle.current_battery -= energy_to_station
            vehicle.position = station.position

            station.enqueue(vehicle.id)
            self._advance_charging_stations()

            return {"distance": distance_delta, "score": score_delta, "time_hours": time_delta_hours, "cost": 0.0}

        if action.type != "assign_task" or action.task_id is None:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}

        task = task_map.get(action.task_id)
        if task is None or task.completed or task.failed:
            return {"distance": 0.0, "score": 0.0, "time_hours": 0.0}

        # 路网距离与行驶时间（拥堵随当前时刻变化）
        depart_time = self.current_time
        dist_to_pickup = self.network.shortest_distance(vehicle.position, task.origin, method="dijkstra")
        t_to_pickup = self.network.shortest_travel_time_hours(
            vehicle.position,
            task.origin,
            start_time=depart_time,
            vehicle_max_speed_kmh=vehicle.max_speed_kmh,
        )

        # 近似：到达取货点后的时间 = depart_time + t_to_pickup（拥堵按到达时刻重新取一片）
        arrive_pickup_time = depart_time + timedelta(hours=t_to_pickup)
        dist_delivery = self.network.shortest_distance(task.origin, task.destination, method="dijkstra")
        t_delivery = self.network.shortest_travel_time_hours(
            task.origin,
            task.destination,
            start_time=arrive_pickup_time,
            vehicle_max_speed_kmh=vehicle.max_speed_kmh,
        )

        dist = dist_to_pickup + dist_delivery
        time_delta_hours = t_to_pickup + t_delivery
        # Calculate energy considering vehicle's current speed
        energy_used = EnergyManager.calculate_consumption(
            distance=dist,
            load=task.weight,
            speed_kmh=vehicle.current_speed_kmh,
            efficiency=vehicle.efficiency
        )
        distance_delta += dist

        # 运输费用模型（参与评分体系）
        # - 路程成本：按公里计费
        # - 重量成本：按 kg*km 计费（越重越贵）
        # - 车型成本：普通车最便宜（这里用 Standard 作为“普通车”）
        # - 额外建议：能耗成本（按 kWh 折算成本），更贴近新能源车队
        base_cost_per_km = 1.0
        weight_cost_per_kg_km = 0.002
        energy_cost_per_kwh = 0.8
        vehicle_cost_multiplier = {
            "compact": 1.10,
            "standard": 1.00,  # 普通车最便宜
            "large": 1.25,
        }
        vt = (vehicle.vehicle_type.name or "").strip().lower()
        vt_key = "standard"
        if "compact" in vt:
            vt_key = "compact"
        elif "large" in vt:
            vt_key = "large"
        elif "standard" in vt:
            vt_key = "standard"
        mult = float(vehicle_cost_multiplier.get(vt_key, 1.10))

        distance_cost = dist * base_cost_per_km
        weight_cost = dist * task.weight * weight_cost_per_kg_km
        energy_cost = energy_used * energy_cost_per_kwh
        cost_delta = (distance_cost + weight_cost + energy_cost) * mult

        # 检查：车辆是否支持该货物类型
        cargo_type = getattr(task.cargo_type, "value", task.cargo_type)
        if cargo_type not in vehicle.supported_cargo_types:
            task.failed = True
            task.failure_reason = "cargo_type_mismatch"
            if task.id not in self._failed_task_ids and task.id not in self._completed_task_ids:
                self.failed_tasks.append(task)
                self._failed_task_ids.add(task.id)
            score_delta -= 50  # 货物类型不匹配，任务失败
            return {"distance": distance_delta, "score": score_delta, "time_hours": time_delta_hours, "cost": cost_delta}
        
        # 检查：车辆是否有足够的电量和容量
        if energy_used <= vehicle.current_battery and vehicle.can_carry_task(task.weight, task.volume, cargo_type):
            vehicle.current_battery -= energy_used
            vehicle.position = task.destination
            task.completed = True
            task.failed = False
            task.failure_reason = ""
            if task.id in self._failed_task_ids:
                self._failed_task_ids.remove(task.id)
                self.failed_tasks = [t for t in self.failed_tasks if t.id != task.id]
            if task.id not in self._completed_task_ids:
                self.completed_tasks.append(task)
                self._completed_task_ids.add(task.id)
            # 简单收益：路程越短越好 + 时间越短越好
            score_delta += 100 - (dist * 0.1) - (time_delta_hours * 2.0) - cost_delta
        else:
            task.failed = True
            task.failure_reason = "capacity_or_energy_insufficient"
            if task.id not in self._failed_task_ids and task.id not in self._completed_task_ids:
                self.failed_tasks.append(task)
                self._failed_task_ids.add(task.id)
            score_delta -= 50

        return {"distance": distance_delta, "score": score_delta, "time_hours": time_delta_hours, "cost": cost_delta}

    def run_simulation(self, num_steps: int = 100, tasks_per_step: int = 3) -> Dict[str, float]:
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
            self._advance_charging_stations()

            new_tasks = [self.generate_random_task(self.current_time) for _ in range(tasks_per_step)]
            total_generated += len(new_tasks)
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

            # #region agent log
            self._dbg(
                "H2",
                "simulator.py:run_simulation",
                "step_actions",
                {
                    "step": step,
                    "generated_tasks": [t.id for t in new_tasks],
                    "generated_cargo_types": {t.id: t.cargo_type for t in new_tasks},
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

            self.current_time += timedelta(hours=1)

            pending = [t for t in new_tasks if not t.completed and t not in self.failed_tasks]
            self.frames.append(self._build_frame(step=step, pending_tasks=pending))

        return {
            # 用“任务唯一ID”计数，避免同一任务被重复计入失败/完成
            "completed": len(self._completed_task_ids),
            "failed": len(self._failed_task_ids),
            "generated": total_generated,
            "total_distance": total_distance,
            "total_time_hours": total_time_hours,
            "total_cost": total_cost,
            "total_score": total_score,
            "avg_score_per_task": total_score / (max(1, len(self._completed_task_ids))),
        }

    def get_frames(self) -> List[SimulationFrame]:
        """可视化模块读取全量轨迹帧。"""
        return list(self.frames)

    def get_latest_frame(self) -> Optional[SimulationFrame]:
        """可视化模块读取最新一帧状态。"""
        if not self.frames:
            return None
        return self.frames[-1]
