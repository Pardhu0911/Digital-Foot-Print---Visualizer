"""Run sequential, full-dataset WhatsMyName benchmarks with a synthetic handle."""

import argparse
from contextlib import ExitStack
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import statistics
import sys
from threading import local, Lock
import time
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import whatsmyname


def load_scanner(path):
    if path is None:
        return whatsmyname
    spec = spec_from_file_location("baseline_whatsmyname", path)
    scanner = module_from_spec(spec)
    spec.loader.exec_module(scanner)
    return scanner


def run(scanner, dataset, handle, workers):
    """Time request work including body reads, and other per-site work separately."""
    active = local()
    lock = Lock()
    site_times = []
    request = requests.Session.request
    content = requests.Response.content.fget
    check = scanner.check_site

    def network_call(operation, *args, **kwargs):
        if not getattr(active, "checking", False) or active.in_request:
            return operation(*args, **kwargs)
        started = time.perf_counter()
        active.in_request = True
        try:
            return operation(*args, **kwargs)
        finally:
            active.request_seconds += time.perf_counter() - started
            active.in_request = False

    def timed_check(*args, **kwargs):
        active.checking = True
        active.in_request = False
        active.request_seconds = 0.0
        started = time.perf_counter()
        try:
            return check(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            network = active.request_seconds
            active.checking = False
            with lock:
                site_times.append((network, elapsed - network))

    with ExitStack() as patches:
        patches.enter_context(patch.object(scanner, "load_dataset", return_value=dataset))
        patches.enter_context(patch.object(scanner, "check_site", timed_check))
        patches.enter_context(patch.object(requests.Session, "request", lambda *a, **kw: network_call(request, *a, **kw)))
        patches.enter_context(patch.object(requests.Response, "content", property(lambda r: network_call(content, r))))
        started = time.perf_counter()
        cpu_started = time.process_time()
        first_result = None
        for final in scanner.scan_username_stream(handle, workers=workers):
            if first_result is None and final["sites_checked"]:
                first_result = time.perf_counter() - started
        elapsed = time.perf_counter() - started
        cpu = time.process_time() - cpu_started

    request_times, other_times = zip(*site_times)
    assert final["sites_checked"] == final["sites_total"]
    assert final["found_count"] + final["not_found_count"] + final["uncertain_count"] == final["sites_checked"]
    return {
        "workers": workers,
        "wall_seconds": round(elapsed, 3),
        "process_cpu_seconds": round(cpu, 3),
        "first_result_seconds": round(first_result, 3),
        "request_worker_seconds": round(sum(request_times), 3),
        "other_worker_seconds": round(sum(other_times), 3),
        **{key: final[key] for key in ("sites_checked", "found_count", "not_found_count", "uncertain_count", "status_counts")},
        "site_outcomes": [
            {key: row[key] for key in ("site", "status", "http_status", "elapsed_ms") if key in row}
            for row in final["results"]
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanner", type=Path, help="Path to a preserved baseline scanner module")
    parser.add_argument("--dataset", type=Path, required=True, help="Read this dataset snapshot, or download it if missing")
    parser.add_argument("--output", type=Path, required=True, help="Write full results as newline-delimited JSON")
    parser.add_argument("--handle", default="dfvbench9a7f31e4", help="Synthetic handle to use across all runs")
    parser.add_argument("--workers", nargs="+", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    if args.repeats < 1 or any(workers < 1 for workers in args.workers):
        parser.error("--workers and --repeats must be positive")
    started = time.perf_counter()
    if args.dataset.exists():
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    else:
        dataset = whatsmyname.load_dataset()
        args.dataset.write_text(json.dumps(dataset), encoding="utf-8")
    if not any(site.get("valid", True) for site in dataset.get("sites", [])):
        parser.error("The dataset must contain at least one active site")
    print(f"Dataset ready in {time.perf_counter() - started:.3f}s", flush=True)
    scanner = load_scanner(args.scanner)
    with args.output.open("a", encoding="utf-8") as output:
        for workers in args.workers:
            times = []
            for _ in range(args.repeats):
                result = run(scanner, dataset, args.handle, workers)
                times.append(result["wall_seconds"])
                output.write(json.dumps(result) + "\n")
                output.flush()
                print(json.dumps({key: value for key, value in result.items() if key != "site_outcomes"}), flush=True)
            print(f"{workers} workers, min {min(times):.3f}s, max {max(times):.3f}s, mean {statistics.mean(times):.3f}s", flush=True)


if __name__ == "__main__":
    main()
