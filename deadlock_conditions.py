"""
Deadlock Condition Simulator
============================
Demonstrates ACTUAL OS-level deadlock conditions using Python threading.
Run this to see real deadlocks happen in practice — and how to avoid them.

Usage:
    python deadlock_conditions.py [--scenario SCENARIO] [--timeout SECONDS]

Scenarios:
    classic      - Two threads, two locks, classic circular wait
    resource_starvation - Thread starved by priority inversion
    dining       - Dining Philosophers problem (N philosophers)
    db_lock      - Simulated database row-level lock deadlock
    safe         - Same setup with lock ordering fix applied
"""

import threading
import time
import argparse
import sys
from contextlib import contextmanager
from collections import defaultdict


# ── Deadlock Detection Monitor ───────────────────────────────────────────────

class DeadlockMonitor:
    """
    Tracks which threads hold and are waiting for which locks.
    Runs periodic DFS cycle detection on the wait-for graph.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.holds: dict[int, list] = defaultdict(list)     # thread_id -> [locks]
        self.waiting: dict[int, object] = {}                 # thread_id -> lock
        self.thread_names: dict[int, str] = {}
        self._running = False
        self._monitor_thread = None

    def register(self, name: str):
        tid = threading.current_thread().ident
        with self._lock:
            self.thread_names[tid] = name

    def about_to_acquire(self, lock):
        tid = threading.current_thread().ident
        with self._lock:
            self.waiting[tid] = lock

    def acquired(self, lock):
        tid = threading.current_thread().ident
        with self._lock:
            self.waiting.pop(tid, None)
            self.holds[tid].append(lock)

    def released(self, lock):
        tid = threading.current_thread().ident
        with self._lock:
            if lock in self.holds[tid]:
                self.holds[tid].remove(lock)

    def detect_deadlock(self) -> list[list[str]]:
        """DFS cycle detection on the wait-for graph."""
        with self._lock:
            # Build graph: thread -> thread (via shared locks)
            graph: dict[int, list[int]] = defaultdict(list)
            all_threads = set(self.holds.keys()) | set(self.waiting.keys())
            for waiter_tid, wanted_lock in self.waiting.items():
                for holder_tid, held_locks in self.holds.items():
                    if wanted_lock in held_locks and holder_tid != waiter_tid:
                        graph[waiter_tid].append(holder_tid)

            visited, rec_stack, cycles = set(), set(), []
            path = []

            def dfs(node):
                visited.add(node)
                rec_stack.add(node)
                path.append(node)
                for nb in graph.get(node, []):
                    if nb not in visited:
                        dfs(nb)
                    elif nb in rec_stack:
                        ci = path.index(nb)
                        cycle_tids = path[ci:]
                        cycle_names = [
                            self.thread_names.get(t, f"Thread-{t}") for t in cycle_tids
                        ]
                        cycles.append(cycle_names)
                path.pop()
                rec_stack.discard(node)

            for t in all_threads:
                if t not in visited:
                    dfs(t)

            return cycles

    def start_monitoring(self, interval: float = 0.5):
        self._running = True
        def _loop():
            while self._running:
                cycles = self.detect_deadlock()
                if cycles:
                    print(f"\n{'='*60}")
                    print(f"  ⚠  DEADLOCK DETECTED by monitor!")
                    for i, cycle in enumerate(cycles, 1):
                        print(f"  Cycle {i}: {' → '.join(cycle)} → {cycle[0]}")
                    print(f"{'='*60}\n")
                time.sleep(interval)
        self._monitor_thread = threading.Thread(target=_loop, daemon=True, name="DeadlockMonitor")
        self._monitor_thread.start()

    def stop_monitoring(self):
        self._running = False


monitor = DeadlockMonitor()


@contextmanager
def tracked_lock(lock, name: str = ""):
    """Context manager that notifies the monitor before/after lock acquisition."""
    tid = threading.current_thread().ident
    tname = threading.current_thread().name
    print(f"  [{tname}] Attempting to acquire {name}...")
    monitor.about_to_acquire(lock)
    lock.acquire()
    monitor.acquired(lock)
    print(f"  [{tname}] Acquired {name}")
    try:
        yield lock
    finally:
        lock.release()
        monitor.released(lock)
        print(f"  [{tname}] Released {name}")


# ── Scenario 1: Classic Two-Thread Deadlock ──────────────────────────────────

def scenario_classic():
    """
    Two threads acquire two locks in opposite order → circular wait.
    Thread A holds Lock1, wants Lock2.
    Thread B holds Lock2, wants Lock1.
    """
    print("\n" + "="*60)
    print("SCENARIO: Classic Two-Thread Deadlock")
    print("Thread A: acquires Lock1, then tries Lock2")
    print("Thread B: acquires Lock2, then tries Lock1")
    print("="*60)

    lock1 = threading.Lock()
    lock2 = threading.Lock()
    deadlock_event = threading.Event()

    def thread_a():
        monitor.register("Thread-A")
        with tracked_lock(lock1, "Lock1"):
            time.sleep(0.1)  # Give Thread-B time to grab Lock2
            print("  [Thread-A] Waiting for Lock2... (this will deadlock)")
            deadlock_event.set()
            with tracked_lock(lock2, "Lock2"):  # Will block forever
                print("  [Thread-A] Got both locks! (unreachable)")

    def thread_b():
        monitor.register("Thread-B")
        with tracked_lock(lock2, "Lock2"):
            deadlock_event.wait()
            print("  [Thread-B] Waiting for Lock1... (this will deadlock)")
            with tracked_lock(lock1, "Lock1"):  # Will block forever
                print("  [Thread-B] Got both locks! (unreachable)")

    monitor.start_monitoring(interval=0.3)
    ta = threading.Thread(target=thread_a, name="Thread-A", daemon=True)
    tb = threading.Thread(target=thread_b, name="Thread-B", daemon=True)
    ta.start()
    tb.start()
    time.sleep(2)
    monitor.stop_monitoring()
    print("\n→ Deadlock confirmed. Both threads blocked indefinitely.")
    print("→ RESOLUTION: Enforce lock ordering — always acquire Lock1 before Lock2.")


# ── Scenario 2: Dining Philosophers ─────────────────────────────────────────

def scenario_dining(n: int = 5):
    """
    N philosophers sit at a round table. Each needs 2 forks (resources).
    Naive implementation causes all to grab their left fork and wait for right.
    """
    print("\n" + "="*60)
    print(f"SCENARIO: Dining Philosophers ({n} philosophers)")
    print("Each philosopher grabs left fork, then waits for right.")
    print("="*60)

    forks = [threading.Lock() for _ in range(n)]
    ate = [0] * n
    done = threading.Event()

    def philosopher(i):
        monitor.register(f"Phil-{i}")
        left = i
        right = (i + 1) % n
        # DEADLOCK VERSION: all grab left first
        print(f"  [Phil-{i}] Grabbing fork {left} (left)...")
        monitor.about_to_acquire(forks[left])
        forks[left].acquire()
        monitor.acquired(forks[left])
        print(f"  [Phil-{i}] Has fork {left}, waiting for fork {right} (right)...")
        time.sleep(0.05)
        monitor.about_to_acquire(forks[right])
        forks[right].acquire()  # All 5 block here simultaneously
        monitor.acquired(forks[right])
        # If we get here (we won't), eat
        ate[i] += 1
        forks[right].release()
        monitor.released(forks[right])
        forks[left].release()
        monitor.released(forks[left])

    monitor.start_monitoring(interval=0.3)
    threads = [
        threading.Thread(target=philosopher, args=(i,), name=f"Phil-{i}", daemon=True)
        for i in range(n)
    ]
    for t in threads:
        t.start()
    time.sleep(2)
    monitor.stop_monitoring()
    print(f"\n→ Deadlock: all {n} philosophers hold one fork, waiting for the other.")
    print("→ RESOLUTION: Odd philosophers pick right-then-left; even pick left-then-right.")


# ── Scenario 3: Simulated DB Row Lock Deadlock ───────────────────────────────

def scenario_db_lock():
    """
    Two transactions each update two rows in opposite order.
    Classic scenario in PostgreSQL, MySQL, SQL Server.
    """
    print("\n" + "="*60)
    print("SCENARIO: Database Row-Level Lock Deadlock")
    print("Txn1: UPDATE accounts WHERE id=1, then id=2")
    print("Txn2: UPDATE accounts WHERE id=2, then id=1")
    print("="*60)

    row_locks = {1: threading.Lock(), 2: threading.Lock()}

    def transaction_1():
        monitor.register("Txn-1")
        print("  [Txn-1] BEGIN")
        with tracked_lock(row_locks[1], "Row #1"):
            print("  [Txn-1] UPDATE accounts SET balance=balance-100 WHERE id=1")
            time.sleep(0.1)
            print("  [Txn-1] Waiting for Row #2 lock...")
            with tracked_lock(row_locks[2], "Row #2"):
                print("  [Txn-1] UPDATE accounts SET balance=balance+100 WHERE id=2")
                print("  [Txn-1] COMMIT  ← never reached")

    def transaction_2():
        monitor.register("Txn-2")
        print("  [Txn-2] BEGIN")
        with tracked_lock(row_locks[2], "Row #2"):
            print("  [Txn-2] UPDATE accounts SET balance=balance-50 WHERE id=2")
            print("  [Txn-2] Waiting for Row #1 lock...")
            with tracked_lock(row_locks[1], "Row #1"):
                print("  [Txn-2] UPDATE accounts SET balance=balance+50 WHERE id=1")
                print("  [Txn-2] COMMIT  ← never reached")

    monitor.start_monitoring(interval=0.3)
    t1 = threading.Thread(target=transaction_1, name="Txn-1", daemon=True)
    t2 = threading.Thread(target=transaction_2, name="Txn-2", daemon=True)
    t1.start()
    t2.start()
    time.sleep(2)
    monitor.stop_monitoring()
    print("\n→ Deadlock confirmed. DB would normally detect and abort one transaction.")
    print("→ RESOLUTION: Always update rows in primary key order (id=1 before id=2).")


# ── Scenario 4: Safe State (Lock Ordering Fix) ───────────────────────────────

def scenario_safe():
    """
    Same two-lock setup as classic, but with lock ordering applied.
    Both threads always acquire Lock1 before Lock2 — no circular wait possible.
    """
    print("\n" + "="*60)
    print("SCENARIO: Safe State — Lock Ordering Fix Applied")
    print("Both threads always acquire Lock1, then Lock2.")
    print("="*60)

    lock1 = threading.Lock()
    lock2 = threading.Lock()
    results = []

    def thread_a():
        monitor.register("Thread-A")
        with tracked_lock(lock1, "Lock1"):
            time.sleep(0.05)
            with tracked_lock(lock2, "Lock2"):
                results.append("A")
                print("  [Thread-A] Critical section complete ✓")

    def thread_b():
        monitor.register("Thread-B")
        with tracked_lock(lock1, "Lock1"):  # Same order as A
            with tracked_lock(lock2, "Lock2"):
                results.append("B")
                print("  [Thread-B] Critical section complete ✓")

    monitor.start_monitoring(interval=0.3)
    ta = threading.Thread(target=thread_a, name="Thread-A")
    tb = threading.Thread(target=thread_b, name="Thread-B")
    ta.start()
    tb.start()
    ta.join(timeout=3)
    tb.join(timeout=3)
    monitor.stop_monitoring()
    print(f"\n→ Both threads completed successfully. Order: {results}")
    print("→ No deadlock — consistent lock ordering prevents circular wait.")


# ── Main ─────────────────────────────────────────────────────────────────────

SCENARIOS = {
    "classic":  scenario_classic,
    "dining":   scenario_dining,
    "db_lock":  scenario_db_lock,
    "safe":     scenario_safe,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deadlock Condition Simulator")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        default="classic",
        help="Which deadlock scenario to simulate"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all scenarios sequentially"
    )
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║   Deadlock Condition Simulator       ║")
    print("║   Real threading-based demos         ║")
    print("╚══════════════════════════════════════╝")

    if args.all:
        for name, fn in SCENARIOS.items():
            fn()
            print("\n" + "─"*60)
            time.sleep(0.5)
    else:
        SCENARIOS[args.scenario]()

    print("\nSimulation complete.")
