"""Discrete-time elevator simulation with a scheduler-agnostic engine.

"Discrete-time" means the clock advances in whole ticks; "scheduler-agnostic" means the
engine (:mod:`.simulation`) only enforces the rules, while a pluggable scheduler
(:mod:`.scheduler`, :mod:`.algorithms`) makes every decision. Start reading at
:mod:`.models` for the data types, then :mod:`.simulation` for the tick loop.
"""

from .algorithms import BUILTINS, available, create
from .models import BuildingConfig, Direction, ElevatorView, Request, Rider, SimulationState
from .scheduler import CarAction, ReplayError, Scheduler, SchedulingError
from .simulation import Simulation, SimulationResult, SimulationStalled
from .stats import Distribution, Summary, distribution, format_summary, percentile, summarize

__version__ = "0.1.0"

__all__ = [
    "BuildingConfig", "Direction", "ElevatorView", "Request", "Rider", "SimulationState",
    "BUILTINS", "CarAction", "ReplayError", "Scheduler", "SchedulingError", "available",
    "create",
    "Simulation", "SimulationResult", "SimulationStalled",
    "Distribution", "Summary", "distribution", "format_summary", "percentile", "summarize",
]
