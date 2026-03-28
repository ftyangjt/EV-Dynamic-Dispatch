import numpy as np
import networkx as nx
import heapq
from typing import Optional, Tuple

from ev_dispatch.core.location import Location


class RoadNetwork:
    """City road network represented by an undirected graph."""

    def __init__(self, width: float = 20.0, height: float = 20.0, num_nodes: int = 25):
        self.width = width
        self.height = height
        self.graph = nx.Graph()
        self.nodes = []

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
                    self.graph.add_edge(node_i, node_j, weight=dist)

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
                self.graph, start_node, end_node, weight='weight'
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
                self.graph, start_node, end_node, heuristic=heuristic, weight='weight'
            )
            return shortest_path_distance
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            # 如果没有路径，返回直线距离
            return start.distance_to(end)
    
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
