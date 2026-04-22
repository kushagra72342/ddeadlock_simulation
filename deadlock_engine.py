"""
Deadlock Detection Engine
=========================
Implements:
  - Resource Allocation Graph (RAG) construction
  - Iterative DFS-based cycle detection (no recursion limit)
  - Banker's Algorithm safety check for multi-instance resources
  - Resolution strategy generator
  - Step-by-step DFS trace for educational mode

Bug fixes applied:
  - DFS is now iterative to avoid Python recursion limit on large graphs
  - Banker's algorithm outer loop runs n passes (was 1 pass)
  - Cycle deduplication uses canonical rotation, not frozenset
    (frozenset loses ordering and can incorrectly merge distinct cycles)
  - generate_resolution guards against empty proc list
"""

from dataclasses import dataclass
from typing import Optional
from collections import defaultdict


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class SystemState:
    processes: list[dict]
    resources: list[dict]


SCENARIOS = {
    "classic": {
        "processes": [
            {"pid": "P1", "name": "Process 1",  "priority": 2, "holds": ["R1"], "waiting_for": ["R2"]},
            {"pid": "P2", "name": "Process 2",  "priority": 1, "holds": ["R2"], "waiting_for": ["R3"]},
            {"pid": "P3", "name": "Process 3",  "priority": 3, "holds": ["R3"], "waiting_for": ["R1"]},
        ],
        "resources": [
            {"rid": "R1", "name": "Database Lock",  "instances": 1, "available": 0},
            {"rid": "R2", "name": "File Handle",    "instances": 1, "available": 0},
            {"rid": "R3", "name": "Network Socket", "instances": 1, "available": 0},
        ]
    },
    "chain": {
        "processes": [
            {"pid": "P1", "name": "Auth Service",  "priority": 3, "holds": ["R1"], "waiting_for": ["R2"]},
            {"pid": "P2", "name": "DB Worker",     "priority": 2, "holds": ["R2"], "waiting_for": ["R3"]},
            {"pid": "P3", "name": "Cache Manager", "priority": 1, "holds": ["R3"], "waiting_for": ["R4"]},
            {"pid": "P4", "name": "File Writer",   "priority": 2, "holds": ["R4"], "waiting_for": ["R5"]},
            {"pid": "P5", "name": "Log Daemon",    "priority": 1, "holds": ["R5"], "waiting_for": ["R1"]},
        ],
        "resources": [
            {"rid": "R1", "name": "Mutex A",     "instances": 1, "available": 0},
            {"rid": "R2", "name": "Mutex B",     "instances": 1, "available": 0},
            {"rid": "R3", "name": "Semaphore C", "instances": 1, "available": 0},
            {"rid": "R4", "name": "Semaphore D", "instances": 1, "available": 0},
            {"rid": "R5", "name": "Pipe E",      "instances": 1, "available": 0},
        ]
    },
    "partial": {
        "processes": [
            {"pid": "P1", "name": "Worker 1", "priority": 2, "holds": ["R1"], "waiting_for": ["R2"]},
            {"pid": "P2", "name": "Worker 2", "priority": 1, "holds": ["R2"], "waiting_for": ["R1"]},
            {"pid": "P3", "name": "Worker 3", "priority": 3, "holds": ["R3"], "waiting_for": []},
            {"pid": "P4", "name": "Worker 4", "priority": 1, "holds": [],     "waiting_for": ["R3"]},
        ],
        "resources": [
            {"rid": "R1", "name": "Lock A", "instances": 1, "available": 0},
            {"rid": "R2", "name": "Lock B", "instances": 1, "available": 0},
            {"rid": "R3", "name": "Lock C", "instances": 1, "available": 0},
        ]
    },
    "multi_instance": {
        "processes": [
            {"pid": "P1", "name": "Process 1", "priority": 2, "holds": ["R1"],       "waiting_for": ["R2"]},
            {"pid": "P2", "name": "Process 2", "priority": 1, "holds": ["R1", "R2"], "waiting_for": ["R3"]},
            {"pid": "P3", "name": "Process 3", "priority": 3, "holds": ["R2", "R3"], "waiting_for": []},
        ],
        "resources": [
            {"rid": "R1", "name": "Thread Pool",  "instances": 2, "available": 0},
            {"rid": "R2", "name": "DB Conn Pool", "instances": 3, "available": 1},
            {"rid": "R3", "name": "Shared Mem",   "instances": 2, "available": 0},
        ]
    },
    "safe": {
        "processes": [
            {"pid": "P1", "name": "Reader A", "priority": 2, "holds": ["R1"], "waiting_for": []},
            {"pid": "P2", "name": "Reader B", "priority": 1, "holds": [],     "waiting_for": ["R1"]},
            {"pid": "P3", "name": "Writer",   "priority": 3, "holds": ["R2"], "waiting_for": []},
        ],
        "resources": [
            {"rid": "R1", "name": "Read Lock",  "instances": 1, "available": 0},
            {"rid": "R2", "name": "Write Lock", "instances": 1, "available": 0},
        ]
    }
}


