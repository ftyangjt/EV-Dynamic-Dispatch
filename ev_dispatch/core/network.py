import numpy as np
import networkx as nx
import heapq
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ev_dispatch.core.location import Location


@dataclass(frozen=True)
class CongestionModel:
    """
    Time-varying congestion multiplier model.

    Returns a multiplier in (min_multiplier, 1.0], where lower means slower traffic.
    """

    morning_peak: Tuple[int, int] = (7, 10)  # inclusive start, exclusive end
    evening_peak: Tuple[int, int] = (17, 20)
    min_multiplier: float = 0.35

    def multiplier(self, t: datetime, edge_peak_intensity: float) -> float:
        hour = t.hour + (t.minute / 60.0)

        def peak_strength(h: float, start: int, end: int) -> float:
            if h < start or h >= end:
                return 0.0
            # Smooth bump: 0 -> 1 -> 0 over [start, end)
            x = (h - start) / max(1e-9, (end - start))
            return float(np.sin(np.pi * x))

        base = (
            peak_strength(hour, self.morning_peak[0], self.morning_peak[1])
            + peak_strength(hour, self.evening_peak[0], self.evening_peak[1])
        )

        # edge_peak_intensity \in [0,1], higher means more sensitive to peaks
        slowdown = min(1.0, base * float(np.clip(edge_peak_intensity, 0.0, 1.0)))
        m = 1.0 - slowdown * (1.0 - self.min_multiplier)
        return float(np.clip(m, self.min_multiplier, 1.0))


