"""Write concurrency on workspace memory (Track 2 claim; an open question from planning).

  same_graph  W writers append steps to W investigations inside ONE workspace graph
  many_graphs W writers, each in its own workspace graph
  claims      W agents race for T tasks: every task claimed exactly once
  reads       ntsb read latency (find_open_warnings) while the writers run
Writes eval/report_concurrency.json. Run: python -m eval.concurrency [writers] [steps_each]
"""
import json
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread

from unheeded import memory as M
from unheeded import tools as T
from unheeded.config import connect

OUT = Path(__file__).parent / "report_concurrency.json"


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[max(0, int(p * len(xs)) - 1)], 2)


def writers(graphs, n):
    """One thread per graph handle; each appends n steps. Returns wall seconds and per-write latencies (ms)."""
    def work(g):
        inv = M.start_investigation(g, "bench")
        lat = []
        for i in range(n):
            t = time.perf_counter()
            M.log_step(g, inv, "bench", "tool", ms=0.1)
            lat.append((time.perf_counter() - t) * 1000)
        return lat
    t0 = time.perf_counter()
    with ThreadPoolExecutor(len(graphs)) as pool:
        lat = [x for xs in pool.map(work, graphs) for x in xs]
    return time.perf_counter() - t0, lat


def summary(wall, lat):
    return {"writes": len(lat), "seconds": round(wall, 3), "writes_per_s": round(len(lat) / wall), "p50_ms": pct(lat, .5),
            "p95_ms": pct(lat, .95)}


def main():
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    tag = uuid.uuid4().hex[:6]
    names = [f"bench_{tag}_{i}" for i in range(w)]
    ntsb = connect("agent_ro").select_graph("ntsb")
    report = {"writers": w, "steps_each": n}
    try:
        one = M.workspace(f"bench_{tag}_one")
        report["same_graph"] = summary(*writers([one] * w, n))
        report["many_graphs"] = summary(*writers([M.workspace(x) for x in names], n))

        stop, read_lat = Event(), []
        def reader():
            while not stop.is_set():
                t = time.perf_counter()
                T.find_open_warnings(ntsb, "ANC25MA018")
                read_lat.append((time.perf_counter() - t) * 1000)
        idle = []
        for _ in range(200):
            t = time.perf_counter()
            T.find_open_warnings(ntsb, "ANC25MA018")
            idle.append((time.perf_counter() - t) * 1000)
        th = Thread(target=reader)
        th.start()
        writers([M.workspace(x) for x in names], n)
        stop.set()
        th.join()
        report["reads"] = {"idle_p50_ms": pct(idle, .5), "idle_p95_ms": pct(idle, .95),
                           "under_writes_p50_ms": pct(read_lat, .5), "under_writes_p95_ms": pct(read_lat, .95), "n": len(read_lat)}

        race = M.workspace(f"bench_{tag}_race")
        inv = M.start_investigation(race, "race")
        tasks = 10
        for i in range(tasks):
            M.create_task(race, inv, "k", task_id=f"t{i}")
        with ThreadPoolExecutor(w * 4) as pool:
            got = [x for x in pool.map(lambda i: M.claim_task(race, f"a{i}"), range(w * 4 * 2)) if x]
        ids = [x["id"] for x in got]
        report["claims"] = {"agents": w * 4, "attempts": w * 4 * 2, "tasks": tasks, "claimed": len(ids),
                            "distinct": len(set(ids)), "ok": len(ids) == len(set(ids)) == tasks}
    finally:
        for x in names + [f"bench_{tag}_one", f"bench_{tag}_race"]:
            try:
                M.drop_workspace(x)
            except Exception:  # never created
                pass
    OUT.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    sys.exit(0 if report["claims"]["ok"] else 1)


if __name__ == "__main__":
    main()
