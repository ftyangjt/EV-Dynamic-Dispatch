import heapq
from itertools import count
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from ev_dispatch.core.task import Task


TaskPriorityKey = Callable[[Task], Tuple[float, ...]]


def _deadline_key(task: Task) -> Tuple[float, ...]:
    return (task.deadline.timestamp(), -float(task.priority), float(task.weight))


def _weight_key(task: Task) -> Tuple[float, ...]:
    return (-float(task.weight), task.deadline.timestamp(), -float(task.priority))


def _priority_deadline_key(task: Task) -> Tuple[float, ...]:
    return (-float(task.priority), task.deadline.timestamp(), -float(task.weight))


TASK_POOL_POLICIES: Dict[str, TaskPriorityKey] = {
    "deadline": _deadline_key,
    "weight": _weight_key,
    "priority_deadline": _priority_deadline_key,
}


class TaskPriorityPool:
    """Heap-backed pending task pool with lazy removal by task id."""

    def __init__(
        self,
        tasks: Iterable[Task] = (),
        policy: str = "deadline",
        key: Optional[TaskPriorityKey] = None,
    ):
        if key is None:
            key = TASK_POOL_POLICIES.get(policy)
        if key is None:
            raise ValueError(f"Unknown task pool policy: {policy}")

        self.policy = policy
        self._key = key
        self._counter = count()
        self._heap: List[Tuple[Tuple[float, ...], int, str]] = []
        self._tasks_by_id: Dict[str, Task] = {}
        self._removed_task_ids = set()

        for task in tasks:
            self.push(task)

    def __len__(self) -> int:
        return len(self._tasks_by_id) - len(self._removed_task_ids)

    def __bool__(self) -> bool:
        self._discard_removed_head()
        return bool(self._heap)

    def push(self, task: Task) -> None:
        self._tasks_by_id[task.id] = task
        self._removed_task_ids.discard(task.id)
        heapq.heappush(self._heap, (self._key(task), next(self._counter), task.id))

    def remove(self, task_id: str) -> None:
        if task_id in self._tasks_by_id:
            self._removed_task_ids.add(task_id)

    def pop(self) -> Optional[Task]:
        self._discard_removed_head()
        if not self._heap:
            return None
        _key, _seq, task_id = heapq.heappop(self._heap)
        task = self._tasks_by_id.pop(task_id, None)
        self._removed_task_ids.discard(task_id)
        return task

    def peek(self) -> Optional[Task]:
        self._discard_removed_head()
        if not self._heap:
            return None
        return self._tasks_by_id.get(self._heap[0][2])

    def ordered_tasks(self) -> List[Task]:
        return list(self)

    def __iter__(self) -> Iterator[Task]:
        clone = TaskPriorityPool(policy=self.policy, key=self._key)
        clone._counter = count(start=0)
        clone._heap = list(self._heap)
        heapq.heapify(clone._heap)
        clone._tasks_by_id = dict(self._tasks_by_id)
        clone._removed_task_ids = set(self._removed_task_ids)
        while clone:
            task = clone.pop()
            if task is not None:
                yield task

    def _discard_removed_head(self) -> None:
        while self._heap:
            _key, _seq, task_id = self._heap[0]
            if task_id in self._removed_task_ids or task_id not in self._tasks_by_id:
                heapq.heappop(self._heap)
                self._removed_task_ids.discard(task_id)
                self._tasks_by_id.pop(task_id, None)
                continue
            break
