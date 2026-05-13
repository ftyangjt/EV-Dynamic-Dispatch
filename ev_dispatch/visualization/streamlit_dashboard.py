from copy import deepcopy
import os
import sys
from typing import Dict, List, Tuple

# Support running with either:
# - streamlit run ev_dispatch/visualization/streamlit_dashboard.py  (project root)
# - streamlit run .\visualization\streamlit_dashboard.py            (inside ev_dispatch)
if __package__ is None or __package__ == "":
    _this_file = os.path.abspath(__file__)
    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(_this_file)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

from ev_dispatch.algorithms.strategies import DispatcherLargestFirst, DispatcherNearestFirst
from ev_dispatch.core.interfaces import SimulationFrame
from ev_dispatch.scenarios.default import build_default_scenario
from ev_dispatch.simulator.simulator import Simulator


SCENARIO_PRESETS: Dict[str, Dict[str, float]] = {
    "小规模": {
        "width": 16,
        "height": 16,
        "num_nodes": 16,
        "num_vehicles": 4,
        "num_stations": 2,
    },
    "中规模": {
        "width": 20,
        "height": 20,
        "num_nodes": 25,
        "num_vehicles": 8,
        "num_stations": 3,
    },
    "大规模": {
        "width": 28,
        "height": 28,
        "num_nodes": 49,
        "num_vehicles": 16,
        "num_stations": 5,
    },
}


def _run_single_strategy(
    strategy_name: str,
    num_steps: int,
    tasks_per_step: int,
    scenario_name: str,
) -> Tuple[Dict[str, float], List[SimulationFrame], object]:
    cfg = SCENARIO_PRESETS[scenario_name]
    network, vehicles, charging_stations = build_default_scenario(**cfg)

    if strategy_name == "largest":
        dispatcher = DispatcherLargestFirst(network)
    else:
        dispatcher = DispatcherNearestFirst(network)

    sim = Simulator(
        network=network,
        vehicles=deepcopy(vehicles),
        charging_stations=charging_stations,
        dispatcher=dispatcher,
    )
    results = sim.run_simulation(num_steps=num_steps, tasks_per_step=tasks_per_step)
    return results, sim.get_frames(), network


def _build_timeline(frames: List[SimulationFrame]) -> Dict[str, List[float]]:
    steps: List[int] = []
    pending: List[int] = []
    completed: List[int] = []
    failed: List[int] = []
    avg_battery: List[float] = []

    for frame in frames:
        steps.append(frame.step)
        pending.append(len(frame.pending_task_ids))
        completed.append(len(frame.completed_task_ids))
        failed.append(len(frame.failed_task_ids))
        battery_values = list(frame.vehicle_battery.values())
        avg_battery.append(float(np.mean(battery_values)) if battery_values else 0.0)

    return {
        "step": steps,
        "pending": pending,
        "completed": completed,
        "failed": failed,
        "avg_battery": avg_battery,
    }


def _render_snapshot(network: object, frame: SimulationFrame, panel_title: str) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    ax.set_title(panel_title)

    node_xy = {node_id: (loc.x, loc.y) for node_id, loc in network.nodes}
    for n1, n2 in network.graph.edges():
        x1, y1 = node_xy[n1]
        x2, y2 = node_xy[n2]
        ax.plot([x1, x2], [y1, y2], color="#d0d7de", linewidth=0.8, alpha=0.8, zorder=1)

    xs = [loc.x for _, loc in network.nodes]
    ys = [loc.y for _, loc in network.nodes]
    ax.scatter(xs, ys, s=12, color="#8b949e", alpha=0.7, zorder=2)

    vehicle_ids = list(frame.vehicle_positions.keys())
    vx = [frame.vehicle_positions[vid][0] for vid in vehicle_ids]
    vy = [frame.vehicle_positions[vid][1] for vid in vehicle_ids]
    vb = [frame.vehicle_battery[vid] for vid in vehicle_ids]
    sc = ax.scatter(
        vx,
        vy,
        c=vb,
        cmap="RdYlGn",
        vmin=0,
        vmax=100,
        s=120,
        edgecolors="black",
        linewidths=0.5,
        zorder=3,
    )
    for vid in vehicle_ids:
        x, y = frame.vehicle_positions[vid]
        ax.text(x + 0.1, y + 0.1, vid, fontsize=7)

    ax.set_xlim(0, network.width)
    ax.set_ylim(0, network.height)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.grid(alpha=0.2, linestyle="--")
    fig.colorbar(sc, ax=ax, fraction=0.04, pad=0.02, label="Battery")

    st.pyplot(fig, clear_figure=True)


