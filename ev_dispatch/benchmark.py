import argparse
import csv
import json
import os
import statistics
from typing import Callable, Dict, Iterable, List, Sequence

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.algorithms.strategies import (
    COMPOSITE_CONFIG_PRESETS,
    DispatcherCompositeScore,
    DispatcherLargestFirst,
    DispatcherNearestFirst,
)
from ev_dispatch.scenarios.city_scales import (
    CityScaleName,
    build_city_scale_scenario,
    list_city_scales,
)
from ev_dispatch.scenarios.default import CargoConfig
from ev_dispatch.simulator.simulator import Simulator


StrategyFactory = Callable[[object], Dispatcher]


STRATEGIES: Dict[str, StrategyFactory] = {
    "nearest": DispatcherNearestFirst,
    "largest": DispatcherLargestFirst,
    "composite": DispatcherCompositeScore,
}


DEFAULT_METRICS = [
    "completion_rate",
    "on_time_rate",
    "avg_delay_hours",
    "total_distance",
    "total_cost",
    "total_score",
    "distance_per_completed_task",
    "cost_per_completed_task",
    "avg_battery_ratio",
    "avg_station_wait_minutes",
]


def _parse_csv_values(raw: str, cast=str) -> List:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(cast(item))
    return values


def _build_dispatcher(strategy_name: str, network) -> Dispatcher:
    if ":" not in strategy_name:
        return STRATEGIES[strategy_name](network)

    base_name, variant_name = strategy_name.split(":", 1)
    if base_name != "composite":
        raise ValueError(f"Strategy variants are only supported for composite: {strategy_name}")
    if variant_name not in COMPOSITE_CONFIG_PRESETS:
        raise ValueError(
            f"Unknown composite preset: {variant_name}. "
            f"Available: {','.join(COMPOSITE_CONFIG_PRESETS.keys())}"
        )
    return DispatcherCompositeScore(
        network,
        config=COMPOSITE_CONFIG_PRESETS[variant_name],
    )


def run_single_benchmark(
    scale: CityScaleName,
    strategy_name: str,
    seed: int,
    num_steps: int = None,
    tasks_per_step: int = None,
    cargo_config: CargoConfig = None,
) -> Dict[str, float]:
    network, vehicles, charging_stations, cfg = build_city_scale_scenario(
        scale=scale,
        random_seed=seed,
    )
    steps = cfg.num_steps if num_steps is None else int(num_steps)
    tasks = cfg.tasks_per_step if tasks_per_step is None else int(tasks_per_step)
    dispatcher = _build_dispatcher(strategy_name, network)
    simulator = Simulator(
        network=network,
        vehicles=vehicles,
        charging_stations=charging_stations,
        dispatcher=dispatcher,
        cargo_config=cargo_config or CargoConfig(),
        random_seed=seed,
        debug_run_id=f"benchmark-{scale}-{strategy_name}-{seed}",
    )
    results = simulator.run_simulation(num_steps=steps, tasks_per_step=tasks)
    return {
        "scale": scale,
        "strategy": strategy_name,
        "seed": seed,
        "num_steps": steps,
        "tasks_per_step": tasks,
        "num_vehicles": len(vehicles),
        "num_stations": len(charging_stations),
        "num_nodes": len(network.nodes),
        **results,
    }


def run_benchmark_suite(
    scales: Sequence[CityScaleName],
    strategies: Sequence[str],
    seeds: Sequence[int],
    num_steps: int = None,
    tasks_per_step: int = None,
    cargo_config: CargoConfig = None,
) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for scale in scales:
        for seed in seeds:
            for strategy_name in strategies:
                rows.append(
                    run_single_benchmark(
                        scale=scale,
                        strategy_name=strategy_name,
                        seed=int(seed),
                        num_steps=num_steps,
                        tasks_per_step=tasks_per_step,
                        cargo_config=cargo_config,
                    )
                )
    return rows


def summarize_runs(rows: Iterable[Dict[str, float]]) -> List[Dict[str, float]]:
    grouped: Dict[tuple, List[Dict[str, float]]] = {}
    for row in rows:
        grouped.setdefault((row["scale"], row["strategy"]), []).append(row)

    summaries = []
    for (scale, strategy), group in sorted(grouped.items()):
        summary: Dict[str, float] = {
            "scale": scale,
            "strategy": strategy,
            "runs": len(group),
        }
        for metric in DEFAULT_METRICS:
            values = [float(row[metric]) for row in group if metric in row]
            if not values:
                continue
            summary[f"{metric}_mean"] = statistics.fmean(values)
            summary[f"{metric}_stdev"] = statistics.stdev(values) if len(values) > 1 else 0.0
        summaries.append(summary)
    return summaries


