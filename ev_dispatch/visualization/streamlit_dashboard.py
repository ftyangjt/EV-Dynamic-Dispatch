from copy import deepcopy
import base64
import json
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

import numpy as np
import streamlit as st

from ev_dispatch.algorithms.strategies import (
    COMPOSITE_CONFIG_PRESETS,
    DispatcherCompositeScore,
    DispatcherLargestFirst,
    DispatcherNearestFirst,
)
from ev_dispatch.core.interfaces import SimulationFrame
from ev_dispatch.scenarios.default import CargoConfig, build_default_scenario
from ev_dispatch.simulator.simulator import DEFAULT_SIMULATION_START_TIME, Simulator


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

STRATEGY_LABELS = {
    "nearest": "最近优先",
    "largest": "最大优先",
    "composite": "综合评分",
}


def _run_single_strategy(
    strategy_name: str,
    num_steps: int,
    tasks_per_step: int,
    scenario_name: str,
    random_seed: int,
    composite_preset: str = "balanced",
) -> Tuple[Dict[str, float], List[SimulationFrame], object, List[object]]:
    cfg = SCENARIO_PRESETS[scenario_name]
    network, vehicles, charging_stations = build_default_scenario(
        **cfg,
        random_seed=random_seed,
    )

    if strategy_name == "largest":
        dispatcher = DispatcherLargestFirst(network)
    elif strategy_name == "composite":
        dispatcher = DispatcherCompositeScore(
            network,
            config=COMPOSITE_CONFIG_PRESETS[composite_preset],
        )
    else:
        dispatcher = DispatcherNearestFirst(network)

    sim = Simulator(
        network=network,
        vehicles=deepcopy(vehicles),
        charging_stations=charging_stations,
        dispatcher=dispatcher,
        cargo_config=CargoConfig(num_types=4, type_1_ratio=0.7),
        random_seed=random_seed,
        start_time=DEFAULT_SIMULATION_START_TIME,
        debug_run_id=f"streamlit-{strategy_name}-{random_seed}",
    )
    results = sim.run_simulation(num_steps=num_steps, tasks_per_step=tasks_per_step)
    return results, sim.get_frames(), network, charging_stations


def _build_timeline(frames: List[SimulationFrame]) -> Dict[str, List[float]]:
    steps: List[float] = []
    pending: List[int] = []
    completed: List[int] = []
    failed: List[int] = []
    in_progress: List[int] = []
    avg_battery: List[float] = []

    base_time = frames[0].current_time if frames else None
    for frame in frames:
        if base_time is None:
            steps.append(float(frame.step))
        else:
            steps.append((frame.current_time - base_time).total_seconds() / 3600.0)
        pending.append(len(frame.pending_task_ids))
        completed.append(len(frame.completed_task_ids))
        failed.append(len(frame.failed_task_ids))
        task_details = frame.task_details or {}
        in_progress.append(sum(1 for task in task_details.values() if task.get("status") == "in_progress"))
        battery_values = list(frame.vehicle_battery.values())
        avg_battery.append(float(np.mean(battery_values)) if battery_values else 0.0)

    return {
        "step": steps,
        "pending": pending,
        "completed": completed,
        "failed": failed,
        "in_progress": in_progress,
        "avg_battery": avg_battery,
    }


def _fmt_time(value: object) -> str:
    if value is None:
        return "-"
    text = str(value)
    if "T" in text:
        date_part, time_part = text.split("T", 1)
        return f"{date_part} {time_part[:8]}"
    return text


def _render_embedded_html(html: str, height: int = 700) -> None:
    if hasattr(st, "iframe"):
        st.iframe(html, width="stretch", height=height)
        return

    import streamlit.components.v1 as components

    components.html(html, height=height, scrolling=False)


def _build_road_rows(network: object, current_time) -> List[Dict[str, object]]:
    node_xy = {node_id: loc for node_id, loc in network.nodes}
    rows: List[Dict[str, object]] = []
    for edge_index, (n1, n2, attrs) in enumerate(network.graph.edges(data=True)):
        speed = network.effective_edge_speed_kmph(attrs, current_time)
        rows.append(
            {
                "道路": f"{n1} -> {n2}",
                "类型": attrs.get("road_type", "-"),
                "长度(km)": round(float(attrs.get("length_km", 0.0)), 2),
                "车道": int(attrs.get("lane_count", 0)),
                "限速(km/h)": round(float(attrs.get("speed_limit_kmph", 0.0)), 1),
                "当前有效速度": round(float(speed), 1),
                "路面质量": round(float(attrs.get("surface_quality", 0.0)), 2),
                "x1": round(float(node_xy[n1].x), 2),
                "y1": round(float(node_xy[n1].y), 2),
                "x2": round(float(node_xy[n2].x), 2),
                "y2": round(float(node_xy[n2].y), 2),
                "_id": f"road_{edge_index}",
            }
        )
    return rows


def _build_scene_payload(
    network: object,
    frames: List[SimulationFrame],
    road_rows: List[Dict[str, object]],
    stations: List[object] = None,
) -> Dict[str, object]:
    nodes = [
        {"id": node_id, "x": float(loc.x), "y": float(loc.y), "name": loc.name}
        for node_id, loc in network.nodes
    ]
    edges = []
    for edge_index, (n1, n2, attrs) in enumerate(network.graph.edges(data=True)):
        edges.append(
            {
                "id": f"road_{edge_index}",
                "from": n1,
                "to": n2,
                "road_type": attrs.get("road_type", "local"),
                "length_km": float(attrs.get("length_km", 0.0)),
                "lane_count": int(attrs.get("lane_count", 1)),
                "speed_limit_kmph": float(attrs.get("speed_limit_kmph", 0.0)),
                "surface_quality": float(attrs.get("surface_quality", 0.0)),
            }
        )

    scene_frames = []
    for frame in frames:
        task_details = frame.task_details or {}
        vehicle_details = frame.vehicle_details or {}
        station_details = frame.station_details or {}
        visible_tasks = [
            task
            for task in task_details.values()
            if task.get("status") in ("pending", "in_progress")
        ]

        if vehicle_details:
            vehicles = [
                {
                    **details,
                    "x": float(frame.vehicle_positions[vid][0]),
                    "y": float(frame.vehicle_positions[vid][1]),
                }
                for vid, details in vehicle_details.items()
                if vid in frame.vehicle_positions
            ]
        else:
            vehicles = [
                {
                    "id": vid,
                    "type": "-",
                    "status": "idle",
                    "phase": "snapshot",
                    "battery": float(frame.vehicle_battery.get(vid, 0.0)),
                    "battery_capacity": 100.0,
                    "battery_ratio": max(0.0, min(1.0, float(frame.vehicle_battery.get(vid, 0.0)) / 100.0)),
                    "current_load": 0.0,
                    "load_capacity": 0.0,
                    "current_volume": 0.0,
                    "volume_capacity": 0.0,
                    "current_tasks": [],
                    "supported_cargo_types": [],
                    "available_at": None,
                    "charging_station_id": None,
                    "route": [],
                    "x": float(pos[0]),
                    "y": float(pos[1]),
                }
                for vid, pos in frame.vehicle_positions.items()
            ]

        if station_details:
            scene_stations = list(station_details.values())
        else:
            scene_stations = [
                {
                    "id": station.id,
                    "position": {
                        "name": station.position.name,
                        "x": float(station.position.x),
                        "y": float(station.position.y),
                    },
                    "num_chargers": int(station.num_chargers),
                    "charging_power": float(station.charging_power),
                    "waiting_queue": list(station.waiting_queue),
                    "charging_vehicles": {},
                    "charging_now": 0,
                    "wait_time_minutes": 0.0,
                }
                for station in (stations or [])
            ]

        scene_frames.append(
            {
                "step": frame.step,
                "time": frame.current_time.isoformat(),
                "vehicles": vehicles,
                "tasks": visible_tasks,
                "stations": scene_stations,
                "counts": {
                    "pending": len(frame.pending_task_ids),
                    "completed": len(frame.completed_task_ids),
                    "failed": len(frame.failed_task_ids),
                    "in_progress": sum(1 for task in task_details.values() if task.get("status") == "in_progress"),
                },
            }
        )

    return {
        "width": float(network.width),
        "height": float(network.height),
        "nodes": nodes,
        "edges": edges,
        "frames": scene_frames,
        "roads": road_rows,
    }


