import argparse
import csv
import json
import os
import statistics
import time
from dataclasses import asdict, replace
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np

from ev_dispatch.algorithms.strategies import (
    COMPOSITE_CONFIG_PRESETS,
    CompositeScoreConfig,
    DispatcherCompositeScore,
)
from ev_dispatch.scenarios.city_scales import CityScaleName, build_city_scale_scenario, list_city_scales
from ev_dispatch.scenarios.default import CargoConfig
from ev_dispatch.simulator.simulator import DEFAULT_SIMULATION_START_TIME, Simulator, parse_simulation_start_time


TUNABLE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "urgency_weight": (0.0, 30.0),
    "urgency_floor_hours": (0.05, 2.0),
    "load_fit_weight": (0.0, 30.0),
    "pickup_distance_weight": (0.0, 4.0),
    "delivery_distance_weight": (0.0, 2.0),
    "travel_time_weight": (0.0, 15.0),
    "energy_weight": (0.0, 8.0),
    "lateness_weight": (0.0, 220.0),
    "station_wait_weight": (0.0, 18.0),
    "station_distance_weight": (0.0, 1.5),
    "low_battery_ratio_threshold": (0.05, 0.60),
    "low_battery_penalty_weight": (0.0, 220.0),
    "reserve_energy_kwh": (0.0, 18.0),
}


def _parse_csv_values(raw: str, cast=str) -> List:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(cast(item))
    return values


def _clip(name: str, value: float) -> float:
    lo, hi = TUNABLE_BOUNDS[name]
    return float(min(hi, max(lo, value)))


def _config_from_values(name: str, values: Dict[str, float]) -> CompositeScoreConfig:
    kwargs = {field: _clip(field, float(value)) for field, value in values.items()}
    return replace(COMPOSITE_CONFIG_PRESETS["balanced"], name=name, **kwargs)


def _config_values(config: CompositeScoreConfig) -> Dict[str, float]:
    data = asdict(config)
    return {name: float(data[name]) for name in TUNABLE_BOUNDS}


def sample_random_config(rng: np.random.Generator, name: str) -> CompositeScoreConfig:
    values = {}
    for field, (lo, hi) in TUNABLE_BOUNDS.items():
        values[field] = float(rng.uniform(lo, hi))
    return _config_from_values(name, values)


def perturb_config(
    rng: np.random.Generator,
    base: CompositeScoreConfig,
    name: str,
    temperature: float,
) -> CompositeScoreConfig:
    values = _config_values(base)
    for field, (lo, hi) in TUNABLE_BOUNDS.items():
        span = hi - lo
        noise = rng.normal(0.0, span * max(0.01, temperature))
        values[field] = _clip(field, values[field] + noise)
    return _config_from_values(name, values)


def evaluate_config(
    config: CompositeScoreConfig,
    scales: Sequence[CityScaleName],
    seeds: Sequence[int],
    num_steps: int = None,
    tasks_per_step: int = None,
    start_time=DEFAULT_SIMULATION_START_TIME,
) -> Dict[str, object]:
    rows: List[Dict[str, float]] = []
    for scale in scales:
        for seed in seeds:
            network, vehicles, charging_stations, cfg = build_city_scale_scenario(
                scale=scale,
                random_seed=int(seed),
            )
            steps = cfg.num_steps if num_steps is None else int(num_steps)
            tasks = cfg.tasks_per_step if tasks_per_step is None else int(tasks_per_step)
            simulator = Simulator(
                network=network,
                vehicles=vehicles,
                charging_stations=charging_stations,
                dispatcher=DispatcherCompositeScore(network, config=config),
                cargo_config=CargoConfig(num_types=4, type_1_ratio=0.7),
                random_seed=int(seed),
                start_time=start_time,
                debug_run_id=f"tune-composite-{scale}-{seed}-{config.name}",
            )
            results = simulator.run_simulation(num_steps=steps, tasks_per_step=tasks)
            rows.append(
                {
                    "scale": scale,
                    "seed": int(seed),
                    "total_score": float(results["total_score"]),
                    "completion_rate": float(results["completion_rate"]),
                    "on_time_rate": float(results["on_time_rate"]),
                    "total_cost": float(results["total_cost"]),
                    "total_distance": float(results["total_distance"]),
                    "completed": int(results["completed"]),
                    "failed": int(results["failed"]),
                    "pending": int(results["pending"]),
                    "in_progress": int(results["in_progress"]),
                }
            )

    scores = [float(row["total_score"]) for row in rows]
    completions = [float(row["completion_rate"]) for row in rows]
    on_time = [float(row["on_time_rate"]) for row in rows]
    return {
        "rows": rows,
        "mean_score": statistics.fmean(scores) if scores else 0.0,
        "min_score": min(scores) if scores else 0.0,
        "stdev_score": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        "mean_completion_rate": statistics.fmean(completions) if completions else 0.0,
        "mean_on_time_rate": statistics.fmean(on_time) if on_time else 0.0,
    }


