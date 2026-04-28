from copy import deepcopy

# 兼容直接运行: python ev_dispatch/main.py
if __package__ is None or __package__ == "":
    import os
    import sys

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from ev_dispatch.algorithms.strategies import DispatcherLargestFirst, DispatcherNearestFirst
from ev_dispatch.scenarios.default import build_default_scenario
from ev_dispatch.simulator.simulator import Simulator
from ev_dispatch.visualization.console import print_run_summary


def run_demo() -> None:
    print("=" * 50)
    print("新能源物流车队协同调度系统 - 模块化演示")
    print("=" * 50)

    network, vehicles, charging_stations = build_default_scenario()

    print("\n[初始化] 创建场景...")
    print(f"车队规模: {len(vehicles)} 辆车")
    print(f"充电站数: {len(charging_stations)} 个")

    print("\n[策略1] 最近任务优先调度")
    sim1 = Simulator(
        network=network,
        vehicles=deepcopy(vehicles),
        charging_stations=charging_stations,
        dispatcher=DispatcherNearestFirst(network),
        random_seed=42,
        debug_run_id="pre",
    )
    results1 = sim1.run_simulation(num_steps=10, tasks_per_step=3)
    print_run_summary("策略1结果", results1, sim1.get_frames())

    print("\n[策略2] 最大任务优先调度")
    sim2 = Simulator(
        network=network,
        vehicles=deepcopy(vehicles),
        charging_stations=charging_stations,
        dispatcher=DispatcherLargestFirst(network),
        random_seed=42,
        debug_run_id="pre",
    )
    results2 = sim2.run_simulation(num_steps=10, tasks_per_step=3)
    print_run_summary("策略2结果", results2, sim2.get_frames())

    print("\n[对比分析]")
    diff = results1["total_score"] - results2["total_score"]
    winner = "最近优先" if diff > 0 else "最大优先"
    print(f"策略1 vs 策略2 - 评分差: {diff:.2f}")
    print(f"更优策略: {winner}")


if __name__ == "__main__":
    run_demo()
