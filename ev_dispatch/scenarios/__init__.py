"""Scenario builders for benchmark scales."""

from ev_dispatch.scenarios.city_scales import (
    CITY_SCALE_CONFIGS,
    CityScaleConfig,
    build_city_scale_scenario,
    get_city_scale_config,
    list_city_scales,
)
from ev_dispatch.scenarios.default import CargoConfig, build_default_scenario

__all__ = [
    "CargoConfig",
    "build_default_scenario",
    "CityScaleConfig",
    "CITY_SCALE_CONFIGS",
    "build_city_scale_scenario",
    "get_city_scale_config",
    "list_city_scales",
]
