"""
DeadlockX Engine Package
========================
Exposes the core detection engine, additive heuristics, and what-if simulator.
"""
from .core import DeadlockEngine, SystemState, SCENARIOS
from .heuristics import HeuristicsEngine
from .whatif import WhatIfSimulator

__all__ = ["DeadlockEngine", "SystemState", "SCENARIOS", "HeuristicsEngine", "WhatIfSimulator"]
