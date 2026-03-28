#!/usr/bin/env python3
"""
测试Dijkstra和A*算法的实现
"""

import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.location import Location


def test_pathfinding():
    print("=" * 60)
    print("路径规划算法测试 - Dijkstra vs A*")
    print("=" * 60)
    
    # 创建网络 (5×5网格)
    network = RoadNetwork(width=20.0, height=20.0, num_nodes=25)
    print(f"\n✓ 创建路网: 5×5网格, 共{len(network.nodes)}个节点")
    print(f"  地图范围: [0, {network.width}] × [0, {network.height}]")
    
    # 测试用例
    test_cases = [
        {
            "name": "短距离 (相邻区域)",
            "start": Location(2, 2, "start"),
            "end": Location(8, 8, "end")
        },
        {
            "name": "中距离 (对角线)",
            "start": Location(2, 2, "start"),
            "end": Location(18, 18, "end")
        },
        {
            "name": "长距离 (对角)",
            "start": Location(2, 2, "start"),
            "end": Location(18, 2, "end")
        },
        {
            "name": "相同点 (起点=终点)",
            "start": Location(10, 10, "center"),
            "end": Location(10, 10, "center")
        }
    ]
    
    print("\n" + "-" * 60)
    print("测试结果对比:")
    print("-" * 60)
    
    for i, test in enumerate(test_cases, 1):
        name = test["name"]
        start = test["start"]
        end = test["end"]
        
        # 计算直线距离
        straight_dist = start.distance_to(end)
        
        # 运行Dijkstra
        dijkstra_dist = network.dijkstra(start, end)
        
        # 运行A*
        astar_dist = network.a_star(start, end)
        
        print(f"\n测试{i}: {name}")
        print(f"  起点: ({start.x:.1f}, {start.y:.1f})")
        print(f"  终点: ({end.x:.1f}, {end.y:.1f})")
        print(f"  直线距离:        {straight_dist:.3f} km")
        print(f"  Dijkstra距离:    {dijkstra_dist:.3f} km")
        print(f"  A*距离:          {astar_dist:.3f} km")
        
        # 验证结果
        if abs(dijkstra_dist - astar_dist) < 0.001:
            print(f"  ✓ 两算法结果一致")
        else:
            print(f"  ⚠ 两算法结果不同 (差异: {abs(dijkstra_dist - astar_dist):.3f})")
        
        if dijkstra_dist >= straight_dist * 0.99:  # 允许数值误差
            print(f"  ✓ 网络距离 >= 直线距离 (合理)")
        else:
            print(f"  ⚠ 网络距离 < 直线距离 (异常)")
    
    print("\n" + "=" * 60)
    print("性能对比 (100次迭代):")
    print("=" * 60)
    
    import time
    
    start_loc = Location(2, 2, "start")
    end_loc = Location(18, 18, "end")
    iterations = 100
    
    # 测试Dijkstra性能
    t0 = time.time()
    for _ in range(iterations):
        network.dijkstra(start_loc, end_loc)
    dijkstra_time = (time.time() - t0) * 1000 / iterations  # ms
    
    # 测试A*性能
    t0 = time.time()
    for _ in range(iterations):
        network.a_star(start_loc, end_loc)
    astar_time = (time.time() - t0) * 1000 / iterations  # ms
    
    print(f"\nDijkstra平均耗时: {dijkstra_time:.3f} ms")
    print(f"A*平均耗时:       {astar_time:.3f} ms")
    
    if astar_time < dijkstra_time:
        improvement = (dijkstra_time - astar_time) / dijkstra_time * 100
        print(f"A*快 {improvement:.1f}% ✓")
    else:
        print(f"Dijkstra快 ✓")
    
    print("\n" + "=" * 60)
    print("✓ 测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    test_pathfinding()