def _render_game_scene(payload: Dict[str, object], autoplay: bool, fps: int, focus_id: str) -> None:
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
    focus_json = json.dumps(focus_id, ensure_ascii=False)
    autoplay_json = "true" if autoplay else "false"
    frame_count = len(payload.get("frames", []))
    first_vehicle_count = len(payload.get("frames", [{}])[0].get("vehicles", [])) if frame_count else 0
    progress_max = max(0, frame_count - 1)
    initial_frame_label = f"1 / {frame_count}" if frame_count else "0 / 0"

    _render_embedded_html(
        f"""
<div id="ev-game-root">
  <style>
    #ev-game-root {{
      background: #101820;
      border: 1px solid #243345;
      border-radius: 8px;
      color: #e7eef7;
      font-family: Inter, "Segoe UI", Arial, sans-serif;
      overflow: hidden;
    }}
    #ev-stage-wrap {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      min-height: 620px;
    }}
    #ev-canvas-wrap {{
      position: relative;
      min-height: 620px;
      background:
        linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px),
        radial-gradient(circle at 30% 25%, rgba(52, 211, 153, 0.12), transparent 32%),
        #0d141c;
      background-size: 34px 34px, 34px 34px, 100% 100%, 100% 100%;
    }}
    #ev-canvas {{
      width: 100%;
      height: 620px;
      display: block;
      cursor: crosshair;
    }}
    #ev-dom-layer {{
      position: absolute;
      inset: 0;
      pointer-events: none;
      z-index: 2;
    }}
    .ev-dom-vehicle {{
      position: absolute;
      width: 24px;
      height: 16px;
      margin-left: -12px;
      margin-top: -8px;
      border: 2px solid #020617;
      border-radius: 5px;
      box-shadow: 0 0 12px currentColor;
      pointer-events: auto;
      cursor: pointer;
      transform: translate3d(0, 0, 0);
    }}
    .ev-dom-vehicle::after {{
      content: attr(data-id);
      position: absolute;
      left: 26px;
      top: -9px;
      color: currentColor;
      background: rgba(2, 6, 23, 0.72);
      padding: 1px 4px;
      border-radius: 3px;
      font-size: 11px;
      white-space: nowrap;
    }}
    #ev-hud {{
      position: absolute;
      left: 16px;
      top: 14px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      pointer-events: none;
    }}
    .ev-pill {{
      background: rgba(13, 20, 28, 0.78);
      border: 1px solid rgba(148, 163, 184, 0.28);
      border-radius: 8px;
      padding: 7px 10px;
      font-size: 12px;
      line-height: 1.2;
      backdrop-filter: blur(8px);
    }}
    #ev-error {{
      display: none;
      position: absolute;
      left: 16px;
      right: 16px;
      top: 58px;
      padding: 10px 12px;
      border: 1px solid rgba(248, 113, 113, 0.55);
      border-radius: 8px;
      background: rgba(127, 29, 29, 0.86);
      color: #fee2e2;
      font-size: 12px;
      z-index: 4;
      overflow-wrap: anywhere;
    }}
    #ev-controls {{
      position: absolute;
      left: 16px;
      right: 16px;
      bottom: 14px;
      display: grid;
      grid-template-columns: 44px 44px minmax(120px, 1fr) 72px;
      align-items: center;
      gap: 10px;
      background: rgba(13, 20, 28, 0.82);
      border: 1px solid rgba(148, 163, 184, 0.28);
      border-radius: 8px;
      padding: 10px;
      backdrop-filter: blur(8px);
    }}
    .ev-control-btn {{
      width: 34px;
      height: 34px;
      border: 1px solid rgba(203, 213, 225, 0.35);
      border-radius: 8px;
      background: #1f2937;
      color: #f8fafc;
      cursor: pointer;
      font-size: 15px;
      line-height: 1;
    }}
    .ev-control-btn:hover {{
      background: #334155;
    }}
    #ev-progress {{
      width: 100%;
      accent-color: #38bdf8;
      cursor: pointer;
    }}
    #ev-frame-label {{
      color: #cbd5e1;
      font-size: 12px;
      text-align: right;
      white-space: nowrap;
    }}
    #ev-panel {{
      border-left: 1px solid #243345;
      background: #121c27;
      padding: 14px;
      overflow: auto;
    }}
    #ev-panel h3 {{
      margin: 0 0 10px;
      font-size: 16px;
      color: #f8fafc;
    }}
    #ev-panel .muted {{
      color: #9fb0c3;
      font-size: 12px;
      line-height: 1.5;
    }}
    #ev-panel .kv {{
      display: grid;
      grid-template-columns: 108px minmax(0, 1fr);
      gap: 7px;
      font-size: 12px;
      margin: 8px 0;
    }}
    #ev-panel .kv span:nth-child(odd) {{
      color: #9fb0c3;
    }}
    #ev-panel .kv span:nth-child(even) {{
      color: #f8fafc;
      overflow-wrap: anywhere;
    }}
    #ev-panel .bar {{
      height: 7px;
      border-radius: 999px;
      background: #233142;
      overflow: hidden;
      margin: 7px 0 12px;
    }}
    #ev-panel .bar > div {{
      height: 100%;
      background: linear-gradient(90deg, #ef4444, #f59e0b, #22c55e);
    }}
    @media (max-width: 900px) {{
      #ev-stage-wrap {{ grid-template-columns: 1fr; }}
      #ev-panel {{ border-left: 0; border-top: 1px solid #243345; }}
    }}
  </style>
  <div id="ev-stage-wrap">
    <div id="ev-canvas-wrap">
      <canvas id="ev-canvas"></canvas>
      <div id="ev-dom-layer"></div>
      <div id="ev-hud">
        <div class="ev-pill" id="ev-time">time</div>
        <div class="ev-pill" id="ev-counts">counts</div>
        <div class="ev-pill" id="ev-selected">selected -</div>
        <div class="ev-pill">Click objects for details</div>
        <div class="ev-pill">data {frame_count} frames / {first_vehicle_count} vehicles</div>
      </div>
      <div id="ev-error"></div>
      <div id="ev-controls">
        <button id="ev-play" class="ev-control-btn" title="Play or pause">▶</button>
        <button id="ev-reset" class="ev-control-btn" title="Restart">↺</button>
        <input id="ev-progress" type="range" min="0" max="{progress_max}" step="0.01" value="0" aria-label="Animation progress" />
        <div id="ev-frame-label">{initial_frame_label}</div>
      </div>
    </div>
    <aside id="ev-panel">
      <h3>对象详情</h3>
      <div class="muted">画面会在相邻帧之间做连续插值；车辆的发光轨迹表示接下来要走的路。</div>
      <div id="ev-detail" class="muted" style="margin-top:12px;">尚未选中对象。</div>
    </aside>
  </div>
  <script>
  (() => {{
    const payloadText = new TextDecoder("utf-8").decode(
      Uint8Array.from(atob("{payload_b64}"), c => c.charCodeAt(0))
    );
    const payload = JSON.parse(payloadText);
    const canvas = document.getElementById("ev-canvas");
    const ctx = canvas.getContext("2d");
    const domLayer = document.getElementById("ev-dom-layer");
    const detail = document.getElementById("ev-detail");
    const timeBox = document.getElementById("ev-time");
    const countsBox = document.getElementById("ev-counts");
    const selectedBox = document.getElementById("ev-selected");
    const errorBox = document.getElementById("ev-error");
    const playButton = document.getElementById("ev-play");
    const resetButton = document.getElementById("ev-reset");
    const progress = document.getElementById("ev-progress");
    const frameLabel = document.getElementById("ev-frame-label");
    let isPlaying = {autoplay_json};
    const vehicleSpeed = Math.max(0.25, Number({int(fps)}));
    let selectedId = {focus_json} || "";
    let selectedKind = selectedId ? "vehicle" : "";
    let currentFrameIndex = 0;
    let lastTick = performance.now();
    let hitTargets = [];
    let isScrubbing = false;
    const vehicleDomNodes = new Map();
    const vehicleVisualStates = new Map();
    let lastDrawDelta = 1 / 60;

    const nodes = new Map(payload.nodes.map(n => [n.id, n]));
    const roadColors = {{ arterial: "#65a30d", collector: "#0891b2", local: "#64748b" }};
    const vehicleColors = {{ idle: "#38bdf8", en_route: "#fbbf24", occupied: "#f59e0b", charging: "#22c55e", maintenance: "#ef4444" }};
    const taskColors = {{ pending: "#f97316", in_progress: "#a78bfa", completed: "#22c55e", failed: "#ef4444" }};
    const emptyFrame = {{
      vehicles: [],
      tasks: [],
      stations: [],
      counts: {{ pending: 0, in_progress: 0, completed: 0, failed: 0 }},
      step: 0,
      time: new Date().toISOString()
    }};

    function showError(error) {{
      errorBox.style.display = "block";
      errorBox.textContent = `Visualization error: ${{error && error.message ? error.message : error}}`;
    }}

    function clearError() {{
      errorBox.style.display = "none";
      errorBox.textContent = "";
    }}

    window.addEventListener("error", (event) => showError(event.error || event.message));

    function resize() {{
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(300, Math.floor(rect.width * dpr));
      canvas.height = Math.max(420, Math.floor(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }}

    function scale() {{
      const rect = canvas.getBoundingClientRect();
      const pad = 38;
      const sx = (rect.width - pad * 2) / Math.max(1, payload.width);
      const sy = (rect.height - pad * 2) / Math.max(1, payload.height);
      const s = Math.min(sx, sy);
      const ox = (rect.width - payload.width * s) / 2;
      const oy = (rect.height - payload.height * s) / 2;
      return {{ s, ox, oy, h: rect.height }};
    }}

    function pt(loc, sc) {{
      return {{ x: sc.ox + loc.x * sc.s, y: sc.oy + (payload.height - loc.y) * sc.s }};
    }}

    function lerp(a, b, t) {{ return a + (b - a) * t; }}

    function distance(a, b) {{
      return Math.hypot((a.x || 0) - (b.x || 0), (a.y || 0) - (b.y || 0));
    }}

    function pointAtRouteDistance(route, targetDistance) {{
      if (!Array.isArray(route) || route.length === 0) return null;
      if (route.length === 1) return {{ x: route[0].x, y: route[0].y }};

      let covered = 0;
      for (let i = 0; i < route.length - 1; i += 1) {{
        const a = route[i];
        const b = route[i + 1];
        const len = distance(a, b);
        if (len <= 1e-9) continue;
        if (covered + len >= targetDistance) {{
          const ratio = Math.max(0, Math.min(1, (targetDistance - covered) / len));
          return {{ x: lerp(a.x || 0, b.x || 0, ratio), y: lerp(a.y || 0, b.y || 0, ratio) }};
        }}
        covered += len;
      }}

      const last = route[route.length - 1];
      return {{ x: last.x, y: last.y }};
    }}

    function closestRouteProgress(route, loc) {{
      if (!Array.isArray(route) || route.length < 2 || !loc) return null;
      let covered = 0;
      let best = {{ progress: 0, distance: Infinity }};
      const px = Number(loc.x);
      const py = Number(loc.y);
      if (!Number.isFinite(px) || !Number.isFinite(py)) return null;

      for (let i = 0; i < route.length - 1; i += 1) {{
        const a = route[i];
        const b = route[i + 1];
        const ax = Number(a.x || 0);
        const ay = Number(a.y || 0);
        const bx = Number(b.x || 0);
        const by = Number(b.y || 0);
        const dx = bx - ax;
        const dy = by - ay;
        const len = Math.hypot(dx, dy);
        if (len <= 1e-9) continue;
        const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (len * len)));
        const qx = ax + dx * t;
        const qy = ay + dy * t;
        const d = Math.hypot(px - qx, py - qy);
        if (d < best.distance) best = {{ progress: covered + len * t, distance: d }};
        covered += len;
      }}

      return best;
    }}

    function sameRoute(a, b) {{
      const routeA = Array.isArray(a) ? a : [];
      const routeB = Array.isArray(b) ? b : [];
      if (routeA.length !== routeB.length || routeA.length < 2) return false;
      for (let i = 0; i < routeA.length; i += 1) {{
        const pa = routeA[i];
        const pb = routeB[i];
        if (Math.abs((pa.x || 0) - (pb.x || 0)) > 1e-6 || Math.abs((pa.y || 0) - (pb.y || 0)) > 1e-6) return false;
      }}
      return true;
    }}

    function routeTarget(current, next) {{
      if (!current || !next) return null;
      const route = (Array.isArray(next.route) && next.route.length >= 2) ? next.route : current.route;
      if (!Array.isArray(route) || route.length < 2) return null;

      let startProgress = Number(current.route_progress);
      const endProgress = Number(next.route_progress);
      if (!Number.isFinite(endProgress)) return null;
      if (!sameRoute(route, current.route || [])) {{
        const projected = closestRouteProgress(route, current);
        if (!projected || projected.distance > 0.5) return null;
        startProgress = projected.progress;
      }}
      if (!Number.isFinite(startProgress)) return null;
      if (endProgress < startProgress - 1e-6) return null;

      return {{
        route,
        startProgress,
        endProgress,
        point: pointAtRouteDistance(route, endProgress),
      }};
    }}

    function interpolateAlongRoute(a, b, t) {{
      if (!a || !b) return null;
      if (a.status !== b.status) return null;
      const aTask = (a.current_tasks || [])[0] || "";
      const bTask = (b.current_tasks || [])[0] || "";
      if (aTask !== bTask) return null;
      const route = (Array.isArray(a.route) && a.route.length >= 2) ? a.route : [];
      if (route.length < 2) return null;

      const startDistance = Number(a.route_progress);
      const endDistance = Number(b.route_progress);
      if (!Number.isFinite(startDistance) || !Number.isFinite(endDistance)) return null;
      if (endDistance < startDistance - 1e-6) {{
        return null;
      }}

      return pointAtRouteDistance(route, lerp(startDistance, endDistance, t));
    }}

    function interpolateVehicle(a, b, t) {{
      if (!b || a.id !== b.id) return a;
      if (t <= 1e-4) return {{ ...a }};
      if (t >= 0.9999) return {{ ...b }};
      const routedPoint = interpolateAlongRoute(a, b, t);
      if (routedPoint) return {{ ...a, ...b, route: a.route || b.route || [], x: routedPoint.x, y: routedPoint.y }};
      if (a.status !== b.status || a.phase !== b.phase) return {{ ...a }};
      return {{ ...a }};
    }}

    function currentFramePair() {{
      const frames = Array.isArray(payload.frames) ? payload.frames : [];
      if (frames.length === 0) return [emptyFrame, emptyFrame, 0, 0];

      const maxIndex = frames.length - 1;
      const i = Math.max(0, Math.min(maxIndex, Math.floor(currentFrameIndex)));
      const j = Math.max(0, Math.min(maxIndex, i + 1));
      return [frames[i] || emptyFrame, frames[j] || frames[i] || emptyFrame, 0, i];
    }}

    function drawRoads(sc) {{
      for (const e of payload.edges) {{
        const a = nodes.get(e.from);
        const b = nodes.get(e.to);
        if (!a || !b) continue;
        const p1 = pt(a, sc);
        const p2 = pt(b, sc);
        ctx.strokeStyle = selectedId === e.id ? "#f8fafc" : (roadColors[e.road_type] || "#64748b");
        ctx.globalAlpha = selectedId === e.id ? 0.95 : 0.62;
        ctx.lineWidth = (e.lane_count || 1) * (selectedId === e.id ? 1.9 : 1.15);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
        hitTargets.push({{ id: e.id, kind: "road", x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, data: e }});
      }}
      ctx.globalAlpha = 1;
    }}

    function drawNodes(sc) {{
      ctx.fillStyle = "#cbd5e1";
      for (const n of payload.nodes) {{
        const p = pt(n, sc);
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.6, 0, Math.PI * 2);
        ctx.fill();
      }}
    }}

    function drawRoute(route, color, sc) {{
      if (!route || route.length < 2) return;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 12;
      ctx.lineWidth = 4;
      ctx.globalAlpha = 0.78;
      ctx.beginPath();
      route.forEach((loc, idx) => {{
        const p = pt(loc, sc);
        if (idx === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      }});
      ctx.stroke();
      ctx.restore();
    }}

    function drawStations(stations, sc) {{
      for (const s of (stations || [])) {{
        if (!s || !s.position) continue;
        const p = pt(s.position, sc);
        const selected = selectedId === s.id;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.fillStyle = selected ? "#fef08a" : "#fde047";
        ctx.strokeStyle = "#0f172a";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(0, -14);
        ctx.lineTo(13, 10);
        ctx.lineTo(-13, 10);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = "#0f172a";
        ctx.font = "bold 12px Segoe UI";
        ctx.textAlign = "center";
        ctx.fillText("EV", 0, 5);
        ctx.restore();
        label(s.id, p.x + 14, p.y - 10, "#fde047");
        hitTargets.push({{ id: s.id, kind: "station", x: p.x, y: p.y, r: 18, data: s }});
      }}
    }}

    function drawTasks(tasks, sc) {{
      for (const task of (tasks || [])) {{
        const o = task.origin ? pt(task.origin, sc) : null;
        const d = task.destination ? pt(task.destination, sc) : null;
        if (!o || !d) continue;
        const color = taskColors[task.status] || "#fb923c";
        if (selectedId === task.id) {{
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.setLineDash([7, 5]);
          ctx.beginPath();
          ctx.moveTo(o.x, o.y);
          ctx.lineTo(d.x, d.y);
          ctx.stroke();
          ctx.setLineDash([]);
        }}
        marker(o.x, o.y, 8, color, selectedId === task.id ? "#f8fafc" : "#111827");
        marker(d.x, d.y, 6, "#94a3b8", "#111827");
        label(task.id, o.x + 10, o.y + 4, color);
        hitTargets.push({{ id: task.id, kind: "task", x: o.x, y: o.y, r: 12, data: task }});
      }}
    }}

    function drawVehicles(vehicles, nextVehicles, t, sc) {{
      const nextById = new Map((nextVehicles || []).map(v => [v.id, v]));
      const currentById = new Map((vehicles || []).map(v => [v.id, v]));
      const vehicleIds = new Set([...currentById.keys(), ...nextById.keys()]);
      const visibleIds = new Set();
      let allArrived = true;
      for (const vehicleId of vehicleIds) {{
        const raw = currentById.get(vehicleId) || nextById.get(vehicleId);
        if (!Number.isFinite(raw.x) || !Number.isFinite(raw.y)) continue;
        const next = nextById.get(raw.id);
        const routeMove = isPlaying && next ? routeTarget(raw, next) : null;
        const v = routeMove ? raw : ((isPlaying && next) ? next : raw);
        const targetLoc = routeMove && routeMove.point ? routeMove.point : v;
        const target = pt(targetLoc, sc);
        const color = vehicleColors[v.status] || "#38bdf8";
        visibleIds.add(v.id);
        let visual = vehicleVisualStates.get(v.id);
        if (!visual) {{
          visual = {{
            x: Number(raw.x),
            y: Number(raw.y),
            routeProgress: Number(raw.route_progress),
            route: raw.route,
          }};
        }} else {{
          const worldStep = vehicleSpeed * Math.max(0.016, lastDrawDelta);
          if (routeMove) {{
            const currentProgress = (sameRoute(visual.route, routeMove.route) && Number.isFinite(visual.routeProgress))
              ? Math.max(routeMove.startProgress, Math.min(routeMove.endProgress, Number(visual.routeProgress)))
              : routeMove.startProgress;
            const nextProgress = Math.min(routeMove.endProgress, currentProgress + worldStep);
            const routePoint = pointAtRouteDistance(routeMove.route, nextProgress) || targetLoc;
            visual = {{ x: routePoint.x, y: routePoint.y, routeProgress: nextProgress, route: routeMove.route }};
            if (Math.abs(routeMove.endProgress - nextProgress) > 1e-4) allArrived = false;
          }} else {{
            const dx = targetLoc.x - visual.x;
            const dy = targetLoc.y - visual.y;
            const dist = Math.hypot(dx, dy);
            if (dist <= worldStep || dist <= 0.001) {{
              visual = {{ x: targetLoc.x, y: targetLoc.y, routeProgress: Number(v.route_progress), route: v.route }};
            }} else {{
              const ratio = worldStep / dist;
              visual = {{
                x: visual.x + dx * ratio,
                y: visual.y + dy * ratio,
                routeProgress: Number.isFinite(Number(v.route_progress)) ? Number(v.route_progress) : visual.routeProgress,
                route: v.route,
              }};
              allArrived = false;
            }}
          }}
        }}
        if (Math.hypot(targetLoc.x - visual.x, targetLoc.y - visual.y) > 0.02) allArrived = false;
        vehicleVisualStates.set(v.id, visual);
        const p = pt(visual, sc);
        let domVehicle = vehicleDomNodes.get(v.id);
        if (!domVehicle) {{
          domVehicle = document.createElement("div");
          domVehicle.className = "ev-dom-vehicle";
          domVehicle.dataset.id = v.id;
          domVehicle.addEventListener("click", (event) => {{
            event.stopPropagation();
            selectedId = domVehicle.dataset.id;
            selectedKind = "vehicle";
            updateSelectedFromId();
          }});
          domLayer.appendChild(domVehicle);
          vehicleDomNodes.set(v.id, domVehicle);
        }}
        domVehicle.style.display = "block";
        domVehicle.style.left = `${{p.x}}px`;
        domVehicle.style.top = `${{p.y}}px`;
        domVehicle.style.color = color;
        domVehicle.style.background = color;
        domVehicle.style.borderColor = selectedId === v.id ? "#f8fafc" : "#020617";
        if (selectedId === v.id) drawRoute(v.route, color, sc);
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.shadowColor = color;
        ctx.shadowBlur = selectedId === v.id ? 20 : 8;
        ctx.fillStyle = color;
        ctx.strokeStyle = selectedId === v.id ? "#f8fafc" : "#020617";
        ctx.lineWidth = selectedId === v.id ? 3 : 2;
        ctx.beginPath();
        roundedRect(-12, -8, 24, 16, 5);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = "rgba(15, 23, 42, 0.9)";
        ctx.fillRect(-10, 10, 20, 4);
        ctx.fillStyle = v.battery_ratio < 0.25 ? "#ef4444" : (v.battery_ratio < 0.55 ? "#f59e0b" : "#22c55e");
        ctx.fillRect(-10, 10, 20 * Math.max(0, Math.min(1, v.battery_ratio || 0)), 4);
        ctx.restore();
        hitTargets.push({{ id: v.id, kind: "vehicle", x: p.x, y: p.y, r: 17, data: v }});
      }}
      for (const [id, node] of vehicleDomNodes.entries()) {{
        if (!visibleIds.has(id)) node.style.display = "none";
      }}
      for (const id of Array.from(vehicleVisualStates.keys())) {{
        if (!visibleIds.has(id)) vehicleVisualStates.delete(id);
      }}
      return allArrived;
    }}

    function marker(x, y, r, fill, stroke) {{
      ctx.fillStyle = fill;
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }}

    function roundedRect(x, y, w, h, r) {{
      const radius = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
      ctx.moveTo(x + radius, y);
      ctx.lineTo(x + w - radius, y);
      ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
      ctx.lineTo(x + w, y + h - radius);
      ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
      ctx.lineTo(x + radius, y + h);
      ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
      ctx.lineTo(x, y + radius);
      ctx.quadraticCurveTo(x, y, x + radius, y);
      ctx.closePath();
    }}

    function label(text, x, y, color) {{
      ctx.font = "12px Segoe UI";
      ctx.fillStyle = "rgba(2, 6, 23, 0.72)";
      const w = ctx.measureText(text).width + 8;
      ctx.fillRect(x - 4, y - 12, w, 16);
      ctx.fillStyle = color;
      ctx.fillText(text, x, y);
    }}

    function detailHtml(kind, data) {{
      const pct = Math.round((data.battery_ratio || 0) * 100);
      if (kind === "vehicle") return `
        <h3>${{data.id}}</h3>
        <div class="bar"><div style="width:${{pct}}%"></div></div>
        <div class="kv">
          <span>状态</span><span>${{data.status}} / ${{data.phase}}</span>
          <span>电量</span><span>${{(data.battery || 0).toFixed(1)}} / ${{(data.battery_capacity || 0).toFixed(1)}} kWh (${{pct}}%)</span>
          <span>位置</span><span>(${{(data.x || 0).toFixed(2)}}, ${{(data.y || 0).toFixed(2)}})</span>
          <span>车型</span><span>${{data.type}}</span>
          <span>载重</span><span>${{(data.current_load || 0).toFixed(1)}} / ${{(data.load_capacity || 0).toFixed(1)}} kg</span>
          <span>体积</span><span>${{(data.current_volume || 0).toFixed(2)}} / ${{(data.volume_capacity || 0).toFixed(2)}} m3</span>
          <span>当前任务</span><span>${{(data.current_tasks || []).join(", ") || "-"}}</span>
          <span>可载货物</span><span>${{(data.supported_cargo_types || []).join(", ")}}</span>
          <span>可用时间</span><span>${{data.available_at || "-"}}</span>
          <span>充电站</span><span>${{data.charging_station_id || "-"}}</span>
        </div>`;
      if (kind === "task") return `
        <h3>${{data.id}}</h3>
        <div class="kv">
          <span>状态</span><span>${{data.status}}</span>
          <span>货物类型</span><span>${{data.cargo_type}}</span>
          <span>重量/体积</span><span>${{(data.weight || 0).toFixed(1)}} kg / ${{(data.volume || 0).toFixed(2)}} m3</span>
          <span>车辆</span><span>${{(data.assigned_vehicles || []).join(", ") || "-"}}</span>
          <span>起点</span><span>${{data.origin?.name || "-"}}</span>
          <span>终点</span><span>${{data.destination?.name || "-"}}</span>
          <span>截止</span><span>${{data.deadline || "-"}}</span>
          <span>预计完成</span><span>${{data.planned_completion_time || "-"}}</span>
          <span>里程/成本</span><span>${{(data.planned_distance || 0).toFixed(2)}} km / ${{(data.planned_cost || 0).toFixed(2)}}</span>
          <span>失败原因</span><span>${{data.failure_reason || "-"}}</span>
        </div>`;
      if (kind === "station") return `
        <h3>${{data.id}}</h3>
        <div class="kv">
          <span>充电桩</span><span>${{data.charging_now || 0}} / ${{data.num_chargers || 0}} 使用中</span>
          <span>可用桩</span><span>${{data.chargers_available ?? "-"}}</span>
          <span>总队列</span><span>${{data.total_queue_length ?? ((data.waiting_queue || []).length + Object.keys(data.charging_vehicles || {{}}).length)}}</span>
          <span>功率</span><span>${{data.charging_power || 0}} kWh/h</span>
          <span>排队车辆</span><span>${{(data.waiting_queue || []).join(", ") || "-"}}</span>
          <span>充电车辆</span><span>${{Object.keys(data.charging_vehicles || {{}}).join(", ") || "-"}}</span>
          <span>预计等待</span><span>${{(data.wait_time_minutes || 0).toFixed(1)}} min</span>
        </div>`;
      if (kind === "road") return `
        <h3>${{data.id}}</h3>
        <div class="kv">
          <span>道路类型</span><span>${{data.road_type}}</span>
          <span>长度</span><span>${{(data.length_km || 0).toFixed(2)}} km</span>
          <span>车道</span><span>${{data.lane_count}}</span>
          <span>限速</span><span>${{(data.speed_limit_kmph || 0).toFixed(1)}} km/h</span>
          <span>路面质量</span><span>${{(data.surface_quality || 0).toFixed(2)}}</span>
        </div>`;
      return '<div class="muted">尚未选中对象。</div>';
    }}

    function updateSelectedFromId() {{
      const [a] = currentFramePair();
      if (!selectedId) {{
        selectedBox.textContent = "selected -";
        return;
      }}
      const vehicles = a.vehicles || [];
      const tasks = a.tasks || [];
      const stations = a.stations || [];
      if (selectedKind === "vehicle") {{
        const found = vehicles.find(v => v.id === selectedId);
        if (found) {{
          selectedBox.textContent = `selected ${{found.id}} · ${{found.status}} · ${{Math.round((found.battery_ratio || 0) * 100)}}%`;
          detail.innerHTML = detailHtml("vehicle", found);
          return;
        }}
      }}
      if (selectedKind === "task") {{
        const found = tasks.find(t => t.id === selectedId);
        if (found) {{
          selectedBox.textContent = `selected ${{found.id}} · ${{found.status}}`;
          detail.innerHTML = detailHtml("task", found);
          return;
        }}
      }}
      if (selectedKind === "station") {{
        const found = stations.find(s => s.id === selectedId);
        if (found) {{
          const queueLength = found.total_queue_length ?? ((found.waiting_queue || []).length + Object.keys(found.charging_vehicles || {{}}).length);
          selectedBox.textContent = `selected ${{found.id}} · charging ${{found.charging_now || 0}}/${{found.num_chargers || 0}} · queue ${{queueLength}}`;
          detail.innerHTML = detailHtml("station", found);
          return;
        }}
      }}
      if (selectedKind === "road") {{
        const found = (payload.edges || []).find(e => e.id === selectedId);
        if (found) {{
          selectedBox.textContent = `selected ${{found.id}} · ${{found.road_type}}`;
          detail.innerHTML = detailHtml("road", found);
          return;
        }}
      }}
      for (const v of vehicles) if (v.id === selectedId) {{ selectedKind = "vehicle"; selectedBox.textContent = `selected ${{v.id}} · ${{v.status}}`; detail.innerHTML = detailHtml("vehicle", v); return; }}
      for (const t of tasks) if (t.id === selectedId) {{ selectedKind = "task"; selectedBox.textContent = `selected ${{t.id}} · ${{t.status}}`; detail.innerHTML = detailHtml("task", t); return; }}
      for (const s of stations) if (s.id === selectedId) {{
        selectedKind = "station";
        const queueLength = s.total_queue_length ?? ((s.waiting_queue || []).length + Object.keys(s.charging_vehicles || {{}}).length);
        selectedBox.textContent = `selected ${{s.id}} · charging ${{s.charging_now || 0}}/${{s.num_chargers || 0}} · queue ${{queueLength}}`;
        detail.innerHTML = detailHtml("station", s);
        return;
      }}
      for (const e of (payload.edges || [])) if (e.id === selectedId) {{ selectedKind = "road"; selectedBox.textContent = `selected ${{e.id}} · ${{e.road_type}}`; detail.innerHTML = detailHtml("road", e); return; }}
    }}

    function distToSegment(px, py, x1, y1, x2, y2) {{
      const dx = x2 - x1, dy = y2 - y1;
      const l2 = dx * dx + dy * dy;
      if (l2 === 0) return Math.hypot(px - x1, py - y1);
      const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / l2));
      return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
    }}

    function hitPriority(kind) {{
      if (kind === "vehicle") return 4;
      if (kind === "task") return 3;
      if (kind === "station") return 2;
      return 1;
    }}

    canvas.addEventListener("click", (event) => {{
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      let best = null;
      for (const h of hitTargets) {{
        const d = h.kind === "road" ? distToSegment(x, y, h.x1, h.y1, h.x2, h.y2) : Math.hypot(x - h.x, y - h.y);
        const limit = h.kind === "road" ? 6 : h.r;
        const priority = hitPriority(h.kind);
        if (d <= limit && (!best || priority > best.priority || (priority === best.priority && d < best.d))) best = {{ ...h, d, priority }};
      }}
      if (best) {{
        selectedId = best.id;
        selectedKind = best.kind;
        detail.innerHTML = detailHtml(best.kind, best.data);
      }}
    }});

    function setPlaying(next) {{
      isPlaying = Boolean(next);
      playButton.textContent = isPlaying ? "Ⅱ" : "▶";
      playButton.title = isPlaying ? "Pause" : "Play";
      lastTick = performance.now();
    }}

    function snapVehiclesToCurrentFrame() {{
      const [frame] = currentFramePair();
      const sc = scale();
      vehicleVisualStates.clear();
      for (const vehicle of (frame.vehicles || [])) {{
        if (!Number.isFinite(vehicle.x) || !Number.isFinite(vehicle.y)) continue;
        vehicleVisualStates.set(vehicle.id, {{
          x: Number(vehicle.x),
          y: Number(vehicle.y),
          routeProgress: Number(vehicle.route_progress),
          route: vehicle.route,
        }});
      }}
    }}

    playButton.addEventListener("click", () => setPlaying(!isPlaying));
    resetButton.addEventListener("click", () => {{
      currentFrameIndex = 0;
      progress.value = "0";
      snapVehiclesToCurrentFrame();
      lastTick = performance.now();
    }});
    progress.max = String(Math.max(0, (payload.frames || []).length - 1));
    progress.addEventListener("input", () => {{
      isScrubbing = true;
      currentFrameIndex = Math.floor(Math.max(0, Math.min(Math.max(0, (payload.frames || []).length - 1), Number(progress.value) || 0)));
      snapVehiclesToCurrentFrame();
      setPlaying(false);
    }});
    progress.addEventListener("change", () => {{
      isScrubbing = false;
    }});
    setPlaying(isPlaying);

    function draw(now) {{
      try {{
        resize();
        const maxFrame = Math.max(0, (payload.frames || []).length - 1);
        clearError();
        lastDrawDelta = Math.min(0.12, Math.max(0.001, (now - lastTick) / 1000));
        lastTick = now;
        const [a, b, t, idx] = currentFramePair();
        const vehicles = a.vehicles || [];
        const nextVehicles = b.vehicles || vehicles;
        const tasks = a.tasks || [];
        const stations = a.stations || [];
        const counts = a.counts || emptyFrame.counts;
        const rect = canvas.getBoundingClientRect();
        ctx.clearRect(0, 0, rect.width, rect.height);
        hitTargets = [];
        const sc = scale();
        drawRoads(sc);
        drawNodes(sc);
        drawTasks(tasks, sc);
        drawStations(stations, sc);
        const arrived = drawVehicles(vehicles, nextVehicles, t, sc);
        if (isPlaying && maxFrame > 0 && arrived) {{
          currentFrameIndex = currentFrameIndex >= maxFrame ? 0 : currentFrameIndex + 1;
        }}
        if (!isScrubbing) progress.value = String(currentFrameIndex);
        frameLabel.textContent = `${{idx + 1}} / ${{(payload.frames || []).length}}`;
        timeBox.textContent = `step ${{a.step}} · ${{new Date(a.time).toLocaleString()}} · frame ${{idx + 1}}/${{(payload.frames || []).length}}`;
        countsBox.textContent = `pending ${{counts.pending}} · moving ${{counts.in_progress}} · done ${{counts.completed}} · failed ${{counts.failed}}`;
        updateSelectedFromId();
      }} catch (error) {{
        setPlaying(false);
        showError(error);
      }}
      requestAnimationFrame(draw);
    }}

    window.addEventListener("resize", resize);
    requestAnimationFrame(draw);
  }})();
  </script>
</div>
        """,
        height=700,
    )


