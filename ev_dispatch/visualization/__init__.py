"""Visualization helpers for simulation playback and reporting."""

from typing import Any

from ev_dispatch.visualization.console import print_run_summary


def play_simulation_frames(*args: Any, **kwargs: Any):
	from ev_dispatch.visualization.matplotlib_player import play_simulation_frames as _impl

	return _impl(*args, **kwargs)


def run_streamlit_dashboard() -> None:
	from ev_dispatch.visualization.streamlit_dashboard import main as _streamlit_main

	_streamlit_main()

__all__ = ["print_run_summary", "play_simulation_frames", "run_streamlit_dashboard"]