class RoadNetwork:
    """City road network represented by an undirected graph."""

    ROAD_TYPE_PROFILES: Dict[str, Dict[str, float]] = {
        "arterial": {
            "speed_min": 45.0,
            "speed_max": 70.0,
            "lane_min": 3,
            "lane_max": 4,
            "surface_min": 0.80,
            "surface_max": 0.98,
            "toll_per_km": 0.12,
            "risk_min": 0.03,
            "risk_max": 0.12,
        },
        "collector": {
            "speed_min": 35.0,
            "speed_max": 55.0,
            "lane_min": 2,
            "lane_max": 3,
            "surface_min": 0.65,
            "surface_max": 0.92,
            "toll_per_km": 0.04,
            "risk_min": 0.08,
            "risk_max": 0.22,
        },
        "local": {
            "speed_min": 20.0,
            "speed_max": 40.0,
            "lane_min": 1,
            "lane_max": 2,
            "surface_min": 0.45,
            "surface_max": 0.82,
            "toll_per_km": 0.00,
            "risk_min": 0.16,
            "risk_max": 0.38,
        },
    }

    def __init__(
        self,
        width: float = 20.0,
        height: float = 20.0,
        num_nodes: int = 25,
        random_seed: Optional[int] = None,
        rng: Optional[np.random.Generator] = None,
    ):
        self.width = width
        self.height = height
        self.graph = nx.Graph()
        self.nodes = []
        self.congestion_model = CongestionModel()
        self.random_seed = random_seed
        self.rng = rng or np.random.default_rng(random_seed)

        grid_size = int(np.sqrt(num_nodes))
        for i in range(grid_size):
            for j in range(grid_size):
                x = i * (width / grid_size) + width / (2 * grid_size)
                y = j * (height / grid_size) + height / (2 * grid_size)
                node_id = f"node_{i}_{j}"
                self.nodes.append((node_id, Location(x, y, node_id)))
                self.graph.add_node(node_id)

        for i in range(len(self.nodes)):
            for j in range(i + 1, len(self.nodes)):
                node_i, loc_i = self.nodes[i]
                node_j, loc_j = self.nodes[j]
                dist = loc_i.distance_to(loc_j)
                max_grid_dist = max(width, height) / grid_size
                if dist <= max_grid_dist *1.1:
                    road_type = self._sample_road_type()
                    road_attrs = self._build_road_attributes(road_type, dist)
                    self.graph.add_edge(
                        node_i,
                        node_j,
                        **road_attrs,
                        # Back-compat: keep weight as distance for distance-based shortest path
                        weight=float(dist),
                    )

    def _sample_road_type(self) -> str:
        return str(self.rng.choice(["arterial", "collector", "local"], p=[0.25, 0.45, 0.30]))

    def _build_road_attributes(self, road_type: str, length_km: float) -> Dict[str, float]:
        profile = self.ROAD_TYPE_PROFILES[road_type]
        lane_count = int(self.rng.integers(int(profile["lane_min"]), int(profile["lane_max"]) + 1))
        surface_quality = float(self.rng.uniform(profile["surface_min"], profile["surface_max"]))
        slope_grade = float(self.rng.uniform(-0.04, 0.06))
        intersection_density = float(self.rng.uniform(0.15, 0.90))
        truck_restriction = bool(self.rng.random() < (0.08 if road_type == "local" else 0.02))
        peak_intensity = float(
            np.clip(
                self.rng.uniform(0.2, 1.0) * (1.15 if road_type == "arterial" else 0.9),
                0.05,
                1.0,
            )
        )

        return {
            "length_km": float(length_km),
            "road_type": road_type,
            "lane_count": lane_count,
            "speed_limit_kmph": float(self.rng.uniform(profile["speed_min"], profile["speed_max"])),
            "surface_quality": surface_quality,
            "slope_grade": slope_grade,
            "intersection_density": intersection_density,
            "peak_intensity": peak_intensity,
            "toll_per_km": float(profile["toll_per_km"]),
            "accident_risk": float(self.rng.uniform(profile["risk_min"], profile["risk_max"])),
            "truck_restriction": truck_restriction,
        }

    def effective_edge_speed_kmph(
        self,
        attrs: dict,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
    ) -> float:
        speed_limit = float(attrs.get("speed_limit_kmph", 40.0))
        peak_intensity = float(attrs.get("peak_intensity", 0.5))
        lane_count = float(attrs.get("lane_count", 2))
        surface_quality = float(attrs.get("surface_quality", 0.75))
        intersection_density = float(attrs.get("intersection_density", 0.4))

        congestion = self.congestion_model.multiplier(start_time, peak_intensity)
        lane_factor = 0.82 + min(4.0, max(1.0, lane_count)) * 0.06
        surface_factor = 0.72 + 0.28 * max(0.0, min(1.0, surface_quality))
        intersection_factor = 1.0 - 0.22 * max(0.0, min(1.0, intersection_density))

        speed = speed_limit * congestion * lane_factor * surface_factor * intersection_factor
        if vehicle_max_speed_kmh is not None:
            speed = min(speed, float(vehicle_max_speed_kmh))
        return max(1e-6, float(speed))

    def edge_energy_factor(self, attrs: dict) -> float:
        surface_quality = float(attrs.get("surface_quality", 0.75))
        slope_grade = float(attrs.get("slope_grade", 0.0))
        intersection_density = float(attrs.get("intersection_density", 0.4))
        accident_risk = float(attrs.get("accident_risk", 0.1))

        surface_factor = 1.0 + max(0.0, 0.8 - surface_quality) * 0.35
        slope_factor = 1.0 + max(0.0, slope_grade) * 3.0 + max(0.0, -slope_grade) * 0.5
        stop_go_factor = 1.0 + max(0.0, min(1.0, intersection_density)) * 0.12
        risk_factor = 1.0 + max(0.0, min(1.0, accident_risk)) * 0.05
        return float(surface_factor * slope_factor * stop_go_factor * risk_factor)

    def dijkstra(self, start: Location, end: Location) -> float:
        """
        Dijkstra算法: 计算两点间的最短距离
        
        Args:
            start: 起始位置
            end: 目标位置
            
        Returns:
            最短距离(km)
        """
        start_node = self._find_nearest_node(start)
        end_node = self._find_nearest_node(end)
        
        if start_node is None or end_node is None:
            return start.distance_to(end)
        
        # 使用NetworkX内置的Dijkstra实现
        try:
            shortest_path_distance = nx.dijkstra_path_length(
                self.graph, start_node, end_node, weight="length_km"
            )
            return shortest_path_distance
        except nx.NetworkXNoPath:
            # 如果没有路径，返回直线距离
            return start.distance_to(end)

    def a_star(self, start: Location, end: Location) -> float:
        """
        A*算法: 计算两点间的最短距离(使用欧几里得距离作为启发函数)
        
        Args:
            start: 起始位置
            end: 目标位置
            
        Returns:
            最短距离(km)
        """
        start_node = self._find_nearest_node(start)
        end_node = self._find_nearest_node(end)
        
        if start_node is None or end_node is None:
            return start.distance_to(end)
        
        # 定义启发函数(欧几里得距离)
        def heuristic(node):
            _, loc = self._get_node_location(node)
            return loc.distance_to(end)
        
        # 使用NetworkX内置的A*实现
        try:
            shortest_path_distance = nx.astar_path_length(
                self.graph, start_node, end_node, heuristic=heuristic, weight="length_km"
            )
            return shortest_path_distance
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            # 如果没有路径，返回直线距离
            return start.distance_to(end)

    def shortest_distance(self, start: Location, end: Location, method: str = "dijkstra") -> float:
        """统一入口：计算最短路程长度（km）。"""
        if method == "a_star":
            return self.a_star(start, end)
        return self.dijkstra(start, end)

    def shortest_path_locations(
        self,
        start: Location,
        end: Location,
        method: str = "dijkstra",
    ) -> List[Location]:
        """Return the shortest path as road-node locations for visualization."""
        start_node = self._find_nearest_node(start)
        end_node = self._find_nearest_node(end)

        if start_node is None or end_node is None:
            return [start, end]

        try:
            if method == "a_star":
                def heuristic(node):
                    _, loc = self._get_node_location(node)
                    return loc.distance_to(end)

                node_path = nx.astar_path(
                    self.graph,
                    start_node,
                    end_node,
                    heuristic=heuristic,
                    weight="length_km",
                )
            else:
                node_path = nx.dijkstra_path(
                    self.graph,
                    start_node,
                    end_node,
                    weight="length_km",
                )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return [start, end]

        locations: List[Location] = []
        for node_id in node_path:
            node_info = self._get_node_location(node_id)
            if node_info is not None:
                locations.append(node_info[1])

        return locations or [start, end]

    def timed_path_locations(
        self,
        start: Location,
        end: Location,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
        method: str = "dijkstra",
    ) -> List[Tuple[Location, float]]:
        """Return path locations with cumulative travel hours at each node."""
        start_node = self._find_nearest_node(start)
        end_node = self._find_nearest_node(end)

        if start_node is None or end_node is None:
            distance = start.distance_to(end)
            speed = min(40.0, float(vehicle_max_speed_kmh or 40.0))
            return [(start, 0.0), (end, distance / max(1e-6, speed))]

        def time_weight(u: str, v: str, attrs: dict) -> float:
            length_km = float(attrs.get("length_km", 0.0))
            speed = self.effective_edge_speed_kmph(
                attrs,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
            )
            return length_km / max(1e-6, speed)

        try:
            if method == "a_star":
                def heuristic(node):
                    _, loc = self._get_node_location(node)
                    fallback_speed = min(40.0, float(vehicle_max_speed_kmh or 40.0))
                    return loc.distance_to(end) / max(1e-6, fallback_speed)

                node_path = nx.astar_path(
                    self.graph,
                    start_node,
                    end_node,
                    heuristic=heuristic,
                    weight=time_weight,
                )
            else:
                node_path = nx.dijkstra_path(self.graph, start_node, end_node, weight=time_weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            distance = start.distance_to(end)
            speed = min(40.0, float(vehicle_max_speed_kmh or 40.0))
            return [(start, 0.0), (end, distance / max(1e-6, speed))]

        timed_path: List[Tuple[Location, float]] = []
        cumulative_hours = 0.0
        for index, node_id in enumerate(node_path):
            node_info = self._get_node_location(node_id)
            if node_info is None:
                continue
            if index == 0:
                timed_path.append((node_info[1], cumulative_hours))
                continue

            prev_node = node_path[index - 1]
            attrs = self.graph[prev_node][node_id]
            length = float(attrs.get("length_km", 0.0))
            speed = self.effective_edge_speed_kmph(
                attrs,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
            )
            cumulative_hours += length / max(1e-6, speed)
            timed_path.append((node_info[1], cumulative_hours))

        return timed_path or [(start, 0.0), (end, 0.0)]

    def shortest_travel_time_hours(
        self,
        start: Location,
        end: Location,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
    ) -> float:
        """
        计算在给定出发时刻的最短行驶时间（小时）。

        这里采用“时刻切片”的近似：在 start_time 时刻，把每条边的拥堵倍率固定下来，
        然后做一次按时间权重的 Dijkstra。
        """
        start_node = self._find_nearest_node(start)
        end_node = self._find_nearest_node(end)

        if start_node is None or end_node is None:
            dist = start.distance_to(end)
            fallback_speed = 40.0
            if vehicle_max_speed_kmh is not None:
                fallback_speed = min(fallback_speed, float(vehicle_max_speed_kmh))
            return dist / max(1e-6, fallback_speed)

        def time_weight(u: str, v: str, attrs: dict) -> float:
            length_km = float(attrs.get("length_km", 0.0))
            speed = self.effective_edge_speed_kmph(
                attrs,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
            )
            return length_km / speed  # hours

        try:
            return float(nx.dijkstra_path_length(self.graph, start_node, end_node, weight=time_weight))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            dist = start.distance_to(end)
            fallback_speed = 40.0
            if vehicle_max_speed_kmh is not None:
                fallback_speed = min(fallback_speed, float(vehicle_max_speed_kmh))
            return dist / max(1e-6, fallback_speed)

    def path_road_metrics(
        self,
        start: Location,
        end: Location,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
        method: str = "dijkstra",
    ) -> Dict[str, float]:
        """Aggregate road attributes along the shortest path."""
        start_node = self._find_nearest_node(start)
        end_node = self._find_nearest_node(end)
        if start_node is None or end_node is None:
            distance = start.distance_to(end)
            return {
                "distance_km": distance,
                "time_hours": distance / max(1e-6, min(40.0, float(vehicle_max_speed_kmh or 40.0))),
                "avg_speed_kmph": min(40.0, float(vehicle_max_speed_kmh or 40.0)),
                "energy_factor": 1.0,
                "toll_cost": 0.0,
                "risk_cost": 0.0,
                "restricted_distance_km": 0.0,
            }

        try:
            if method == "a_star":
                def heuristic(node):
                    _, loc = self._get_node_location(node)
                    return loc.distance_to(end)

                node_path = nx.astar_path(
                    self.graph,
                    start_node,
                    end_node,
                    heuristic=heuristic,
                    weight="length_km",
                )
            else:
                node_path = nx.dijkstra_path(self.graph, start_node, end_node, weight="length_km")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            distance = start.distance_to(end)
            return {
                "distance_km": distance,
                "time_hours": distance / max(1e-6, min(40.0, float(vehicle_max_speed_kmh or 40.0))),
                "avg_speed_kmph": min(40.0, float(vehicle_max_speed_kmh or 40.0)),
                "energy_factor": 1.0,
                "toll_cost": 0.0,
                "risk_cost": 0.0,
                "restricted_distance_km": 0.0,
            }

        total_distance = 0.0
        total_time = 0.0
        weighted_energy_factor = 0.0
        toll_cost = 0.0
        risk_cost = 0.0
        restricted_distance = 0.0

        for u, v in zip(node_path, node_path[1:]):
            attrs = self.graph[u][v]
            length = float(attrs.get("length_km", 0.0))
            speed = self.effective_edge_speed_kmph(
                attrs,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
            )
            total_distance += length
            total_time += length / max(1e-6, speed)
            weighted_energy_factor += length * self.edge_energy_factor(attrs)
            toll_cost += length * float(attrs.get("toll_per_km", 0.0))
            risk_cost += length * float(attrs.get("accident_risk", 0.0)) * 0.25
            if bool(attrs.get("truck_restriction", False)):
                restricted_distance += length

        avg_speed = total_distance / max(1e-6, total_time)
        return {
            "distance_km": float(total_distance),
            "time_hours": float(total_time),
            "avg_speed_kmph": float(avg_speed),
            "energy_factor": float(weighted_energy_factor / max(1e-9, total_distance)),
            "toll_cost": float(toll_cost),
            "risk_cost": float(risk_cost),
            "restricted_distance_km": float(restricted_distance),
        }
    
    def _find_nearest_node(self, location: Location) -> Optional[str]:
        """
        找到距离给定位置最近的网络节点
        
        Args:
            location: 查询位置
            
        Returns:
            最近的节点ID或None
        """
        if not self.nodes:
            return None
        
        nearest_node = None
        min_distance = float('inf')
        
        for node_id, node_location in self.nodes:
            dist = location.distance_to(node_location)
            if dist < min_distance:
                min_distance = dist
                nearest_node = node_id
        
        return nearest_node
    
    def _get_node_location(self, node_id: str) -> Optional[Tuple[str, Location]]:
        """
        根据节点ID获取节点的位置信息
        
        Args:
            node_id: 节点ID
            
        Returns:
            (node_id, Location) 元组或None
        """
        for nid, loc in self.nodes:
            if nid == node_id:
                return (nid, loc)
        return None
