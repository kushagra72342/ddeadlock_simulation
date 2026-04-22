"""
Deadlock Detection Tool — FastAPI Backend
Analyzes process-resource allocation graphs for circular wait conditions.

Bug fixes applied:
  - Pydantic v2 compatibility: replaced .dict() with .model_dump()
  - Added /metrics endpoint for real-time monitoring stats
  - Added /scenarios/{id}/analyze convenience endpoint
  - Improved error handling and response validation
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from typing import Optional
import uvicorn
import re

from deadlock_engine import DeadlockEngine, SystemState

app = FastAPI(
    title="DeadlockX — Deadlock Detector API",
    description="Detects circular wait conditions in process-resource allocation graphs using DFS and Banker's Algorithm",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = DeadlockEngine()

# Simple in-memory stats counter
_stats = {"analyses_run": 0, "deadlocks_found": 0, "traces_run": 0}


# ── Request / Response Models ────────────────────────────────────────────────

_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,16}$')


class ProcessModel(BaseModel):
    pid:         str
    name:        str
    priority:    int = 1
    holds:       list[str] = []
    waiting_for: list[str] = []

    @field_validator("pid")
    @classmethod
    def validate_pid(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("pid must be 1–16 alphanumeric/underscore/hyphen chars")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("priority must be between 1 and 10")
        return v


class ResourceModel(BaseModel):
    rid:       str
    name:      str
    instances: int = 1
    available: int = 1

    @field_validator("rid")
    @classmethod
    def validate_rid(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("rid must be 1–16 alphanumeric/underscore/hyphen chars")
        return v

    @field_validator("instances", "available")
    @classmethod
    def validate_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("instances and available must be non-negative")
        return v


class SystemStateModel(BaseModel):
    processes: list[ProcessModel]
    resources: list[ResourceModel]


class AnalysisResponse(BaseModel):
    has_deadlock:         bool
    cycles:               list[list[str]]
    affected_processes:   list[str]
    affected_resources:   list[str]
    resolution_strategies: list[dict]
    safe_sequence:        Optional[list[str]]
    allocation_matrix:    dict
    graph_edges:          list[dict]


# ── Helper ───────────────────────────────────────────────────────────────────

def _to_system_state(model: SystemStateModel) -> SystemState:
    """Convert Pydantic model → engine dataclass.
    Uses model_dump() for Pydantic v2 compatibility (was .dict() in v1).
    """
    return SystemState(
        processes=[p.model_dump() for p in model.processes],
        resources=[r.model_dump() for r in model.resources],
    )


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "DeadlockX API v2.0", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}


@app.get("/metrics", tags=["Health"])
def metrics():
    """Real-time usage statistics."""
    return _stats


@app.get("/scenarios", tags=["Scenarios"])
def list_scenarios():
    """Return available built-in scenarios."""
    return {
        "scenarios": [
            {
                "id": "classic",
                "name": "Classic 3-Process Deadlock",
                "description": "Textbook circular wait: P1→R2→P2→R3→P3→R1→P1",
                "difficulty": "basic"
            },
            {
                "id": "chain",
                "name": "5-Process Chain Deadlock",
                "description": "Longer chain involving 5 processes and 5 resources",
                "difficulty": "intermediate"
            },
            {
                "id": "partial",
                "name": "Partial Deadlock",
                "description": "Some processes deadlocked, others can still proceed",
                "difficulty": "intermediate"
            },
            {
                "id": "multi_instance",
                "name": "Multi-Instance Resources",
                "description": "Resources with multiple instances — requires Banker's Algorithm",
                "difficulty": "advanced"
            },
            {
                "id": "safe",
                "name": "Safe State (No Deadlock)",
                "description": "System in a safe state — no circular wait possible",
                "difficulty": "basic"
            },
        ]
    }


@app.get("/scenarios/{scenario_id}", response_model=SystemStateModel, tags=["Scenarios"])
def get_scenario(scenario_id: str):
    """Load a built-in scenario."""
    scenario = engine.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")
    return scenario


@app.get("/scenarios/{scenario_id}/analyze", response_model=AnalysisResponse, tags=["Scenarios"])
def analyze_scenario(scenario_id: str):
    """Load and immediately analyze a built-in scenario (convenience endpoint)."""
    scenario = engine.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")
    sys_state = SystemState(
        processes=scenario["processes"],
        resources=scenario["resources"],
    )
    result = engine.analyze(sys_state)
    _stats["analyses_run"] += 1
    if result["has_deadlock"]:
        _stats["deadlocks_found"] += 1
    return result


@app.post("/analyze", response_model=AnalysisResponse, tags=["Analysis"])
def analyze_system(state: SystemStateModel):
    """
    Full deadlock analysis on the submitted system state.
    Returns cycles, affected nodes, resolution strategies, and safe sequence.
    """
    if not state.processes:
        raise HTTPException(status_code=422, detail="At least one process is required")

    sys_state = _to_system_state(state)
    result = engine.analyze(sys_state)

    _stats["analyses_run"] += 1
    if result["has_deadlock"]:
        _stats["deadlocks_found"] += 1

    return result


@app.post("/analyze/step", tags=["Analysis"])
def analyze_step(state: SystemStateModel):
    """
    Step-by-step DFS trace for educational visualization.
    Returns the DFS traversal order and cycle discovery moments.
    """
    if not state.processes:
        raise HTTPException(status_code=422, detail="At least one process is required")

    sys_state = _to_system_state(state)
    result = engine.dfs_trace(sys_state)

    _stats["traces_run"] += 1
    return result


class BankersRequestModel(BaseModel):
    """Body for POST /bankers/simulate."""
    processes: list[ProcessModel]
    resources: list[ResourceModel]
    pid: str
    requested: dict  # {rid: count}


@app.post("/bankers/simulate", tags=["Banker's Algorithm"])
def bankers_simulate(body: BankersRequestModel):
    """
    Simulate a single resource request through the Banker's Algorithm.
    Returns whether granting it keeps the system safe.
    """
    sys_state = SystemState(
        processes=[p.model_dump() for p in body.processes],
        resources=[r.model_dump() for r in body.resources],
    )
    return engine.simulate_request(sys_state, body.pid, body.requested)


@app.post("/bankers/resolve", tags=["Banker's Algorithm"])
def bankers_resolve(state: SystemStateModel):
    """
    Auto-resolve deadlocks by iteratively granting safe pending requests
    using the Banker's Algorithm.
    """
    if not state.processes:
        raise HTTPException(status_code=422, detail="At least one process is required")
    sys_state = _to_system_state(state)
    result = engine.resolve_via_bankers(sys_state)
    _stats["analyses_run"] += 1
    return result


@app.post("/bankers/trace", tags=["Banker's Algorithm"])
def bankers_trace(state: SystemStateModel):
    """
    Step-by-step trace of the Banker's safety algorithm for educational mode.
    """
    if not state.processes:
        raise HTTPException(status_code=422, detail="At least one process is required")
    sys_state = _to_system_state(state)
    return engine.bankers_step_trace(sys_state)


@app.post("/resolve", tags=["Analysis"])
def resolve_deadlock(state: SystemStateModel):
    """
    Simulate deadlock resolution by recommending process termination order.
    Returns the recommended kill sequence and resulting safe state.
    """
    if not state.processes:
        raise HTTPException(status_code=422, detail="At least one process is required")

    sys_state = _to_system_state(state)
    cycles = engine.detect_cycles(sys_state)

    if not cycles:
        return {"message": "No deadlock to resolve", "resolved": True, "strategies": []}

    return engine.generate_resolution(sys_state, cycles)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"}
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
