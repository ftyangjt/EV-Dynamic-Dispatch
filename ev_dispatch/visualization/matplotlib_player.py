from pathlib import Path
from typing import Dict, List, Optional

import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np

from ev_dispatch.core.interfaces import SimulationFrame
from ev_dispatch.core.network import RoadNetwork


ROAD_COLORS = {
    "arterial": "#65a30d",
    "collector": "#0891b2",
    "local": "#64748b",
}
STATUS_COLORS = {
    "idle": "#38bdf8",
    "en_route": "#f59e0b",
    "occupied": "#f59e0b",
    "charging": "#22c55e",
    "maintenance": "#ef4444",
}
TASK_COLORS = {
    "pending": "#f97316",
    "in_progress": "#a78bfa",
    "completed": "#22c55e",
    "failed": "#ef4444",
}


def _draw_network(ax: plt.Axes, network: RoadNetwork) -> None:
    node_xy = {node_id: (loc.x, loc.y) for node_id, loc in network.nodes}

    for n1, n2, attrs in network.graph.edges(data=True):
        x1, y1 = node_xy[n1]
        x2, y2 = node_xy[n2]
        road_type = attrs.get("road_type", "local")
        color = ROAD_COLORS.get(road_type, "#64748b")
        ax.plot(
            [x1, x2],
            [y1, y2],
            color=color,
            linewidth=0.8 + float(attrs.get("lane_count", 1)) * 0.45,
            alpha=0.58,
            zorder=1,
        )

    xs = [loc.x for _, loc in network.nodes]
    ys = [loc.y for _, loc in network.nodes]
    ax.scatter(xs, ys, s=22, color="#cbd5e1", edgecolors="#475569", linewidths=0.6, zorder=2)


def _resolve_writer(save_path: Path) -> Optional[animation.AbstractMovieWriter]:
    suffix = save_path.suffix.lower()
    if suffix == ".gif":
        return animation.PillowWriter(fps=8)

    if not animation.writers.is_available("ffmpeg"):
        return None
    ffmpeg = animation.writers["ffmpeg"]
    return ffmpeg(fps=8, bitrate=2400)


def _task_points(frame: SimulationFrame) -> Dict[str, List[dict]]:
    grouped = {"pending": [], "in_progress": [], "completed": [], "failed": []}
    for task in (frame.task_details or {}).values():
        status = str(task.get("status", "pending"))
        if status not in grouped:
            grouped[status] = []
        origin = task.get("origin") or {}
        destination = task.get("destination") or {}
        if "x" not in origin or "y" not in origin:
            continue
        grouped[status].append(
            {
                "id": task.get("id", ""),
                "origin": origin,
                "destination": destination,
                "vehicle": ", ".join(task.get("assigned_vehicles") or []),
            }
        )
    return grouped


def _draw_routes(ax: plt.Axes, frame: SimulationFrame, selected_vehicle_ids: List[str]) -> List[plt.Artist]:
    artists: List[plt.Artist] = []
    for vid in selected_vehicle_ids:
        details = (frame.vehicle_details or {}).get(vid, {})
        route = details.get("route") or []
        if len(route) < 2:
            continue
        color = STATUS_COLORS.get(details.get("status", "idle"), "#38bdf8")
        xs = [float(p["x"]) for p in route]
        ys = [float(p["y"]) for p in route]
        (line,) = ax.plot(xs, ys, color=color, linewidth=3.0, alpha=0.7, zorder=3)
        artists.append(line)
    return artists