def _initial_configs() -> List[CompositeScoreConfig]:
    return [COMPOSITE_CONFIG_PRESETS[name] for name in ("balanced", "deadline", "cost", "energy")]


def _objective(summary: Dict[str, object], stdev_penalty: float) -> float:
    return float(summary["mean_score"]) - float(stdev_penalty) * float(summary["stdev_score"])


def tune(
    scales: Sequence[CityScaleName],
    seeds: Sequence[int],
    trials: int,
    optimizer_seed: int,
    num_steps: int = None,
    tasks_per_step: int = None,
    start_time=DEFAULT_SIMULATION_START_TIME,
    stdev_penalty: float = 0.0,
) -> Dict[str, object]:
    rng = np.random.default_rng(int(optimizer_seed))
    history: List[Dict[str, object]] = []
    best_config = None
    best_summary = None
    best_objective = float("-inf")
    started_at = time.perf_counter()

    candidate_queue: List[CompositeScoreConfig] = _initial_configs()
    total_trials = max(1, int(trials))

    for trial_index in range(total_trials):
        if trial_index < len(candidate_queue):
            candidate = candidate_queue[trial_index]
        elif best_config is not None and rng.random() < 0.75:
            progress = trial_index / max(1, total_trials - 1)
            temperature = 0.18 * (1.0 - progress) + 0.035
            candidate = perturb_config(rng, best_config, f"trial_{trial_index}", temperature)
        else:
            candidate = sample_random_config(rng, f"trial_{trial_index}")

        summary = evaluate_config(
            candidate,
            scales=scales,
            seeds=seeds,
            num_steps=num_steps,
            tasks_per_step=tasks_per_step,
            start_time=start_time,
        )
        objective = _objective(summary, stdev_penalty=stdev_penalty)
        record = {
            "trial": trial_index,
            "config_name": candidate.name,
            "objective": objective,
            "mean_score": summary["mean_score"],
            "min_score": summary["min_score"],
            "stdev_score": summary["stdev_score"],
            "mean_completion_rate": summary["mean_completion_rate"],
            "mean_on_time_rate": summary["mean_on_time_rate"],
            "config": asdict(candidate),
            "rows": summary["rows"],
        }
        history.append(record)

        if objective > best_objective:
            best_objective = objective
            best_config = replace(candidate, name="tuned")
            best_summary = summary

        print(
            "trial={trial:03d} objective={objective:.3f} mean={mean:.3f} "
            "min={min_score:.3f} stdev={stdev:.3f} best={best:.3f}".format(
                trial=trial_index,
                objective=objective,
                mean=float(summary["mean_score"]),
                min_score=float(summary["min_score"]),
                stdev=float(summary["stdev_score"]),
                best=best_objective,
            )
        )

    return {
        "elapsed_seconds": time.perf_counter() - started_at,
        "scales": list(scales),
        "seeds": [int(seed) for seed in seeds],
        "trials": total_trials,
        "optimizer_seed": int(optimizer_seed),
        "num_steps": num_steps,
        "tasks_per_step": tasks_per_step,
        "stdev_penalty": float(stdev_penalty),
        "best_objective": best_objective,
        "best_config": asdict(best_config) if best_config is not None else {},
        "best_summary": {
            key: value for key, value in (best_summary or {}).items() if key != "rows"
        },
        "history": history,
    }


