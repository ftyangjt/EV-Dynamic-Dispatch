from abc import ABC, abstractmethod
from typing import Dict, List

from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.task import Task
from ev_dispatch.core.vehicle import Vehicle


class Dispatcher(ABC):
    """Dispatcher interface."""

    def __init__(self, network: RoadNetwork):
        self.network = network

    @abstractmethod
    def assign_tasks(self, tasks: List[Task], vehicles: List[Vehicle]) -> Dict[str, List[Task]]:
        raise NotImplementedError
