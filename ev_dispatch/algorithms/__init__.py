"""Dispatch strategies and algorithm interfaces."""

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.algorithms.strategies import (
    COMPOSITE_CONFIG_PRESETS,
    CompositeScoreConfig,
    DispatcherCompositeScore,
    DispatcherLargestFirst,
    DispatcherNearestFirst,
)

__all__ = [
	"Dispatcher",
	"COMPOSITE_CONFIG_PRESETS",
	"CompositeScoreConfig",
	"DispatcherCompositeScore",
	"DispatcherLargestFirst",
	"DispatcherNearestFirst",
]