def write_outputs(output_dir: str, tag: str, payload: Dict[str, object]) -> Tuple[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"composite_tuning_{tag}.json")
    csv_path = os.path.join(output_dir, f"composite_tuning_{tag}.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    fieldnames = [
        "trial",
        "config_name",
        "objective",
        "mean_score",
        "min_score",
        "stdev_score",
        "mean_completion_rate",
        "mean_on_time_rate",
    ] + list(TUNABLE_BOUNDS.keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in payload["history"]:
            flat = {key: row.get(key) for key in fieldnames}
            for key in TUNABLE_BOUNDS:
                flat[key] = row["config"][key]
            writer.writerow(flat)

    return json_path, csv_path


def print_best_config(config: Dict[str, float]) -> None:
    print("\nBest CompositeScoreConfig:")
    print("CompositeScoreConfig(")
    for key, value in config.items():
        if key == "name":
            print(f"    name={value!r},")
        elif isinstance(value, float):
            print(f"    {key}={value:.6g},")
        else:
            print(f"    {key}={value!r},")
    print(")")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune DispatcherCompositeScore weights across seeds.")
    parser.add_argument(
        "--scales",
        default="small_city",
        help=f"Comma-separated scales. Available: {','.join(list_city_scales())}",
    )
    parser.add_argument("--seeds", default="42,43,44", help="Comma-separated scenario seeds.")
    parser.add_argument("--trials", type=int, default=30, help="Number of parameter candidates to evaluate.")
    parser.add_argument("--optimizer-seed", type=int, default=2026, help="Random seed for the tuner itself.")
    parser.add_argument("--steps", type=int, default=None, help="Override simulation steps.")
    parser.add_argument("--tasks-per-step", type=int, default=None, help="Override task arrivals per step.")
    parser.add_argument(
        "--start-time",
        default=DEFAULT_SIMULATION_START_TIME.isoformat(timespec="seconds"),
        help="Simulation start time in ISO format, e.g. 2026-01-01T08:00:00.",
    )
    parser.add_argument(
        "--stdev-penalty",
        type=float,
        default=0.0,
        help="Subtract this multiplier times score stdev from mean score for robust tuning.",
    )
    parser.add_argument("--output-dir", default="outputs/tuning", help="Directory for tuning outputs.")
    parser.add_argument("--tag", default="latest", help="Output file tag.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scales = _parse_csv_values(args.scales, str)
    seeds = _parse_csv_values(args.seeds, int)
    start_time = parse_simulation_start_time(args.start_time)

    unknown_scales = sorted(set(scales) - set(list_city_scales()))
    if unknown_scales:
        raise ValueError(f"Unknown scales: {unknown_scales}")

    payload = tune(
        scales=scales,
        seeds=seeds,
        trials=args.trials,
        optimizer_seed=args.optimizer_seed,
        num_steps=args.steps,
        tasks_per_step=args.tasks_per_step,
        start_time=start_time,
        stdev_penalty=args.stdev_penalty,
    )
    json_path, csv_path = write_outputs(args.output_dir, args.tag, payload)
    print_best_config(payload["best_config"])
    print(f"\nBest objective: {payload['best_objective']:.3f}")
    print(f"Elapsed seconds: {payload['elapsed_seconds']:.3f}")
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV: {csv_path}")


if __name__ == "__main__":
    main()
