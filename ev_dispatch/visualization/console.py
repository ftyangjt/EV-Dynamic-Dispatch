from typing import Dict, List

from ev_dispatch.core.interfaces import SimulationFrame


def print_run_summary(name: str, results: Dict[str, float], frames: List[SimulationFrame]) -> None:
    """Console summary helper for quick demos and recorder scripts."""
    print(f"\n[{name}]")
    print(f"完成任务: {int(results['completed'])}")
    print(f"失败任务: {int(results['failed'])}")
    print(f"总评分: {results['total_score']:.2f}")
    if frames:
        latest = frames[-1]
        print(f"最后时刻: step={latest.step}, pending={len(latest.pending_task_ids)}")
