from pathlib import Path
from typing import Dict, List, Optional

import matplotlib.animation as animation
import matplotlib.pyplot as plt

from ev_dispatch.core.interfaces import SimulationFrame
from ev_dispatch.core.network import RoadNetwork


def _draw_network(ax: plt.Axes, network: RoadNetwork) -> None:
    node_xy = {node_id: (loc.x, loc.y) for node_id, loc in network.nodes}

    for n1, n2 in network.graph.edges():
        x1, y1 = node_xy[n1]
        x2, y2 = node_xy[n2]
        ax.plot([x1, x2], [y1, y2], color="#d0d7de", linewidth=1.0, alpha=0.8, zorder=1)

    xs = [loc.x for _, loc in network.nodes]
    ys = [loc.y for _, loc in network.nodes]
    ax.scatter(xs, ys, s=18, color="#8b949e", alpha=0.8, zorder=2)


def _resolve_writer(save_path: Path) -> Optional[animation.AbstractMovieWriter]:
    suffix = save_path.suffix.lower()
    if suffix == ".gif":
        return animation.PillowWriter(fps=5)

    if not animation.writers.is_available("ffmpeg"):
        return None
    ffmpeg = animation.writers["ffmpeg"]
    return ffmpeg(fps=5, bitrate=1800)


def play_simulation_frames(
    title: str,
    network: RoadNetwork,
    frames: List[SimulationFrame],
    save_path: Optional[str] = None,
    show_plot: bool = True,
) -> Optional[str]:
    """Render simulation frames with Matplotlib animation and optionally export to file."""
    if not frames:
        return None

    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    fig.suptitle(title, fontsize=14)
    _draw_network(ax, network)

    ax.set_xlim(0, network.width)
    ax.set_ylim(0, network.height)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.grid(alpha=0.2, linestyle="--")

    first_frame = frames[0]
    vehicle_ids = list(first_frame.vehicle_positions.keys())
    x0 = [first_frame.vehicle_positions[vid][0] for vid in vehicle_ids]
    y0 = [first_frame.vehicle_positions[vid][1] for vid in vehicle_ids]
    c0 = [first_frame.vehicle_battery[vid] for vid in vehicle_ids]

    scat = ax.scatter(
        x0,
        y0,
        c=c0,
        cmap="RdYlGn",
        vmin=0,
        vmax=100,
        s=140,
        edgecolors="black",
        linewidths=0.6,
        zorder=4,
    )
    cbar = fig.colorbar(scat, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label("Battery")

    labels: Dict[str, plt.Text] = {}
    for vid in vehicle_ids:
        x, y = first_frame.vehicle_positions[vid]
        labels[vid] = ax.text(x + 0.1, y + 0.1, vid, fontsize=8, color="#24292f", zorder=5)

    status_text = ax.text(
        0.02,
        0.98,
        "",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.9),
    )

    def _update(frame_idx: int):
        frame = frames[frame_idx]
        x = [frame.vehicle_positions[vid][0] for vid in vehicle_ids]
        y = [frame.vehicle_positions[vid][1] for vid in vehicle_ids]
        battery = [frame.vehicle_battery[vid] for vid in vehicle_ids]

        scat.set_offsets(list(zip(x, y)))
        scat.set_array(battery)

        for vid in vehicle_ids:
            vx, vy = frame.vehicle_positions[vid]
            labels[vid].set_position((vx + 0.1, vy + 0.1))

        status_text.set_text(
            f"step={frame.step}  time={frame.current_time.strftime('%m-%d %H:%M')}\n"
            f"pending={len(frame.pending_task_ids)}  completed={len(frame.completed_task_ids)}  failed={len(frame.failed_task_ids)}"
        )

        return [scat, status_text, *labels.values()]

    ani = animation.FuncAnimation(
        fig,
        _update,
        frames=len(frames),
        interval=300,
        blit=False,
        repeat=False,
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