def _vehicle_rows(frame: SimulationFrame) -> List[Dict[str, object]]:
    rows = []
    for vid, details in (frame.vehicle_details or {}).items():
        rows.append(
            {
                "车辆": vid,
                "车型": details.get("type"),
                "状态": details.get("status"),
                "阶段": details.get("phase"),
                "电量(kWh)": round(float(details.get("battery", 0.0)), 1),
                "电量%": round(float(details.get("battery_ratio", 0.0)) * 100, 1),
                "载重(kg)": round(float(details.get("current_load", 0.0)), 1),
                "载重上限": round(float(details.get("load_capacity", 0.0)), 1),
                "任务": ", ".join(details.get("current_tasks", [])) or "-",
                "下一可用": _fmt_time(details.get("available_at")),
            }
        )
    return rows


def _task_rows(frame: SimulationFrame) -> List[Dict[str, object]]:
    rows = []
    for tid, task in (frame.task_details or {}).items():
        rows.append(
            {
                "任务": tid,
                "状态": task.get("status"),
                "货物": task.get("cargo_type"),
                "重量(kg)": round(float(task.get("weight", 0.0)), 1),
                "体积(m3)": round(float(task.get("volume", 0.0)), 2),
                "车辆": ", ".join(task.get("assigned_vehicles", [])) or "-",
                "起点": (task.get("origin") or {}).get("name"),
                "终点": (task.get("destination") or {}).get("name"),
                "截止": _fmt_time(task.get("deadline")),
                "预计完成": _fmt_time(task.get("planned_completion_time")),
                "计划里程": round(float(task.get("planned_distance", 0.0)), 2),
                "失败原因": task.get("failure_reason") or "-",
            }
        )
    return rows