def main() -> None:
    st.set_page_config(page_title="EV Dispatch Dashboard", layout="wide")
    st.title("EV 动态调度 Streamlit 仪表盘")
    st.caption("阶段二：场景规模 + 算法选择 + 一键运行 + 图表面板")

    with st.sidebar:
        st.header("运行参数")
        scenario_name = st.selectbox("场景规模", list(SCENARIO_PRESETS.keys()), index=1)
        algo_mode = st.selectbox(
            "算法选择",
            ["仅最近优先", "仅最大优先", "双策略对比"],
            index=2,
        )
        num_steps = st.slider("仿真步数", min_value=5, max_value=200, value=40, step=5)
        tasks_per_step = st.slider("每步任务数", min_value=1, max_value=8, value=3, step=1)
        random_seed = st.number_input("随机种子", min_value=0, max_value=999999, value=42)

        run_clicked = st.button("一键运行", type="primary", use_container_width=True)

    if "dashboard_runs" not in st.session_state:
        st.session_state.dashboard_runs = {}

    if run_clicked:
        np.random.seed(int(random_seed))
        run_data: Dict[str, Dict[str, object]] = {}

        with st.spinner("正在运行仿真并生成图表..."):
            if algo_mode in ("仅最近优先", "双策略对比"):
                n_results, n_frames, n_network = _run_single_strategy(
                    "nearest",
                    num_steps=num_steps,
                    tasks_per_step=tasks_per_step,
                    scenario_name=scenario_name,
                )
                run_data["nearest"] = {
                    "results": n_results,
                    "frames": n_frames,
                    "network": n_network,
                    "timeline": _build_timeline(n_frames),
                }

            if algo_mode in ("仅最大优先", "双策略对比"):
                l_results, l_frames, l_network = _run_single_strategy(
                    "largest",
                    num_steps=num_steps,
                    tasks_per_step=tasks_per_step,
                    scenario_name=scenario_name,
                )
                run_data["largest"] = {
                    "results": l_results,
                    "frames": l_frames,
                    "network": l_network,
                    "timeline": _build_timeline(l_frames),
                }

        st.session_state.dashboard_runs = run_data

    runs = st.session_state.dashboard_runs
    if not runs:
        st.info("请在左侧配置参数后点击“一键运行”。")
        return

    st.subheader("核心指标")
    metric_cols = st.columns(len(runs))
    for idx, (name, data) in enumerate(runs.items()):
        results = data["results"]
        with metric_cols[idx]:
            st.markdown(f"### {name}")
            st.metric("总评分", f"{results['total_score']:.2f}")
            st.metric("完成任务", f"{int(results['completed'])}")
            st.metric("失败任务", f"{int(results['failed'])}")
            st.metric("总里程", f"{results['total_distance']:.2f}")

    st.subheader("图表面板")
    chart_left, chart_right = st.columns(2)

    with chart_left:
        st.markdown("#### 每步任务状态趋势")
        status_series: Dict[str, List[float]] = {}
        for name, data in runs.items():
            timeline = data["timeline"]
            status_series[f"{name}_pending"] = timeline["pending"]
            status_series[f"{name}_completed"] = timeline["completed"]
            status_series[f"{name}_failed"] = timeline["failed"]
        st.line_chart(status_series)

    with chart_right:
        st.markdown("#### 平均电量趋势")
        battery_series = {
            name: data["timeline"]["avg_battery"]
            for name, data in runs.items()
        }
        st.line_chart(battery_series)

    compare_left, compare_right = st.columns(2)
    with compare_left:
        st.markdown("#### 策略收益对比")
        compare_scores = {
            name: [data["results"]["total_score"]]
            for name, data in runs.items()
        }
        st.bar_chart(compare_scores)

    with compare_right:
        st.markdown("#### 任务完成/失败对比")
        compare_task_counts = {
            f"{name}_completed": [data["results"]["completed"]]
            for name, data in runs.items()
        }
        for name, data in runs.items():
            compare_task_counts[f"{name}_failed"] = [data["results"]["failed"]]
        st.bar_chart(compare_task_counts)

    st.subheader("路网快照面板")
    selected_strategy = st.selectbox("选择轨迹", list(runs.keys()))
    selected_data = runs[selected_strategy]
    selected_frames: List[SimulationFrame] = selected_data["frames"]
    if selected_frames:
        max_step = len(selected_frames) - 1
        step_idx = st.slider("查看 step", min_value=0, max_value=max_step, value=0)
        frame = selected_frames[step_idx]
        _render_snapshot(
            selected_data["network"],
            frame,
            panel_title=f"{selected_strategy} @ step={frame.step}",
        )


if __name__ == "__main__":
    main()