def write_csv(path: str, rows: List[Dict[str, float]]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: str, payload: Dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def print_summary_table(summaries: List[Dict[str, float]]) -> None:
    if not summaries:
        print("No benchmark rows.")
        return
    header = (
        "scale",
        "strategy",
        "runs",
        "completion",
        "on_time",
        "cost/task",
        "distance/task",
        "score",
    )
    print(" | ".join(header))
    print("-" * 92)
    for row in summaries:
        print(
            " | ".join(
                [
                    str(row["scale"]),
                    str(row["strategy"]),
                    str(row["runs"]),
                    f"{row.get('completion_rate_mean', 0.0):.3f}",
                    f"{row.get('on_time_rate_mean', 0.0):.3f}",
                    f"{row.get('cost_per_completed_task_mean', 0.0):.2f}",
                    f"{row.get('distance_per_completed_task_mean', 0.0):.2f}",
                    f"{row.get('total_score_mean', 0.0):.2f}",
                ]
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run reproducible EV dispatch benchmarks.")
    parser.add_argument(
        "--scales",
        default="small_city",
        help=f"Comma-separated scales. Available: {','.join(list_city_scales())}",
    )
    parser.add_argument(
        "--strategies",
        default="nearest,largest,composite",
        help=(
            f"Comma-separated strategies. Available: {','.join(STRATEGIES.keys())}. "
            f"Composite presets can be addressed as composite:<preset>; "
            f"presets: {','.join(COMPOSITE_CONFIG_PRESETS.keys())}"
        ),
    )
    parser.add_argument("--seeds", default="42,43,44", help="Comma-separated integer seeds.")
    parser.add_argument("--steps", type=int, default=None, help="Override scenario simulation steps.")
    parser.add_argument("--tasks-per-step", type=int, default=None, help="Override scenario task arrivals.")
    parser.add_argument("--output-dir", default="outputs/benchmarks", help="Directory for CSV/JSON outputs.")
    parser.add_argument("--tag", default="latest", help="Output file tag.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scales = _parse_csv_values(args.scales, str)
    strategies = _parse_csv_values(args.strategies, str)
    seeds = _parse_csv_values(args.seeds, int)

    unknown_scales = sorted(set(scales) - set(list_city_scales()))
    base_strategy_names = {strategy.split(":", 1)[0] for strategy in strategies}
    unknown_strategies = sorted(base_strategy_names - set(STRATEGIES.keys()))
    unknown_composite_presets = sorted(
        strategy.split(":", 1)[1]
        for strategy in strategies
        if strategy.startswith("composite:") and strategy.split(":", 1)[1] not in COMPOSITE_CONFIG_PRESETS
    )
    if unknown_scales:
        raise ValueError(f"Unknown scales: {unknown_scales}")
    if unknown_strategies:
        raise ValueError(f"Unknown strategies: {unknown_strategies}")
    if unknown_composite_presets:
        raise ValueError(
            f"Unknown composite presets: {unknown_composite_presets}. "
            f"Available: {','.join(COMPOSITE_CONFIG_PRESETS.keys())}"
        )

    rows = run_benchmark_suite(
        scales=scales,
        strategies=strategies,
        seeds=seeds,
        num_steps=args.steps,
        tasks_per_step=args.tasks_per_step,
        cargo_config=CargoConfig(num_types=4, type_1_ratio=0.7),
    )
    summaries = summarize_runs(rows)

    csv_path = os.path.join(args.output_dir, f"runs_{args.tag}.csv")
    summary_csv_path = os.path.join(args.output_dir, f"summary_{args.tag}.csv")
    json_path = os.path.join(args.output_dir, f"benchmark_{args.tag}.json")
    write_csv(csv_path, rows)
    write_csv(summary_csv_path, summaries)
    write_json(
        json_path,
        {
            "args": vars(args),
            "strategies": list(strategies),
            "scales": list(scales),
            "seeds": list(seeds),
            "runs": rows,
            "summary": summaries,
        },
    )

    print_summary_table(summaries)
    print(f"\nWrote raw runs: {csv_path}")
    print(f"Wrote summary: {summary_csv_path}")
    print(f"Wrote JSON: {json_path}")


if __name__ == "__main__":
    main()
