import argparse
from copy import deepcopy
from typing import Dict, List, Tuple

# 兼容直接运行: python ev_dispatch/main.py
if __package__ is None or __package__ == "":
    import os
    import sys

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from ev_dispatch.algorithms.strategies import DispatcherLargestFirst, DispatcherNearestFirst
from ev_dispatch.core.interfaces import SimulationFrame
from ev_dispatch.scenarios.default import build_default_scenario, CargoConfig
from ev_dispatch.simulator.simulator import Simulator
from ev_dispatch.visualization.console import print_run_summary


def _run_strategy(
    name: str,
    simulator: Simulator,
    num_steps: int,
    tasks_per_step: int,
) -> Tuple[Dict[str, float], List[SimulationFrame]]:
    results = simulator.run_simulation(num_steps=num_steps, tasks_per_step=tasks_per_step)
    frames = simulator.get_frames()
    print_run_summary(name, results, frames)
    return results, frames


def run_demo(
    num_steps: int = 10,
    tasks_per_step: int = 3,
    visualize: bool = False,
    visualize_strategy: str = "nearest",
    save_animation: str = "",
    no_show: bool = False,
) -> None:
    print("=" * 50)
    print("新能源物流车队协同调度系统 - 模块化演示")
    print("=" * 50)

    network, vehicles, charging_stations = build_default_scenario()

    print("\n[初始化] 创建场景...")
    print(f"车队规模: {len(vehicles)} 辆车")
    print(f"充电站数: {len(charging_stations)} 个")
    
    # 创建货物类型配置
    cargo_config = CargoConfig(num_types=4, type_1_ratio=0.7)

    print("\n[策略1] 最近任务优先调度")
    sim1 = Simulator(
        network=network,
        vehicles=deepcopy(vehicles),
        charging_stations=charging_stations,
        dispatcher=DispatcherNearestFirst(network),
        cargo_config=cargo_config,
        random_seed=42,
        debug_run_id="pre",
    )
    results1, frames1 = _run_strategy(
        "策略1结果",
        sim1,
        num_steps=num_steps,
        tasks_per_step=tasks_per_step,
    )

    print("\n[策略2] 最大任务优先调度")
    sim2 = Simulator(
        network=network,
        vehicles=deepcopy(vehicles),
        charging_stations=charging_stations,
        dispatcher=DispatcherLargestFirst(network),
        cargo_config=cargo_config,
        random_seed=42,
        debug_run_id="pre",
    )
    results2, frames2 = _run_strategy(
        "策略2结果",
        sim2,
        num_steps=num_steps,
        tasks_per_step=tasks_per_step,
    )

    print("\n[对比分析]")
    diff = results1["total_score"] - results2["total_score"]
    winner = "最近优先" if diff > 0 else "最大优先"
    print(f"策略1 vs 策略2 - 评分差: {diff:.2f}")
    print(f"更优策略: {winner}")

    if visualize:
        if no_show and not save_animation:
            print("\n[可视化提示] 你启用了 --no-show，图窗不会弹出；且未提供 --save-animation，因此不会产生可视化输出文件。")

        try:
            from ev_dispatch.visualization import play_simulation_frames
        except Exception as exc:
            print("\n[可视化错误] Matplotlib 环境不可用，请检查 numpy/matplotlib 版本。")
            print(f"详细信息: {exc}")
            return

        if visualize_strategy == "largest":
            target_frames = frames2
            title = "EV Dispatch - Largest First"
        else:
            target_frames = frames1
            title = "EV Dispatch - Nearest First"

        try:
            out_file = play_simulation_frames(
                title=title,
                network=network,
                frames=target_frames,
                save_path=save_animation if save_animation else None,
                show_plot=not no_show,
            )
        except Exception as exc:
            print("\n[可视化错误] Matplotlib 环境不可用，请检查 numpy/matplotlib 版本。")
            print(f"详细信息: {exc}")
            return
        if save_animation and out_file is not None:
            print(f"\n[可视化导出] 已保存: {out_file}")
        elif save_animation and out_file is None:
            print("\n[可视化导出] 未找到可用写入器，已跳过导出。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EV 动态调度演示入口")
    parser.add_argument("--steps", type=int, default=10, help="仿真步数")
    parser.add_argument("--tasks-per-step", type=int, default=3, help="每步新任务数")
    parser.add_argument("--visualize", action="store_true", help="启用 Matplotlib 动画")
    parser.add_argument(
        "--visualize-strategy",
        choices=["nearest", "largest"],
        default="nearest",
        help="可视化展示的策略轨迹",
    )
    parser.add_argument(
        "--save-animation",
        default="",
        help="导出动画路径（.gif 或 .mp4）",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="仅导出动画，不弹出图窗",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_demo(
        num_steps=args.steps,
        tasks_per_step=args.tasks_per_step,
        visualize=args.visualize,
        visualize_strategy=args.visualize_strategy,
        save_animation=args.save_animation,
        no_show=args.no_show,
    )
