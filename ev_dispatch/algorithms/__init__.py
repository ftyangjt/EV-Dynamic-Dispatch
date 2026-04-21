"""Dispatch strategies and algorithm interfaces."""

from ev_dispatch.algorithms.dispatcher import Dispatcher
from ev_dispatch.algorithms.strategies import DispatcherLargestFirst, DispatcherNearestFirst

__all__ = [
	"Dispatcher",
	"DispatcherLargestFirst",
	"DispatcherNearestFirst",
]