def _station_rows(frame: SimulationFrame) -> List[Dict[str, object]]:
    rows = []
    for sid, station in (frame.station_details or {}).items():
        rows.append(
            {
                "充电站": sid,
                "充电桩": station.get("num_chargers"),
                "功率(kWh/h)": station.get("charging_power"),
                "充电中": ", ".join((station.get("charging_vehicles") or {}).keys()) or "-",
                "等待队列": ", ".join(station.get("waiting_queue") or []) or "-",
                "可用桩": station.get("chargers_available"),
                "预计等待(min)": round(float(station.get("wait_time_minutes", 0.0)), 1),
            }
        )
    return rows


def main() -> None:
    st.set_page_config(page_title="EV Dispatch Dashboard", layout="wide")
    st.title("EV 动态调度可视化")
    st.caption("连续动画、对象选中详情、道路/车辆/任务/充电站全量状态面板。")

    with st.sidebar:
        st.header("运行参数")
        scenario_name = st.selectbox("场景规模", list(SCENARIO_PRESETS.keys()), index=1)
        algo_mode = st.selectbox(
            "算法选择",
            ["仅最近优先", "仅最大优先", "仅综合评分", "三策略对比"],
            index=3,
        )
        num_steps = st.slider("仿真步数", min_value=5, max_value=200, value=40, step=5)
        tasks_per_step = st.slider("每步任务数", min_value=1, max_value=8, value=3, step=1)
        random_seed = st.number_input("随机种子", min_value=0, max_value=999999, value=42)
        composite_preset = st.selectbox(
            "综合评分预设",
            list(COMPOSITE_CONFIG_PRESETS.keys()),
            index=0,
        )
        st.divider()
        animation_fps = st.slider("车辆移速", min_value=1, max_value=12, value=4)
        st.caption("播放时会等车辆移动到下一帧目标后再推进；这里控制车辆图标移动速度。")
        run_clicked = st.button("一键运行", type="primary", width="stretch")

    if "dashboard_runs" not in st.session_state:
        st.session_state.dashboard_runs = {}

    if run_clicked:
        np.random.seed(int(random_seed))
        run_data: Dict[str, Dict[str, object]] = {}

        with st.spinner("正在运行仿真并生成可视化数据..."):
            if algo_mode in ("仅最近优先", "三策略对比"):
                n_results, n_frames, n_network, n_stations = _run_single_strategy(
                    "nearest",
                    num_steps=num_steps,
                    tasks_per_step=tasks_per_step,
                    scenario_name=scenario_name,
                    random_seed=int(random_seed),
                    composite_preset=composite_preset,
                )
                run_data["nearest"] = {
                    "label": STRATEGY_LABELS["nearest"],
                    "results": n_results,
                    "frames": n_frames,
                    "network": n_network,
                    "stations": n_stations,
                    "timeline": _build_timeline(n_frames),
                }

            if algo_mode in ("仅最大优先", "三策略对比"):
                l_results, l_frames, l_network, l_stations = _run_single_strategy(
                    "largest",
                    num_steps=num_steps,
                    tasks_per_step=tasks_per_step,
                    scenario_name=scenario_name,
                    random_seed=int(random_seed),
                    composite_preset=composite_preset,
                )
                run_data["largest"] = {
                    "label": STRATEGY_LABELS["largest"],
                    "results": l_results,
                    "frames": l_frames,
                    "network": l_network,
                    "stations": l_stations,
                    "timeline": _build_timeline(l_frames),
                }

            if algo_mode in ("仅综合评分", "三策略对比"):
                c_results, c_frames, c_network, c_stations = _run_single_strategy(
                    "composite",
                    num_steps=num_steps,
                    tasks_per_step=tasks_per_step,
                    scenario_name=scenario_name,
                    random_seed=int(random_seed),
                    composite_preset=composite_preset,
                )
                run_data[f"composite:{composite_preset}"] = {
                    "label": f"{STRATEGY_LABELS['composite']}({composite_preset})",
                    "results": c_results,
                    "frames": c_frames,
                    "network": c_network,
                    "stations": c_stations,
                    "timeline": _build_timeline(c_frames),
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
            st.markdown(f"### {data.get('label', name)}")
            st.metric("总评分", f"{results['total_score']:.2f}")
            st.metric("完成任务", f"{int(results['completed'])}")
            st.metric("失败任务", f"{int(results['failed'])}")
            st.metric("总里程", f"{results['total_distance']:.2f} km")
            st.metric("总成本", f"{results['total_cost']:.2f}")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.markdown("#### 任务状态趋势")
        status_series: Dict[str, List[float]] = {}
        for name, data in runs.items():
            label = data.get("label", name)
            timeline = data["timeline"]
            status_series[f"{label}-待处理"] = timeline["pending"]
            status_series[f"{label}-运输中"] = timeline["in_progress"]
            status_series[f"{label}-完成"] = timeline["completed"]
            status_series[f"{label}-失败"] = timeline["failed"]
        st.line_chart(status_series)

    with chart_right:
        st.markdown("#### 平均电量趋势")
        battery_series = {
            data.get("label", name): data["timeline"]["avg_battery"]
            for name, data in runs.items()
        }
        st.line_chart(battery_series)

    st.subheader("调度动画")
    playback_cols = st.columns([1.2, 1, 1])
    with playback_cols[0]:
        selected_strategy = st.selectbox(
            "选择策略画面",
            list(runs.keys()),
            format_func=lambda key: runs[key].get("label", key),
            key="playback_strategy",
        )
    selected_data = runs[selected_strategy]
    selected_frames: List[SimulationFrame] = selected_data["frames"]

    if not selected_frames:
        st.warning("当前策略没有可播放帧。")
        return

    max_frame = len(selected_frames) - 1
    with playback_cols[1]:
        frame_idx = st.slider("查看帧", min_value=0, max_value=max_frame, value=0, key=f"frame_{selected_strategy}")
    with playback_cols[2]:
        focus_options = [""] + sorted((selected_frames[frame_idx].vehicle_details or {}).keys())
        focus_id = st.selectbox("高亮车辆", focus_options, format_func=lambda x: "不固定" if not x else x)

    frame = selected_frames[frame_idx]
    status_cols = st.columns(5)
    status_cols[0].metric("当前 step", frame.step)
    status_cols[1].metric("仿真时间", frame.current_time.strftime("%H:%M"))
    status_cols[2].metric("待处理", len(frame.pending_task_ids))
    status_cols[3].metric("完成", len(frame.completed_task_ids))
    status_cols[4].metric("失败", len(frame.failed_task_ids))

    road_rows = _build_road_rows(selected_data["network"], frame.current_time)
    payload = _build_scene_payload(
        selected_data["network"],
        selected_frames,
        road_rows,
        selected_data.get("stations", []),
    )
    _render_game_scene(payload, autoplay=True, fps=animation_fps, focus_id=focus_id)

    st.subheader("对象详情")
    tabs = st.tabs(["车辆", "任务列表", "道路信息", "充电站", "原始帧数据"])
    with tabs[0]:
        st.dataframe(_vehicle_rows(frame), width="stretch", hide_index=True)
    with tabs[1]:
        task_rows = _task_rows(frame)
        status_filter = st.multiselect(
            "任务状态筛选",
            ["pending", "in_progress", "completed", "failed"],
            default=["pending", "in_progress", "completed", "failed"],
        )
        filtered_tasks = [row for row in task_rows if row["状态"] in status_filter]
        st.dataframe(filtered_tasks, width="stretch", hide_index=True)
    with tabs[2]:
        st.dataframe(road_rows, width="stretch", hide_index=True)
    with tabs[3]:
        st.dataframe(_station_rows(frame), width="stretch", hide_index=True)
    with tabs[4]:
        st.json(
            {
                "step": frame.step,
                "time": frame.current_time.isoformat(),
                "vehicles": frame.vehicle_details,
                "tasks": frame.task_details,
                "stations": frame.station_details,
            },
            expanded=False,
        )


if __name__ == "__main__":
    main()