def play_simulation_frames(
    title: str,
    network: RoadNetwork,
    frames: List[SimulationFrame],
    save_path: Optional[str] = None,
    show_plot: bool = True,
) -> Optional[str]:
    """Render a richer dispatch animation and optionally export it."""
    if not frames:
        return None

    fig = plt.figure(figsize=(12, 7.5), facecolor="#0f172a")
    grid = fig.add_gridspec(1, 2, width_ratios=[3.2, 1.15], wspace=0.08)
    ax = fig.add_subplot(grid[0, 0])
    info_ax = fig.add_subplot(grid[0, 1])
    fig.suptitle(title, fontsize=15, color="#f8fafc", y=0.98)

    ax.set_facecolor("#111827")
    _draw_network(ax, network)
    ax.set_xlim(0, network.width)
    ax.set_ylim(0, network.height)
    ax.set_xlabel("X", color="#cbd5e1")
    ax.set_ylabel("Y", color="#cbd5e1")
    ax.tick_params(colors="#94a3b8")
    ax.grid(alpha=0.12, linestyle="--", color="#94a3b8")

    info_ax.set_facecolor("#111827")
    info_ax.axis("off")

    first_frame = frames[0]
    vehicle_ids = list(first_frame.vehicle_positions.keys())
    x0 = [first_frame.vehicle_positions[vid][0] for vid in vehicle_ids]
    y0 = [first_frame.vehicle_positions[vid][1] for vid in vehicle_ids]
    b0 = [first_frame.vehicle_battery[vid] for vid in vehicle_ids]

    vehicles = ax.scatter(
        x0,
        y0,
        c=b0,
        cmap="RdYlGn",
        vmin=0,
        vmax=150,
        s=180,
        edgecolors="#020617",
        linewidths=1.0,
        zorder=5,
    )
    cbar = fig.colorbar(vehicles, ax=ax, fraction=0.035, pad=0.015)
    cbar.set_label("Battery (kWh)", color="#f8fafc")
    cbar.ax.yaxis.set_tick_params(color="#94a3b8")
    plt.setp(cbar.ax.get_yticklabels(), color="#94a3b8")

    labels: Dict[str, plt.Text] = {}
    for vid in vehicle_ids:
        x, y = first_frame.vehicle_positions[vid]
        labels[vid] = ax.text(x + 0.15, y + 0.15, vid, fontsize=8, color="#f8fafc", zorder=7)

    station_scatter = ax.scatter([], [], marker="^", s=230, color="#fde047", edgecolors="#0f172a", linewidths=1.2, zorder=4)
    pending_tasks = ax.scatter([], [], marker="o", s=95, color=TASK_COLORS["pending"], edgecolors="#111827", linewidths=1.0, zorder=4)
    active_tasks = ax.scatter([], [], marker="D", s=80, color=TASK_COLORS["in_progress"], edgecolors="#111827", linewidths=1.0, zorder=4)
    failed_tasks = ax.scatter([], [], marker="x", s=80, color=TASK_COLORS["failed"], linewidths=1.5, zorder=4)

    dynamic_artists: List[plt.Artist] = []

    def _set_scatter_offsets(scatter, points: List[dict]) -> None:
        if points:
            scatter.set_offsets(np.array([[p["origin"]["x"], p["origin"]["y"]] for p in points], dtype=float))
        else:
            scatter.set_offsets(np.empty((0, 2)))

    def _station_offsets(frame: SimulationFrame) -> np.ndarray:
        stations = []
        for station in (frame.station_details or {}).values():
            pos = station.get("position") or {}
            if "x" in pos and "y" in pos:
                stations.append([pos["x"], pos["y"]])
        return np.array(stations, dtype=float) if stations else np.empty((0, 2))

    def _update(frame_idx: int):
        nonlocal dynamic_artists
        for artist in dynamic_artists:
            artist.remove()
        dynamic_artists = []

        frame = frames[frame_idx]
        x = [frame.vehicle_positions[vid][0] for vid in vehicle_ids]
        y = [frame.vehicle_positions[vid][1] for vid in vehicle_ids]
        battery = [frame.vehicle_battery[vid] for vid in vehicle_ids]

        vehicles.set_offsets(np.array(list(zip(x, y)), dtype=float))
        vehicles.set_array(np.array(battery, dtype=float))

        for vid in vehicle_ids:
            vx, vy = frame.vehicle_positions[vid]
            labels[vid].set_position((vx + 0.15, vy + 0.15))

        station_scatter.set_offsets(_station_offsets(frame))
        task_groups = _task_points(frame)
        _set_scatter_offsets(pending_tasks, task_groups.get("pending", []))
        _set_scatter_offsets(active_tasks, task_groups.get("in_progress", []))
        _set_scatter_offsets(failed_tasks, task_groups.get("failed", []))

        moving_ids = [
            vid
            for vid, details in (frame.vehicle_details or {}).items()
            if details.get("status") in ("en_route", "occupied")
        ][:4]
        dynamic_artists.extend(_draw_routes(ax, frame, moving_ids))

        info_ax.clear()
        info_ax.set_facecolor("#111827")
        info_ax.axis("off")
        task_count = len(frame.task_details or {})
        in_progress = sum(1 for t in (frame.task_details or {}).values() if t.get("status") == "in_progress")
        charging = sum(1 for v in (frame.vehicle_details or {}).values() if v.get("status") == "charging")
        avg_battery = float(np.mean(battery)) if battery else 0.0

        lines = [
            f"Frame {frame_idx + 1}/{len(frames)}",
            f"Step {frame.step}",
            f"Time {frame.current_time.strftime('%m-%d %H:%M')}",
            "",
            f"Vehicles: {len(vehicle_ids)}",
            f"Moving: {len(moving_ids)}",
            f"Charging: {charging}",
            f"Avg battery: {avg_battery:.1f} kWh",
            "",
            f"Tasks total: {task_count}",
            f"Pending: {len(frame.pending_task_ids)}",
            f"In progress: {in_progress}",
            f"Completed: {len(frame.completed_task_ids)}",
            f"Failed: {len(frame.failed_task_ids)}",
            "",
            "Road legend:",
            "green arterial",
            "cyan collector",
            "gray local",
            "red restricted",
        ]
        info_ax.text(
            0.02,
            0.98,
            "\n".join(lines),
            va="top",
            ha="left",
            color="#e2e8f0",
            fontsize=10,
            family="monospace",
            linespacing=1.35,
        )

        return [
            vehicles,
            station_scatter,
            pending_tasks,
            active_tasks,
            failed_tasks,
            *labels.values(),
            *dynamic_artists,
        ]

    ax.legend(
        handles=[
            plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="#fde047", markersize=10, label="charging station"),
            plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=TASK_COLORS["pending"], markersize=8, label="pending task"),
            plt.Line2D([0], [0], marker="D", color="w", markerfacecolor=TASK_COLORS["in_progress"], markersize=7, label="active task"),
        ],
        loc="lower left",
        framealpha=0.85,
        facecolor="#0f172a",
        labelcolor="#e2e8f0",
        edgecolor="#334155",
    )

    ani = animation.FuncAnimation(
        fig,
        _update,
        frames=len(frames),
        interval=160,
        blit=False,
        repeat=True,
    )

    exported_file: Optional[str] = None
    if save_path:
        out_path = Path(save_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        writer = _resolve_writer(out_path)
        if writer is not None:
            ani.save(str(out_path), writer=writer)
            exported_file = str(out_path)

    if show_plot:
        plt.show()
    else:
        plt.close(fig)

    return exported_file
