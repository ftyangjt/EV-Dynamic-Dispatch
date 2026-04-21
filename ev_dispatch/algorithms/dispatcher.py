from abc import abstractmethod
from typing import List

from ev_dispatch.core.network import RoadNetwork
from ev_dispatch.core.interfaces import AbstractDispatcher, SimulationState, Action


class Dispatcher(AbstractDispatcher):
    """Dispatcher interface."""

    def __init__(self, network: RoadNetwork):
        super().__init__(network)

    @abstractmethod
    def generate_actions(self, state: SimulationState) -> List[Action]:
        raise NotImplementedError
