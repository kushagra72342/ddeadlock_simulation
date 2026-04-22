"""
DeadlockX Heuristics Engine
============================
ADDITIVE module — zero changes to core DFS / Banker's logic.

Provides:
  - Starvation detection  (process indefinitely blocked by lower-priority holders)
  - Livelock heuristic    (state-hash cycle detection across simulation history)
  - Early-warning scores  (per-process risk_score 0.0–1.0 before full cycle forms)
  - Human-readable explanation generator
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import SystemState


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class StarvationReport:
    pid: str
    name: str
    starving_for: list[str]          # resource IDs it's waiting on
    blocked_by: list[str]            # PIDs holding those resources
    severity: str                    # "low" | "medium" | "high"
    reason: str                      # human-readable


@dataclass
class LivelockReport:
    suspected: bool
    repeating_processes: list[str]
    confidence: float                # 0.0 – 1.0
    pattern_length: int              # how many states in the repeated cycle
    explanation: str


@dataclass
class RiskReport:
    scores: dict[str, float]         # pid → 0.0–1.0
    high_risk_pairs: list[tuple[str, str]]  # pairs likely to deadlock
    global_risk: float               # system-wide 0.0–1.0
    warning: str | None


class HeuristicsEngine:
    """
    All methods are pure functions on SystemState — no side effects.
    Thread-safe for concurrent use.
    """

    # ── Starvation Detection ─────────────────────────────────────────────────

    def detect_starvation(
        self,
        state: "SystemState",
        priority_weight: bool = True,
    ) -> list[StarvationReport]:
        """
        A process is considered starving when:
          1. It is waiting_for at least one resource.
          2. Every process holding that resource has HIGHER priority
             (lower priority number = higher urgency — but we model it as
              higher priority value = more important, so starver has LOWER value).
          3. It holds nothing itself (cannot make any forward progress).

        If priority_weight=False, any blocked process with no held resources
        is reported regardless of relative priorities.
        """
        reports: list[StarvationReport] = []

        # Build holder map: rid → list of PIDs holding it
        holder_map: dict[str, list[str]] = {}
        prio_map: dict[str, int] = {}
        name_map: dict[str, str] = {}

        for proc in state.processes:
            prio_map[proc["pid"]] = proc.get("priority", 1)
            name_map[proc["pid"]] = proc.get("name", proc["pid"])
            for rid in proc.get("holds", []):
                holder_map.setdefault(rid, []).append(proc["pid"])

        for proc in state.processes:
            pid = proc["pid"]
            waiting = proc.get("waiting_for", [])
            holds   = proc.get("holds", [])
            if not waiting:
                continue

            # Only flag if the process holds nothing (zero forward progress)
            if holds:
                continue

            starving_for: list[str] = []
            blocked_by:   list[str] = []

            for rid in waiting:
                holders = holder_map.get(rid, [])
                if not holders:
                    continue  # resource is free — not starvation

                if priority_weight:
                    # Starvation: ALL holders have strictly higher priority
                    my_prio = prio_map[pid]
                    if all(prio_map.get(h, 1) > my_prio for h in holders):
                        starving_for.append(rid)
                        blocked_by.extend(holders)
                else:
                    starving_for.append(rid)
                    blocked_by.extend(holders)

            if not starving_for:
                continue

            # Severity based on how many resources it's starved of
            n = len(starving_for)
            severity = "high" if n >= 3 else "medium" if n == 2 else "low"
            blocked_names = [name_map.get(h, h) for h in set(blocked_by)]
            reason = (
                f"{name_map[pid]} (priority {prio_map[pid]}) is waiting for "
                f"{', '.join(starving_for)} but all holders "
                f"({', '.join(blocked_names)}) have higher priority — "
                f"this process may never be scheduled."
            )

            reports.append(StarvationReport(
                pid=pid,
                name=name_map[pid],
                starving_for=starving_for,
                blocked_by=list(set(blocked_by)),
                severity=severity,
                reason=reason,
            ))

        return reports

    # ── Livelock Heuristic ────────────────────────────────────────────────────

    def detect_livelock(
        self,
        state_history: list["SystemState"],
        window: int = 6,
        min_repeats: int = 2,
    ) -> LivelockReport:
        """
        Livelock: processes keep changing state (acquiring/releasing) but
        no net progress is made — the same state pattern repeats.

        Algorithm:
          1. Hash each state in history as a frozenset of (pid, holds_tuple, waits_tuple).
          2. Slide a window of size `window/2` looking for repeated hash sequences.
          3. If a sub-sequence repeats ≥ min_repeats times → suspect livelock.

        Requires at least `window` states in history for meaningful detection.
        """
        if len(state_history) < window:
            return LivelockReport(
                suspected=False,
                repeating_processes=[],
                confidence=0.0,
                pattern_length=0,
                explanation="Insufficient history for livelock detection."
            )

        def _hash_state(s: "SystemState") -> str:
            key = frozenset(
                (p["pid"],
                 tuple(sorted(p.get("holds", []))),
                 tuple(sorted(p.get("waiting_for", []))))
                for p in s.processes
            )
            return hashlib.md5(json.dumps(sorted(str(k) for k in key)).encode()).hexdigest()

        hashes = [_hash_state(s) for s in state_history[-window:]]

        # Look for repeating sub-sequences of length 2..window//2
        pattern_len = 0
        repeats = 0
        for plen in range(2, window // 2 + 1):
            pattern = hashes[:plen]
            count = 0
            for i in range(0, len(hashes) - plen + 1, plen):
                if hashes[i:i + plen] == pattern:
                    count += 1
            if count >= min_repeats:
                pattern_len = plen
                repeats = count
                break

        if pattern_len == 0:
            return LivelockReport(
                suspected=False,
                repeating_processes=[],
                confidence=0.0,
                pattern_length=0,
                explanation="No repeating state pattern detected."
            )

        # Find which processes change between repeated states
        repeating = set()
        s0 = state_history[-window]
        s1 = state_history[-window + 1] if len(state_history) > window else s0
        h0 = {p["pid"]: (tuple(sorted(p.get("holds", []))), tuple(sorted(p.get("waiting_for", []))))
              for p in s0.processes}
        h1 = {p["pid"]: (tuple(sorted(p.get("holds", []))), tuple(sorted(p.get("waiting_for", []))))
              for p in s1.processes}
        for pid in h0:
            if h0.get(pid) != h1.get(pid):
                repeating.add(pid)

        confidence = min(1.0, repeats / (window / pattern_len))
        explanation = (
            f"State pattern of length {pattern_len} repeated {repeats}x in the last "
            f"{window} steps. Processes {', '.join(sorted(repeating))} are changing "
            f"state with no net progress — likely livelock."
        )

        return LivelockReport(
            suspected=True,
            repeating_processes=sorted(repeating),
            confidence=round(confidence, 2),
            pattern_length=pattern_len,
            explanation=explanation,
        )

    # ── Early-Warning Risk Scores ─────────────────────────────────────────────

    def compute_risk_scores(self, state: "SystemState") -> RiskReport:
        """
        Per-process risk score (0.0–1.0) estimating how likely a process is
        to become involved in a deadlock, BEFORE any cycle has fully formed.

        Factors:
          - Fraction of total resources the process is waiting for       (0–0.4)
          - Whether any process it's waiting on is also waiting for it   (+0.4)
          - Whether it holds resources others are waiting for            (+0.2)
          - Priority inversions (high-prio process waiting on low-prio)  (+0.1 each)
        """
        total_res = max(len(state.resources), 1)
        prio_map  = {p["pid"]: p.get("priority", 1) for p in state.processes}

        # Build reverse maps
        waits_map: dict[str, list[str]] = {}   # rid → [pids waiting]
        holds_map: dict[str, list[str]] = {}   # rid → [pids holding]
        for proc in state.processes:
            for rid in proc.get("waiting_for", []):
                waits_map.setdefault(rid, []).append(proc["pid"])
            for rid in proc.get("holds", []):
                holds_map.setdefault(rid, []).append(proc["pid"])

        scores: dict[str, float] = {}

        for proc in state.processes:
            pid     = proc["pid"]
            waiting = proc.get("waiting_for", [])
            holds   = proc.get("holds", [])
            score   = 0.0

            # Factor 1: breadth of waiting
            score += min(0.4, len(waiting) / total_res * 0.8)

            # Factor 2: mutual wait (potential cycle seed)
            for rid in waiting:
                for holder in holds_map.get(rid, []):
                    if holder == pid:
                        continue
                    # Does the holder wait for anything this process holds?
                    holder_proc = next((p for p in state.processes if p["pid"] == holder), None)
                    if holder_proc:
                        holder_waits = holder_proc.get("waiting_for", [])
                        if any(r in holds for r in holder_waits):
                            score += 0.4
                            break

            # Factor 3: is a contention point (others waiting for my resources)
            for rid in holds:
                if len(waits_map.get(rid, [])) > 1:
                    score += 0.15
                    break
            elif holds and any(waits_map.get(rid) for rid in holds):
                score += 0.1

            # Factor 4: priority inversion
            my_prio = prio_map[pid]
            for rid in waiting:
                for holder in holds_map.get(rid, []):
                    if prio_map.get(holder, 1) < my_prio:
                        score += 0.1
                        break

            scores[pid] = round(min(1.0, score), 3)

        # Find high-risk pairs (both score ≥ 0.7 and mutually blocked)
        high_risk_pairs: list[tuple[str, str]] = []
        pids = [p["pid"] for p in state.processes]
        for i, a in enumerate(pids):
            for b in pids[i + 1:]:
                if scores.get(a, 0) >= 0.7 and scores.get(b, 0) >= 0.7:
                    proc_a = next(p for p in state.processes if p["pid"] == a)
                    proc_b = next(p for p in state.processes if p["pid"] == b)
                    a_waits = set(proc_a.get("waiting_for", []))
                    b_holds = set(proc_b.get("holds", []))
                    b_waits = set(proc_b.get("waiting_for", []))
                    a_holds = set(proc_a.get("holds", []))
                    if (a_waits & b_holds) and (b_waits & a_holds):
                        high_risk_pairs.append((a, b))

        global_risk = round(max(scores.values(), default=0.0), 3)
        warning: str | None = None
        if global_risk >= 0.9:
            warning = "CRITICAL: System at imminent deadlock risk. Immediate intervention recommended."
        elif global_risk >= 0.7:
            warning = "WARNING: High contention detected. Monitor closely."
        elif global_risk >= 0.5:
            warning = "CAUTION: Moderate resource contention. Risk of deadlock forming."

        return RiskReport(
            scores=scores,
            high_risk_pairs=high_risk_pairs,
            global_risk=global_risk,
            warning=warning,
        )

    # ── Human-Readable Explanation ────────────────────────────────────────────

    def explain_deadlock(self, state: "SystemState", cycles: list[list[str]]) -> str:
        """
        Generate a plain-English explanation of detected deadlocks.
        """
        if not cycles:
            return "No deadlock detected. All processes can eventually complete."

        pid_set  = {p["pid"] for p in state.processes}
        name_map = {p["pid"]: p.get("name", p["pid"]) for p in state.processes}
        rname    = {r["rid"]: r.get("name", r["rid"]) for r in state.resources}

        lines = [f"{'=' * 50}", f"  DEADLOCK DETECTED — {len(cycles)} cycle(s) found", f"{'=' * 50}"]

        for i, cycle in enumerate(cycles, 1):
            procs_in_cycle = [n for n in cycle if n in pid_set]
            res_in_cycle   = [n for n in cycle if n not in pid_set]
            desc = " → ".join(
                name_map.get(n, rname.get(n, n)) for n in cycle
            )
            lines.append(f"\nCycle {i}: {desc}")
            lines.append(
                f"  {len(procs_in_cycle)} processes ({', '.join(procs_in_cycle)}) "
                f"are each holding a resource the next one needs, "
                f"forming a circular wait that can never resolve on its own."
            )
            lines.append(
                f"  Resources involved: {', '.join(f'{r} ({rname.get(r, r)})' for r in res_in_cycle)}"
            )

        lines.append(
            "\nTo resolve this deadlock, at least one process must release its "
            "resources. The resolution panel shows ranked options ordered by cost."
        )
        return "\n".join(lines)
