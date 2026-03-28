from dataclasses import dataclass

import numpy as np


@dataclass
class Location:
    """2D coordinate used by vehicles, tasks, and stations."""

    x: float
    y: float
    name: str = "unknown"

    def distance_to(self, other: "Location") -> float:
        return float(np.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2))