def _canonical_cycle(cycle: list[str]) -> tuple:
    """
    Return a canonical (hashable) form of a cycle by rotating it so that
    the lexicographically smallest node comes first.
    This correctly deduplicates cycles found from different starting points
    while preserving ordering (unlike frozenset which discards order).
    """
    if not cycle:
        return ()
    # Drop the repeated tail node before canonicalising
    body = cycle[:-1] if cycle[0] == cycle[-1] else cycle
    if not body:
        return ()
    min_idx = body.index(min(body))
    rotated = body[min_idx:] + body[:min_idx]
    return tuple(rotated)


class DeadlockEngine:

    def get_scenario(self, scenario_id: str) -> Optional[dict]:
        s = SCENARIOS.get(scenario_id)
        if not s:
            return None
        return s

    # ── Graph Construction ───────────────────────────────────────────────────

    def build_rag(self, state: SystemState) -> dict:
        """
        Build a Resource Allocation Graph as an adjacency list.
        Standard Convention used for cycle detection:
          P holds R  →  edge  R → P  (assignment edge)
          P waits R  →  edge  P → R  (request edge)
        """
        graph: dict[str, list[str]] = defaultdict(list)
        edges: list[dict] = []

        for proc in state.processes:
            pid = proc["pid"]
            for rid in proc.get("holds", []):
                graph[rid].append(pid)
                edges.append({"from": rid, "to": pid, "type": "holds"})
            for rid in proc.get("waiting_for", []):
                graph[pid].append(rid)
                edges.append({"from": pid, "to": rid, "type": "waits"})

        return {"graph": dict(graph), "edges": edges}

    # ── Cycle Detection (Iterative DFS) ─────────────────────────────────────

    def detect_cycles(self, state: SystemState) -> list[list[str]]:
        """
        Iterative DFS-based cycle detection on the Resource Allocation Graph.
        Returns a list of unique cycles, each represented as a list of node IDs
        where the first and last elements are the same (closed cycle).

        Using an iterative approach avoids Python's default recursion limit
        (~1000 frames) which would crash on graphs with > ~500 nodes.
        """
        rag = self.build_rag(state)
        graph = rag["graph"]

        all_nodes: list[str] = (
            [p["pid"] for p in state.processes]
            + [r["rid"] for r in state.resources]
        )

        visited: set[str] = set()
        cycles: list[list[str]] = []
        seen_keys: set[tuple] = set()

        for start in all_nodes:
            if start in visited:
                continue

            # Iterative DFS using an explicit stack.
            # Each stack frame: (node, iterator-over-neighbors, current-path)
            # We store the recursion stack as a list and a set for O(1) lookup.
            rec_stack: list[str] = []
            rec_set: set[str] = set()
            path: list[str] = []

            # Stack items: (node, neighbor_index)
            dfs_stack: list[tuple[str, int]] = [(start, 0)]
            visited.add(start)
            rec_stack.append(start)
            rec_set.add(start)
            path.append(start)

            while dfs_stack:
                node, nb_idx = dfs_stack[-1]
                neighbors = graph.get(node, [])

                if nb_idx < len(neighbors):
                    dfs_stack[-1] = (node, nb_idx + 1)
                    neighbor = neighbors[nb_idx]

                    if neighbor not in visited:
                        visited.add(neighbor)
                        rec_stack.append(neighbor)
                        rec_set.add(neighbor)
                        path.append(neighbor)
                        dfs_stack.append((neighbor, 0))
                    elif neighbor in rec_set:
                        # Back edge → cycle found
                        cycle_start = path.index(neighbor)
                        cycle = path[cycle_start:] + [neighbor]
                        key = _canonical_cycle(cycle)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            cycles.append(cycle)
                else:
                    # Finished all neighbors — backtrack
                    dfs_stack.pop()
                    rec_stack.pop()
                    rec_set.discard(node)
                    path.pop()

        return cycles

    # ── DFS Trace (Educational Mode) ─────────────────────────────────────────

    def dfs_trace(self, state: SystemState) -> dict:
        """
        Produces a step-by-step trace of the DFS cycle detection.
        Each step records: current node, stack, visited set, action.
        Uses iterative DFS to be consistent with detect_cycles.
        """
        rag = self.build_rag(state)
        graph = rag["graph"]
        all_nodes = [p["pid"] for p in state.processes] + [r["rid"] for r in state.resources]

        visited: set[str] = set()
        steps: list[dict] = []
        cycles_found: list[list[str]] = []
        seen_keys: set[tuple] = set()

        for start in all_nodes:
            if start in visited:
                continue

            rec_stack: list[str] = []
            rec_set: set[str] = set()
            path: list[str] = []
            dfs_stack: list[tuple[str, int]] = [(start, 0)]

            visited.add(start)
            rec_stack.append(start)
            rec_set.add(start)
            path.append(start)

            steps.append({
                "action": "visit",
                "node": start,
                "stack": list(rec_stack),
                "visited": list(visited),
                "depth": 0,
                "message": f"Visiting {start}, added to recursion stack"
            })

            while dfs_stack:
                node, nb_idx = dfs_stack[-1]
                depth = len(dfs_stack) - 1
                neighbors = graph.get(node, [])

                if nb_idx < len(neighbors):
                    dfs_stack[-1] = (node, nb_idx + 1)
                    neighbor = neighbors[nb_idx]

                    if neighbor not in visited:
                        steps.append({
                            "action": "explore",
                            "node": node,
                            "neighbor": neighbor,
                            "stack": list(rec_stack),
                            "visited": list(visited),
                            "depth": depth,
                            "message": f"Exploring edge {node} → {neighbor}"
                        })
                        visited.add(neighbor)
                        rec_stack.append(neighbor)
                        rec_set.add(neighbor)
                        path.append(neighbor)
                        dfs_stack.append((neighbor, 0))
                        steps.append({
                            "action": "visit",
                            "node": neighbor,
                            "stack": list(rec_stack),
                            "visited": list(visited),
                            "depth": depth + 1,
                            "message": f"Visiting {neighbor}, added to recursion stack"
                        })
                    elif neighbor in rec_set:
                        cycle_start = path.index(neighbor)
                        cycle = path[cycle_start:] + [neighbor]
                        key = _canonical_cycle(cycle)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            cycles_found.append(cycle)
                        steps.append({
                            "action": "cycle_found",
                            "node": node,
                            "neighbor": neighbor,
                            "cycle": cycle,
                            "stack": list(rec_stack),
                            "visited": list(visited),
                            "depth": depth,
                            "message": f"CYCLE DETECTED: back edge {node} → {neighbor} | Path: {' → '.join(cycle)}"
                        })
                else:
                    dfs_stack.pop()
                    rec_stack.pop()
                    rec_set.discard(node)
                    path.pop()
                    steps.append({
                        "action": "backtrack",
                        "node": node,
                        "stack": list(rec_stack),
                        "visited": list(visited),
                        "depth": depth,
                        "message": f"Backtracking from {node}"
                    })

        return {
            "steps": steps,
            "cycles": cycles_found,
            "total_steps": len(steps),
            "has_deadlock": len(cycles_found) > 0
        }

    # ── Banker's Algorithm (Multi-Instance) ──────────────────────────────────

    def bankers_safe_sequence(self, state: SystemState) -> Optional[list[str]]:
        """
        Banker's Algorithm to find a safe sequence.
        Returns the safe sequence or None if the system is in an unsafe state.

        Fix: the outer loop now runs n times (not just 1), matching the
        standard Banker's algorithm which needs up to n iterations to find
        all processes that can run to completion.
        """
        processes = state.processes
        resources = state.resources

        if not processes or not resources:
            return []

        res_index = {r["rid"]: i for i, r in enumerate(resources)}
        n = len(processes)
        m = len(resources)

        available = [r.get("available", r["instances"]) for r in resources]
        allocation = [[0] * m for _ in range(n)]
        need = [[0] * m for _ in range(n)]

        for i, proc in enumerate(processes):
            for rid in proc.get("holds", []):
                if rid in res_index:
                    allocation[i][res_index[rid]] += 1

        for i, proc in enumerate(processes):
            # max_need = currently held + currently waiting (simplified model)
            for rid in proc.get("holds", []):
                if rid in res_index:
                    need[i][res_index[rid]] += 1
            for rid in proc.get("waiting_for", []):
                if rid in res_index:
                    need[i][res_index[rid]] += 1

        finish = [False] * n
        safe_seq: list[str] = []
        work = list(available)

        # BUG FIX: outer loop must run n times; previously was only 1 pass
        # which caused it to miss processes whose needs could only be met
        # after earlier processes finished and returned their resources.
        for _ in range(n):
            progress = False
            for i, proc in enumerate(processes):
                if not finish[i] and all(need[i][j] <= work[j] for j in range(m)):
                    work = [work[j] + allocation[i][j] for j in range(m)]
                    finish[i] = True
                    safe_seq.append(proc["pid"])
                    progress = True
            if not progress:
                break  # No progress possible — unsafe state

        return safe_seq if all(finish) else None

    # ── Resolution Strategies ────────────────────────────────────────────────

    def generate_resolution(self, state: SystemState, cycles: list[list[str]]) -> dict:
        """
        Generates concrete resolution strategies for detected cycles.
        """
        if not cycles:
            return {"resolved": True, "strategies": []}

        pid_set = {p["pid"] for p in state.processes}
        affected_procs = list({
            n for c in cycles for n in c if n in pid_set
        })

        # Guard: if no affected processes found (e.g. resource-only cycle), bail gracefully
        proc_candidates = [
            p for p in state.processes if p["pid"] in affected_procs
        ]
        if not proc_candidates:
            return {
                "resolved": False,
                "strategies": [],
                "recommended_process_to_terminate": None,
                "cycles_before": len(cycles),
                "cycles_after_termination": len(cycles),
            }

        # Sort by priority (lowest priority = best termination candidate)
        proc_by_priority = sorted(proc_candidates, key=lambda p: p.get("priority", 1))
        victim = proc_by_priority[0]

        resource_order = " < ".join(r["rid"] for r in state.resources)

        strategies = [
            {
                "id": "terminate",
                "name": "Process Termination",
                "description": "Abort the lowest-priority process in each cycle to release its resources.",
                "recommended_action": f"Terminate {victim['pid']} ({victim['name']}) — priority {victim.get('priority', 1)}",
                "cost": "low",
                "risk": "medium",
                "steps": [
                    f"Identify lowest-priority process: {victim['pid']}",
                    "Save checkpoint/state for rollback",
                    f"Terminate {victim['pid']} and release held resources: {victim.get('holds', [])}",
                    "Re-run deadlock detection on remaining processes",
                    "Resume waiting processes that can now proceed"
                ]
            },
            {
                "id": "preempt",
                "name": "Resource Preemption",
                "description": "Forcibly take a resource from a waiting process and assign it elsewhere.",
                "recommended_action": f"Preempt resources held by {victim['pid']}",
                "cost": "medium",
                "risk": "high",
                "steps": [
                    f"Select victim process: {victim['pid']}",
                    "Roll process back to last safe checkpoint",
                    f"Preempt resources: {victim.get('holds', [])}",
                    "Allocate preempted resources to the waiting process",
                    "Restart victim process from checkpoint"
                ]
            },
            {
                "id": "lock_ordering",
                "name": "Lock Ordering (Prevention)",
                "description": "Enforce a global resource ordering to prevent circular wait from forming.",
                "recommended_action": "Assign numeric IDs to resources; require processes to acquire in ascending order",
                "cost": "low",
                "risk": "low",
                "steps": [
                    f"Assign a global order to all resources: {resource_order}",
                    "Audit all processes to enforce acquisition order",
                    "Refactor code that acquires resources out of order",
                    "Add runtime assertions to catch ordering violations in development"
                ]
            },
            {
                "id": "timeout",
                "name": "Timeout & Retry (Wait-Die / Wound-Wait)",
                "description": "Use timestamps to decide which process waits and which is aborted when requesting a held resource.",
                "recommended_action": "Apply wound-wait: older processes wound (abort) younger ones",
                "cost": "low",
                "risk": "low",
                "steps": [
                    "Assign timestamps to each process at creation",
                    "When process Pi requests a resource held by Pj:",
                    "  If timestamp(Pi) < timestamp(Pj): Pi wounds Pj (Pj aborts and retries)",
                    "  Else: Pi waits",
                    "Aborted processes restart with their original timestamp"
                ]
            }
        ]

        # Simulate post-termination state
        surviving = [p for p in state.processes if p["pid"] != victim["pid"]]
        post_state = SystemState(processes=surviving, resources=state.resources)
        remaining_cycles = self.detect_cycles(post_state)

        return {
            "resolved": len(remaining_cycles) == 0,
            "cycles_before": len(cycles),
            "cycles_after_termination": len(remaining_cycles),
            "recommended_process_to_terminate": victim["pid"],
            "strategies": strategies
        }

    # ── Banker's Step Trace (Educational) ───────────────────────────────────

    def bankers_step_trace(self, state: SystemState) -> dict:
        """
        Run the Banker's safety algorithm and return every inner-loop iteration
        as an educational step so the UI can animate it.
        """
        processes = state.processes
        resources = state.resources
        if not processes or not resources:
            return {"steps": [], "safe": True, "safe_sequence": []}

        res_index = {r["rid"]: i for i, r in enumerate(resources)}
        n, m = len(processes), len(resources)

        available = [r.get("available", r["instances"]) for r in resources]
        allocation = [[0] * m for _ in range(n)]
        need       = [[0] * m for _ in range(n)]

        for i, proc in enumerate(processes):
            for rid in proc.get("holds", []):
                if rid in res_index:
                    allocation[i][res_index[rid]] += 1
            for rid in proc.get("holds", []):
                if rid in res_index:
                    need[i][res_index[rid]] += 1
            for rid in proc.get("waiting_for", []):
                if rid in res_index:
                    need[i][res_index[rid]] += 1

        finish = [False] * n
        work   = list(available)
        safe_seq: list[str] = []
        steps: list[dict] = []
        rid_names = [r["rid"] for r in resources]

        steps.append({
            "phase": "init",
            "message": "Initialise Work = Available",
            "work": list(work),
            "resources": rid_names,
            "finish": list(finish),
            "safe_sequence": [],
        })

        for iteration in range(n):
            found_any = False
            for i, proc in enumerate(processes):
                if finish[i]:
                    continue
                can_run = all(need[i][j] <= work[j] for j in range(m))
                steps.append({
                    "phase": "check",
                    "iteration": iteration,
                    "process": proc["pid"],
                    "need": list(need[i]),
                    "work": list(work),
                    "resources": rid_names,
                    "can_run": can_run,
                    "finish": list(finish),
                    "safe_sequence": list(safe_seq),
                    "message": (
                        f"Check {proc['pid']}: Need {need[i]} ≤ Work {work}? → {'YES ✓' if can_run else 'NO ✗'}"
                    ),
                })
                if can_run:
                    work = [work[j] + allocation[i][j] for j in range(m)]
                    finish[i] = True
                    safe_seq.append(proc["pid"])
                    found_any = True
                    steps.append({
                        "phase": "grant",
                        "process": proc["pid"],
                        "allocation": list(allocation[i]),
                        "work": list(work),
                        "resources": rid_names,
                        "finish": list(finish),
                        "safe_sequence": list(safe_seq),
                        "message": (
                            f"{proc['pid']} can finish → Work += Allocation {allocation[i]} → Work = {work}"
                        ),
                    })
            if not found_any:
                break

        is_safe = all(finish)
        steps.append({
            "phase": "result",
            "safe": is_safe,
            "safe_sequence": list(safe_seq) if is_safe else [],
            "message": (
                f"System is SAFE. Safe sequence: {' → '.join(safe_seq)}"
                if is_safe
                else "System is UNSAFE — deadlock detected."
            ),
        })
        return {"steps": steps, "safe": is_safe, "safe_sequence": safe_seq if is_safe else []}

    # ── Banker's Request Simulator ───────────────────────────────────────────

    def simulate_request(
        self,
        state: SystemState,
        pid: str,
        requested: dict,          # {rid: count}
    ) -> dict:
        """
        Banker's resource-request algorithm for a single process.
        1. Check request ≤ need[i]
        2. Check request ≤ available
        3. Pretend to allocate
        4. Run safety check
        5. If safe → grant (return new state); else → deny (rollback)
        """
        processes = state.processes
        resources = state.resources
        res_index = {r["rid"]: i for i, r in enumerate(resources)}
        n, m = len(processes), len(resources)

        proc_index = next((i for i, p in enumerate(processes) if p["pid"] == pid), None)
        if proc_index is None:
            return {"granted": False, "reason": f"Process {pid} not found."}

        available  = [r.get("available", r["instances"]) for r in resources]
        allocation = [[0] * m for _ in range(n)]
        need       = [[0] * m for _ in range(n)]

        for i, proc in enumerate(processes):
            for rid in proc.get("holds", []):
                if rid in res_index:
                    allocation[i][res_index[rid]] += 1
            for rid in proc.get("holds", []):
                if rid in res_index:
                    need[i][res_index[rid]] += 1
            for rid in proc.get("waiting_for", []):
                if rid in res_index:
                    need[i][res_index[rid]] += 1

        req_vec = [requested.get(r["rid"], 0) for r in resources]

        # Step 1: request ≤ need?
        for j in range(m):
            if req_vec[j] > need[proc_index][j]:
                return {
                    "granted": False,
                    "reason": f"Request exceeds declared need for {resources[j]['rid']} "
                              f"(requested {req_vec[j]}, need {need[proc_index][j]}).",
                    "step_failed": "need_check",
                }

        # Step 2: request ≤ available?
        for j in range(m):
            if req_vec[j] > available[j]:
                return {
                    "granted": False,
                    "reason": f"Insufficient available instances of {resources[j]['rid']} "
                              f"(requested {req_vec[j]}, available {available[j]}). Process must wait.",
                    "step_failed": "availability_check",
                }

        # Step 3: pretend-allocate
        for j in range(m):
            available[j]               -= req_vec[j]
            allocation[proc_index][j]  += req_vec[j]
            need[proc_index][j]        -= req_vec[j]

        # Step 4: safety check on pretend state
        finish = [False] * n
        work   = list(available)
        safe_seq: list[str] = []
        for _ in range(n):
            for i, proc in enumerate(processes):
                if not finish[i] and all(need[i][j] <= work[j] for j in range(m)):
                    work = [work[j] + allocation[i][j] for j in range(m)]
                    finish[i] = True
                    safe_seq.append(proc["pid"])

        if not all(finish):
            return {
                "granted": False,
                "reason": "Granting this request would lead to an UNSAFE state (potential deadlock).",
                "step_failed": "safety_check",
                "safe_sequence": None,
            }

        # Build new state after grant
        new_processes = []
        for proc in processes:
            p = dict(proc)
            if p["pid"] == pid:
                new_holds = list(p.get("holds", []))
                new_waits = list(p.get("waiting_for", []))
                for rid, cnt in requested.items():
                    new_holds.extend([rid] * cnt)
                    for _ in range(min(cnt, new_waits.count(rid))):
                        new_waits.remove(rid)
                p["holds"] = new_holds
                p["waiting_for"] = new_waits
            new_processes.append(p)

        new_resources = []
        for r in resources:
            nr = dict(r)
            nr["available"] = max(0, nr.get("available", nr["instances"]) - requested.get(r["rid"], 0))
            new_resources.append(nr)

        return {
            "granted": True,
            "reason": "Request granted — system remains in a SAFE state.",
            "safe_sequence": safe_seq,
            "new_state": {"processes": new_processes, "resources": new_resources},
        }

    # ── Auto-Resolve via Banker's ────────────────────────────────────────────

    def resolve_via_bankers(self, state: SystemState) -> dict:
        """
        Iteratively try to grant each waiting process's pending resource requests
        using the Banker's safety check. Stop when no more grants are possible.
        Returns a step-log of every grant/deny decision and the final state.
        """
        current_processes = [dict(p) for p in state.processes]
        current_resources = [dict(r) for r in state.resources]
        steps: list[dict] = []
        granted_any = True
        round_num = 0

        while granted_any:
            granted_any = False
            round_num += 1
            for proc in current_processes:
                pid = proc["pid"]
                waiting = proc.get("waiting_for", [])
                if not waiting:
                    continue
                # Build a request of 1 unit per waited resource (simple model)
                requested = {}
                for rid in set(waiting):
                    requested[rid] = waiting.count(rid)

                temp_state = SystemState(
                    processes=current_processes,
                    resources=current_resources,
                )
                result = self.simulate_request(temp_state, pid, requested)
                step_entry = {
                    "round": round_num,
                    "process": pid,
                    "requested": requested,
                    "granted": result["granted"],
                    "reason": result["reason"],
                }
                if result["granted"]:
                    new_s = result["new_state"]
                    current_processes = new_s["processes"]
                    current_resources = new_s["resources"]
                    step_entry["safe_sequence"] = result.get("safe_sequence")
                    granted_any = True
                steps.append(step_entry)

        final_state = SystemState(processes=current_processes, resources=current_resources)
        remaining_cycles = self.detect_cycles(final_state)
        safe_seq = self.bankers_safe_sequence(final_state)

        return {
            "steps": steps,
            "resolved": len(remaining_cycles) == 0,
            "remaining_cycles": remaining_cycles,
            "final_safe_sequence": safe_seq,
            "final_state": {"processes": current_processes, "resources": current_resources},
        }

    # ── Full Analysis ────────────────────────────────────────────────────────

    def analyze(self, state: SystemState) -> dict:
        rag = self.build_rag(state)
        cycles = self.detect_cycles(state)
        safe_seq = self.bankers_safe_sequence(state)

        pid_set = {p["pid"] for p in state.processes}
        rid_set = {r["rid"] for r in state.resources}

        affected_procs = list({n for c in cycles for n in c if n in pid_set})
        affected_res   = list({n for c in cycles for n in c if n in rid_set})

        # Allocation matrix
        allocation_matrix = {}
        for proc in state.processes:
            allocation_matrix[proc["pid"]] = {
                "holds":       proc.get("holds", []),
                "waiting_for": proc.get("waiting_for", [])
            }

        resolution = self.generate_resolution(state, cycles) if cycles else {"strategies": []}

        return {
            "has_deadlock": len(cycles) > 0,
            "cycles": cycles,
            "affected_processes": affected_procs,
            "affected_resources": affected_res,
            "resolution_strategies": resolution.get("strategies", []),
            "safe_sequence": safe_seq,
            "allocation_matrix": allocation_matrix,
            "graph_edges": rag["edges"]
        }
