"""
Automated System Process Deadlock Tool
--------------------------------------
This tool automatically detects potential deadlocks in system processes.
It analyzes process dependencies and resource allocations to identify
circular wait conditions and suggests resolution strategies.
"""
import threading
import time
import os
import sys
from collections import defaultdict

# Add current directory to path if needed to finds deadlock_engine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deadlock_engine import DeadlockEngine, SystemState


class SystemResource:
    """Represents an OS-level shared resource (e.g., File handle, Mutex, DB Lock)."""
    def __init__(self, rid: str, name: str):
        self.rid = rid
        self.name = name
        self.lock = threading.Lock()


class AutomatedDeadlockDetector:
    """
    Acts as the OS Lock Manager and the Automated Daemon Monitor.
    It tracks real-time resource acquisitions and runs the detection engine.
    """
    def __init__(self):
        self.engine = DeadlockEngine()
        self.resources = {}
        self.processes = {}
        
        # State tracking matrices
        self.holds = defaultdict(list)       # pid -> [rid, ...]
        self.waiting_for = defaultdict(list) # pid -> [rid, ...]
        
        self.state_lock = threading.Lock()
        self.running = True

    def register_resource(self, rid: str, name: str):
        self.resources[rid] = SystemResource(rid, name)

    def register_process(self, pid: str, name: str, priority: int = 1):
        with self.state_lock:
            self.processes[pid] = {"pid": pid, "name": name, "priority": priority}

    def request_resource(self, pid: str, rid: str):
        res = self.resources[rid]
        
        # Mark as waiting (Process dependency)
        with self.state_lock:
            self.waiting_for[pid].append(rid)
        
        # OS-level blocking call to acquire resource
        res.lock.acquire()
        
        # Mark as held (Resource allocation)
        with self.state_lock:
            if rid in self.waiting_for[pid]:
                self.waiting_for[pid].remove(rid)
            self.holds[pid].append(rid)

    def release_resource(self, pid: str, rid: str):
        res = self.resources[rid]
        
        with self.state_lock:
            if rid in self.holds[pid]:
                self.holds[pid].remove(rid)
                res.lock.release()

    def get_system_state(self) -> SystemState:
        """Translates the current thread lock states into a SystemState graph."""
        with self.state_lock:
            procs = []
            for pid, pinfo in self.processes.items():
                p = dict(pinfo)
                p["holds"] = list(self.holds.get(pid, []))
                p["waiting_for"] = list(self.waiting_for.get(pid, []))
                procs.append(p)
            
            res_list = [
                {"rid": r.rid, "name": r.name, "instances": 1, "available": 0 if r.lock.locked() else 1} 
                for r in self.resources.values()
            ]
                   
            return SystemState(processes=procs, resources=res_list)

    def monitor_loop(self):
        """
        Background daemon that automatically detects deadlocks.
        """
        print("\n[DAEMON] Automated Deadlock Monitor started in background.")
        print("[DAEMON] Scanning process dependencies and resource allocation...")
        
        while self.running:
            time.sleep(1.0) # Check every second
            state = self.get_system_state()
            
            # Use DeadlockEngine to automatically identify circular wait
            cycles = self.engine.detect_cycles(state)
            
            if cycles:
                print("\n" + "!"*70)
                print("!!!  POTENTIAL DEADLOCK DETECTED IN SYSTEM PROCESSES  !!!")
                print("!"*70)
                
                print("\n[ANALYSIS] Circular wait condition identified:")
                for i, cycle in enumerate(cycles, 1):
                    # cycle is [P1, R1, P2, R2, P1]
                    print(f"  Cycle {i}: {' -> '.join(cycle)}")
                
                print("\n[RESOLUTION] Suggested Resolution Strategies:")
                resolution = self.engine.generate_resolution(state, cycles)
                
                for strat in resolution.get("strategies", []):
                    risk = strat['risk'].upper()
                    print(f"\n  * {strat['name']} (Risk: {risk})")
                    # Replace unicode em-dash with standard dash just in case
                    action = strat['recommended_action'].replace('—', '-')
                    print(f"    Action: {action}")
                
                print("\n" + "="*70)
                sys.stdout.flush()
                self.running = False
                
                # Forcefully terminate the simulation to prevent infinite hang
                os._exit(1)


# ── Simulation Logic ─────────────────────────────────────────────────────────

def simulate_system_processes(detector: AutomatedDeadlockDetector):
    # Setup System Resources
    detector.register_resource("DB_LOCK", "Database Master Lock")
    detector.register_resource("FILE_IO", "Log File System Mutex")
    detector.register_resource("NET_PORT", "Network Socket 8080")

    # Define system processes that request resources in a circular manner
    def system_process(pid: str, name: str, priority: int, res1: str, res2: str, delay: float):
        detector.register_process(pid, name, priority)
        print(f"[{pid}] {name} spawned (Priority {priority}).")
        
        # Step 1: Resource allocation
        print(f"[{pid}] Requesting resource allocation: {res1}")
        detector.request_resource(pid, res1)
        print(f"[{pid}] Acquired {res1}. Processing...")
        
        time.sleep(delay) # Simulate work, allowing other processes to spawn
        
        # Step 2: Process dependency (this creates the circular wait)
        print(f"[{pid}] Dependency required: Now waiting for {res2}...")
        detector.request_resource(pid, res2) # Will block indefinitely
        
        print(f"[{pid}] Acquired {res2}. Finished task.")
        detector.release_resource(pid, res2)
        detector.release_resource(pid, res1)

    # Spawn real threads simulating OS system processes
    threads = [
        threading.Thread(target=system_process, args=("PROC_DB", "DB Writer Daemon", 3, "DB_LOCK", "FILE_IO", 0.8)),
        threading.Thread(target=system_process, args=("PROC_LOG", "Log Aggregator", 1, "FILE_IO", "NET_PORT", 0.8)),
        threading.Thread(target=system_process, args=("PROC_NET", "Network Sync", 2, "NET_PORT", "DB_LOCK", 0.8)),
    ]

    for t in threads:
        t.start()
    
    for t in threads:
        t.join()


if __name__ == "__main__":
    print("="*70)
    print(" AUTOMATED SYSTEM PROCESS DEADLOCK DETECTOR ".center(70, " "))
    print("="*70)
    
    detector = AutomatedDeadlockDetector()
    
    # 1. Start the automated background monitor daemon
    monitor_thread = threading.Thread(target=detector.monitor_loop, daemon=True)
    monitor_thread.start()
    
    # 2. Run the simulated system processes
    simulate_system_processes(detector)
