from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import networkx as nx
import numpy as np

from ev_dispatch.core.location import Location


@dataclass(frozen=True)
class CongestionModel:
    """
    Time-varying congestion multiplier model.

    Returns a multiplier in (min_multiplier, 1.0], where lower means slower traffic.
    """

    morning_peak: Tuple[int, int] = (7, 10)
    evening_peak: Tuple[int, int] = (17, 20)
    min_multiplier: float = 0.35

    def multiplier(self, t: datetime, edge_peak_intensity: float) -> float:
        hour = t.hour + (t.minute / 60.0)

        def peak_strength(h: float, start: int, end: int) -> float:
            if h < start or h >= end:
                return 0.0
            x = (h - start) / max(1e-9, (end - start))
            return float(np.sin(np.pi * x))

        base = (
            peak_strength(hour, self.morning_peak[0], self.morning_peak[1])
            + peak_strength(hour, self.evening_peak[0], self.evening_peak[1])
        )
        slowdown = min(1.0, base * float(np.clip(edge_peak_intensity, 0.0, 1.0)))
        multiplier = 1.0 - slowdown * (1.0 - self.min_multiplier)
        return float(np.clip(multiplier, self.min_multiplier, 1.0))


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
        },
        "collector": {
            "speed_min": 35.0,
            "speed_max": 55.0,
            "lane_min": 2,
            "lane_max": 3,
            "surface_min": 0.65,
            "surface_max": 0.92,
        },
        "local": {
            "speed_min": 20.0,
            "speed_max": 40.0,
            "lane_min": 1,
            "lane_max": 2,
            "surface_min": 0.45,
            "surface_max": 0.82,
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
        self.nodes: List[Tuple[str, Location]] = []
        self._node_location_map: Dict[str, Location] = {}
        self._node_ids: List[str] = []
        self._node_xy: np.ndarray = np.empty((0, 2), dtype=float)

        self._nearest_node_cache: Dict[Tuple[float, float, str], Optional[str]] = {}
        self._distance_cache: Dict[Tuple[str, str, str], float] = {}
        self._distance_path_cache: Dict[Tuple[str, str, str], List[str]] = {}
        self._time_path_cache: Dict[Tuple[str, str, str, str, Optional[float]], List[str]] = {}
        self._time_length_cache: Dict[Tuple[str, str, str, Optional[float]], float] = {}
        self._road_metrics_cache: Dict[Tuple[str, str, str, str, Optional[float]], Dict[str, float]] = {}

        self.congestion_model = CongestionModel()
        self.random_seed = random_seed
        self.rng = rng or np.random.default_rng(random_seed)

        grid_size = int(np.sqrt(num_nodes))
        for i in range(grid_size):
            for j in range(grid_size):
                x = i * (width / grid_size) + width / (2 * grid_size)
                y = j * (height / grid_size) + height / (2 * grid_size)
                node_id = f"node_{i}_{j}"
                location = Location(x, y, node_id)
                self.nodes.append((node_id, location))
                self._node_location_map[node_id] = location
                self.graph.add_node(node_id)

        self._node_ids = [node_id for node_id, _loc in self.nodes]
        self._node_xy = np.array([(loc.x, loc.y) for _node_id, loc in self.nodes], dtype=float)

        for i in range(len(self.nodes)):
            for j in range(i + 1, len(self.nodes)):
                node_i, loc_i = self.nodes[i]
                node_j, loc_j = self.nodes[j]
                dist = loc_i.distance_to(loc_j)
                max_grid_dist = max(width, height) / grid_size
                if dist <= max_grid_dist * 1.1:
                    road_type = self._sample_road_type()
                    road_attrs = self._build_road_attributes(road_type, dist)
                    self.graph.add_edge(
                        node_i,
                        node_j,
                        **road_attrs,
                        weight=float(dist),
                    )

    def _sample_road_type(self) -> str:
        return str(self.rng.choice(["arterial", "collector", "local"], p=[0.25, 0.45, 0.30]))

    def _build_road_attributes(self, road_type: str, length_km: float) -> Dict[str, float]:
        profile = self.ROAD_TYPE_PROFILES[road_type]
        lane_count = int(self.rng.integers(int(profile["lane_min"]), int(profile["lane_max"]) + 1))
        surface_quality = float(self.rng.uniform(profile["surface_min"], profile["surface_max"]))
        intersection_density = float(self.rng.uniform(0.15, 0.90))
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
            "intersection_density": intersection_density,
            "peak_intensity": peak_intensity,
        }

    @staticmethod
    def _location_cache_key(location: Location) -> Tuple[float, float, str]:
        return (round(float(location.x), 9), round(float(location.y), 9), str(location.name))

    @staticmethod
    def _time_cache_key(start_time: datetime) -> str:
        return start_time.isoformat(timespec="minutes")

    @staticmethod
    def _speed_key(vehicle_max_speed_kmh: Optional[float]) -> Optional[float]:
        return None if vehicle_max_speed_kmh is None else float(vehicle_max_speed_kmh)

    @staticmethod
    def _fallback_speed(vehicle_max_speed_kmh: Optional[float] = None) -> float:
        speed = 40.0
        if vehicle_max_speed_kmh is not None:
            speed = min(speed, float(vehicle_max_speed_kmh))
        return max(1e-6, speed)

    def _nodes_for_locations(self, start: Location, end: Location) -> Tuple[Optional[str], Optional[str]]:
        return self._find_nearest_node(start), self._find_nearest_node(end)

    def _node_location(self, node_id: str) -> Optional[Location]:
        return self._node_location_map.get(node_id)

    def _distance_path_node_ids(
        self,
        start_node: str,
        end_node: str,
        method: str = "dijkstra",
        heuristic_target: Optional[Location] = None,
    ) -> List[str]:
        cache_key = (start_node, end_node, method)
        if cache_key in self._distance_path_cache:
            return list(self._distance_path_cache[cache_key])

        if method == "a_star":
            target = heuristic_target or self._node_location(end_node)

            def heuristic(node):
                loc = self._node_location(node)
                if loc is None or target is None:
                    return 0.0
                return loc.distance_to(target)

            node_path = nx.astar_path(
                self.graph,
                start_node,
                end_node,
                heuristic=heuristic,
                weight="length_km",
            )
        else:
            node_path = nx.dijkstra_path(self.graph, start_node, end_node, weight="length_km")

        self._distance_path_cache[cache_key] = list(node_path)
        self._distance_path_cache[(end_node, start_node, method)] = list(reversed(node_path))
        return list(node_path)

    def _distance_path_length(self, start_node: str, end_node: str, method: str = "dijkstra") -> float:
        cache_key = (start_node, end_node, method)
        if cache_key in self._distance_cache:
            return self._distance_cache[cache_key]

        node_path = self._distance_path_node_ids(start_node, end_node, method=method)
        distance = 0.0
        for u, v in zip(node_path, node_path[1:]):
            distance += float(self.graph[u][v].get("length_km", 0.0))

        self._distance_cache[cache_key] = float(distance)
        self._distance_cache[(end_node, start_node, method)] = float(distance)
        return float(distance)

    def _time_path_node_ids(
        self,
        start_node: str,
        end_node: str,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
        method: str = "dijkstra",
        heuristic_target: Optional[Location] = None,
    ) -> List[str]:
        cache_key = (
            start_node,
            end_node,
            self._time_cache_key(start_time),
            method,
            self._speed_key(vehicle_max_speed_kmh),
        )
        if cache_key in self._time_path_cache:
            return list(self._time_path_cache[cache_key])

        def time_weight(_u: str, _v: str, attrs: dict) -> float:
            length_km = float(attrs.get("length_km", 0.0))
            speed = self.effective_edge_speed_kmph(
                attrs,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
            )
            return length_km / max(1e-6, speed)

        if method == "a_star":
            target = heuristic_target or self._node_location(end_node)

            def heuristic(node):
                loc = self._node_location(node)
                if loc is None or target is None:
                    return 0.0
                return loc.distance_to(target) / self._fallback_speed(vehicle_max_speed_kmh)

            node_path = nx.astar_path(
                self.graph,
                start_node,
                end_node,
                heuristic=heuristic,
                weight=time_weight,
            )
        else:
            node_path = nx.dijkstra_path(self.graph, start_node, end_node, weight=time_weight)

        self._time_path_cache[cache_key] = list(node_path)
        self._time_path_cache[(end_node, start_node, cache_key[2], method, cache_key[4])] = list(
            reversed(node_path)
        )
        return list(node_path)

    def _path_travel_time_hours(
        self,
        node_path: List[str],
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
    ) -> float:
        total_hours = 0.0
        for u, v in zip(node_path, node_path[1:]):
            attrs = self.graph[u][v]
            length = float(attrs.get("length_km", 0.0))
            speed = self.effective_edge_speed_kmph(
                attrs,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
            )
            total_hours += length / max(1e-6, speed)
        return float(total_hours)

    def _fallback_metrics(
        self,
        start: Location,
        end: Location,
        vehicle_max_speed_kmh: Optional[float] = None,
    ) -> Dict[str, float]:
        distance = start.distance_to(end)
        speed = self._fallback_speed(vehicle_max_speed_kmh)
        return {
            "distance_km": distance,
            "time_hours": distance / speed,
            "avg_speed_kmph": speed,
            "energy_factor": 1.0,
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
        intersection_density = float(attrs.get("intersection_density", 0.4))

        surface_factor = 1.0 + max(0.0, 0.8 - surface_quality) * 0.35
        stop_go_factor = 1.0 + max(0.0, min(1.0, intersection_density)) * 0.12
        return float(surface_factor * stop_go_factor)

    def dijkstra(self, start: Location, end: Location) -> float:
        """Calculate shortest distance with cached Dijkstra paths."""
        start_node, end_node = self._nodes_for_locations(start, end)
        if start_node is None or end_node is None:
            return start.distance_to(end)
        try:
            return self._distance_path_length(start_node, end_node, method="dijkstra")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return start.distance_to(end)

    def a_star(self, start: Location, end: Location) -> float:
        """Calculate shortest distance with cached A* paths."""
        start_node, end_node = self._nodes_for_locations(start, end)
        if start_node is None or end_node is None:
            return start.distance_to(end)
        try:
            return self._distance_path_length(start_node, end_node, method="a_star")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return start.distance_to(end)

    def shortest_distance(self, start: Location, end: Location, method: str = "dijkstra") -> float:
        """Calculate shortest road distance in km."""
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
        start_node, end_node = self._nodes_for_locations(start, end)
        if start_node is None or end_node is None:
            return [start, end]

        try:
            node_path = self._distance_path_node_ids(
                start_node,
                end_node,
                method=method,
                heuristic_target=end,
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return [start, end]

        locations = [loc for loc in (self._node_location(node_id) for node_id in node_path) if loc is not None]
        path = [start]
        for loc in locations:
            if path[-1].distance_to(loc) > 1e-9:
                path.append(loc)
        if path[-1].distance_to(end) > 1e-9:
            path.append(end)
        return path or [start, end]

    def timed_path_locations(
        self,
        start: Location,
        end: Location,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
        method: str = "dijkstra",
    ) -> List[Tuple[Location, float]]:
        """Return path locations with cumulative travel hours at each node."""
        start_node, end_node = self._nodes_for_locations(start, end)
        if start_node is None or end_node is None:
            distance = start.distance_to(end)
            speed = self._fallback_speed(vehicle_max_speed_kmh)
            return [(start, 0.0), (end, distance / speed)]

        try:
            node_path = self._time_path_node_ids(
                start_node,
                end_node,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
                method=method,
                heuristic_target=end,
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            distance = start.distance_to(end)
            speed = self._fallback_speed(vehicle_max_speed_kmh)
            return [(start, 0.0), (end, distance / speed)]

        timed_path: List[Tuple[Location, float]] = [(start, 0.0)]
        cumulative_hours = 0.0
        for index, node_id in enumerate(node_path):
            loc = self._node_location(node_id)
            if loc is None:
                continue
            if index > 0:
                prev_node = node_path[index - 1]
                attrs = self.graph[prev_node][node_id]
                length = float(attrs.get("length_km", 0.0))
                speed = self.effective_edge_speed_kmph(
                    attrs,
                    start_time=start_time,
                    vehicle_max_speed_kmh=vehicle_max_speed_kmh,
                )
                cumulative_hours += length / max(1e-6, speed)
            if timed_path[-1][0].distance_to(loc) > 1e-9:
                timed_path.append((loc, cumulative_hours))

        if timed_path[-1][0].distance_to(end) > 1e-9:
            timed_path.append((end, cumulative_hours))

        return timed_path or [(start, 0.0), (end, 0.0)]

    def shortest_travel_time_hours(
        self,
        start: Location,
        end: Location,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
    ) -> float:
        """Calculate cached shortest travel time at a given departure time."""
        start_node, end_node = self._nodes_for_locations(start, end)
        if start_node is None or end_node is None:
            return start.distance_to(end) / self._fallback_speed(vehicle_max_speed_kmh)

        cache_key = (
            start_node,
            end_node,
            self._time_cache_key(start_time),
            self._speed_key(vehicle_max_speed_kmh),
        )
        if cache_key in self._time_length_cache:
            return self._time_length_cache[cache_key]

        try:
            node_path = self._time_path_node_ids(
                start_node,
                end_node,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
                method="dijkstra",
            )
            total_hours = self._path_travel_time_hours(
                node_path,
                start_time=start_time,
                vehicle_max_speed_kmh=vehicle_max_speed_kmh,
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return start.distance_to(end) / self._fallback_speed(vehicle_max_speed_kmh)

        self._time_length_cache[cache_key] = float(total_hours)
        self._time_length_cache[(end_node, start_node, cache_key[2], cache_key[3])] = float(total_hours)
        return float(total_hours)

    def path_road_metrics(
        self,
        start: Location,
        end: Location,
        start_time: datetime,
        vehicle_max_speed_kmh: Optional[float] = None,
        method: str = "dijkstra",
    ) -> Dict[str, float]:
        """Aggregate road attributes along the cached shortest distance path."""
        start_node, end_node = self._nodes_for_locations(start, end)
        if start_node is None or end_node is None:
            return self._fallback_metrics(start, end, vehicle_max_speed_kmh)

        cache_key = (
            start_node,
            end_node,
            self._time_cache_key(start_time),
            method,
            self._speed_key(vehicle_max_speed_kmh),
        )
        if cache_key in self._road_metrics_cache:
            return dict(self._road_metrics_cache[cache_key])

        try:
            node_path = self._distance_path_node_ids(
                start_node,
                end_node,
                method=method,
                heuristic_target=end,
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return self._fallback_metrics(start, end, vehicle_max_speed_kmh)

        total_distance = 0.0
        total_time = 0.0
        weighted_energy_factor = 0.0

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

        avg_speed = total_distance / max(1e-6, total_time)
        metrics = {
            "distance_km": float(total_distance),
            "time_hours": float(total_time),
            "avg_speed_kmph": float(avg_speed),
            "energy_factor": float(weighted_energy_factor / max(1e-9, total_distance)),
        }
        self._road_metrics_cache[cache_key] = dict(metrics)
        self._road_metrics_cache[(end_node, start_node, cache_key[2], method, cache_key[4])] = dict(metrics)
        return metrics

    def _find_nearest_node(self, location: Location) -> Optional[str]:
        """Find the nearest road node with name and coordinate caches."""
        if not self.nodes:
            return None
        if location.name in self._node_location_map:
            return location.name

        cache_key = self._location_cache_key(location)
        if cache_key in self._nearest_node_cache:
            return self._nearest_node_cache[cache_key]

        target = np.array([float(location.x), float(location.y)], dtype=float)
        distances_sq = np.sum((self._node_xy - target) ** 2, axis=1)
        nearest_idx = int(np.argmin(distances_sq))
        nearest_node = self._node_ids[nearest_idx]
        self._nearest_node_cache[cache_key] = nearest_node
        return nearest_node

    def _get_node_location(self, node_id: str) -> Optional[Tuple[str, Location]]:
        """Return a node location by id in O(1)."""
        loc = self._node_location(node_id)
        if loc is None:
            return None
        return (node_id, loc)

    def cache_stats(self) -> Dict[str, int]:
        """Expose lightweight cache sizes for profiling larger benchmark runs."""
        return {
            "nearest_node": len(self._nearest_node_cache),
            "distance": len(self._distance_cache),
            "distance_path": len(self._distance_path_cache),
            "time_path": len(self._time_path_cache),
            "time_length": len(self._time_length_cache),
            "road_metrics": len(self._road_metrics_cache),
        }
