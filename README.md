# Deadlock Detector — Full Stack Tool

A production-grade tool for analyzing, visualizing, and resolving deadlock conditions in process-resource systems.

---

## Architecture

```
deadlock-tool/
├── backend/
│   ├── main.py                  # FastAPI REST API server
│   ├── deadlock_engine.py       # Core detection logic (DFS + Banker's Algorithm)
│   ├── deadlock_conditions.py   # OS-level threading deadlock simulator
│   └── requirements.txt
└── frontend/
    └── index.html               # Single-file full-stack frontend
```

---

## Quick Start

### Backend (Python / FastAPI)

```bash
cd backend
pip install -r requirements.txt
python main.py
# API running at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### Frontend

Open `frontend/index.html` in any browser.  
The frontend works *standalone* (local JS engine) and connects to the backend API when available.

---

## Running the Deadlock Condition Simulator

Demonstrates **real OS-level deadlocks** using Python threads:

```bash
cd backend

# Classic two-thread deadlock
python deadlock_conditions.py --scenario classic

# Dining philosophers
python deadlock_conditions.py --scenario dining

# Database row-lock deadlock
python deadlock_conditions.py --scenario db_lock

# Safe state (lock ordering fix)
python deadlock_conditions.py --scenario safe

# Run all scenarios
python deadlock_conditions.py --all
```

---

## API Endpoints

| Method | Path                    | Description                                  |
|--------|-------------------------|----------------------------------------------|
| GET    | `/`                     | Health check                                 |
| GET    | `/scenarios`            | List all built-in scenarios                  |
| GET    | `/scenarios/{id}`       | Load scenario state                          |
| POST   | `/analyze`              | Full deadlock analysis (DFS + Banker's)      |
| POST   | `/analyze/step`         | Step-by-step DFS trace (educational mode)    |
| POST   | `/resolve`              | Generate resolution strategy                 |
| GET    | `/health`               | Service health                               |

### Example: Analyze a system

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "processes": [
      {"pid":"P1","name":"Worker 1","priority":1,"holds":["R1"],"waiting_for":["R2"]},
      {"pid":"P2","name":"Worker 2","priority":2,"holds":["R2"],"waiting_for":["R1"]}
    ],
    "resources": [
      {"rid":"R1","name":"Mutex A","instances":1,"available":0},
      {"rid":"R2","name":"Mutex B","instances":1,"available":0}
    ]
  }'
```

### Response

```json
{
  "has_deadlock": true,
  "cycles": [["P1","R2","P2","R1","P1"]],
  "affected_processes": ["P1","P2"],
  "affected_resources": ["R1","R2"],
  "safe_sequence": null,
  "resolution_strategies": [...],
  "allocation_matrix": {...},
  "graph_edges": [...]
}
```

---

## Deadlock Detection Algorithms

### 1. DFS Cycle Detection (Single-Instance Resources)

Builds a Resource Allocation Graph (RAG) and runs depth-first search with a recursion stack to find back edges — each back edge indicates a cycle (deadlock).

**Time complexity:** O(V + E) where V = processes + resources, E = allocations + requests

### 2. Banker's Algorithm (Multi-Instance Resources)

Simulates process completion using available resources to find a safe execution sequence. If no safe sequence exists, the system is in an unsafe state.

**Key concept:** A state is safe if there exists a sequence where every process can eventually obtain all its needed resources.

---

## The Four Deadlock Conditions (Coffman, 1971)

All four must hold simultaneously for a deadlock to occur:

1. **Mutual exclusion** — Resources cannot be shared; only one process at a time
2. **Hold and wait** — A process holds one resource while waiting for another
3. **No preemption** — Resources cannot be forcibly taken from a process
4. **Circular wait** — A circular chain of processes each waiting for a resource held by the next

The tool detects **circular wait** (condition 4) via graph analysis.

---

## Resolution Strategies

| Strategy         | Cost   | Risk   | Description                                   |
|-----------------|--------|--------|-----------------------------------------------|
| Process termination | Low  | Medium | Abort lowest-priority process in cycle        |
| Resource preemption | Medium | High | Force-take resource, roll back process        |
| Lock ordering   | Low    | Low    | Enforce global acquisition order (prevention) |
| Timeout / wound-wait | Low | Low  | Timestamp-based preemption (prevention)       |
