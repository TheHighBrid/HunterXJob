"""Improvement Kernel project adapters for the 5 core applications."""
from __future__ import annotations

from improvement_kernel.adapters.agent_gang import AgentGangAdapter
from improvement_kernel.adapters.allo import AlloAdapter
from improvement_kernel.adapters.fidelis import FidelisAdapter
from improvement_kernel.adapters.hunterxjob import HunterXJobAdapter
from improvement_kernel.adapters.pedit import PeditAdapter

__all__ = [
    "HunterXJobAdapter",
    "AlloAdapter",
    "FidelisAdapter",
    "PeditAdapter",
    "AgentGangAdapter",
]
