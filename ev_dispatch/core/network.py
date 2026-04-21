import numpy as np
import networkx as nx
import heapq
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

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

    def __init__(self, width: float = 20.0, height: float = 20.0, num_nodes: int = 25):
        self.width = width
        self.height = height
        self.graph = nx.Graph()
        self.nodes = []
        self.congestion_model = CongestionModel()

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
                if dist < width / (2 * grid_size):
                    # Edge attributes:
                    # - length_km: distance in the same units as coordinates (treated as km)
                    # - speed_limit_kmph: speed limit per road segment
                    # - peak_intensity: how much this edge is affected by rush hours
                    speed_limit = float(np.random.uniform(30.0, 60.0))
                    peak_intensity = float(np.random.uniform(0.2, 1.0))
                    self.graph.add_edge(
                        node_i,
                        node_j,
                        length_km=float(dist),
                        speed_limit_kmph=speed_limit,
                        peak_intensity=peak_intensity,
                        # Back-compat: keep weight as distance for distance-based shortest path
                        weight=float(dist),
                    )

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

    def shortest_travel_time_hours(
        self,
        start: Location,
        end: Location,
        start_time: datetime,
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
            return dist / 40.0  # fallback 40 km/h

        def time_weight(u: str, v: str, attrs: dict) -> float:
            length_km = float(attrs.get("length_km", 0.0))
            speed_limit = float(attrs.get("speed_limit_kmph", 40.0))
            peak_intensity = float(attrs.get("peak_intensity", 0.5))
            m = self.congestion_model.multiplier(start_time, peak_intensity)
            speed = max(1e-6, speed_limit * m)
            return length_km / speed  # hours

        try:
            return float(nx.dijkstra_path_length(self.graph, start_node, end_node, weight=time_weight))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            dist = start.distance_to(end)
            return dist / 40.0
    
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
