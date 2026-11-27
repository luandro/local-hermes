#!/usr/bin/env python3
"""Memory-pressure tracer for the local-hermes eval (E00 instrumentation).

Samples host + process memory/IO pressure around a measured window so that
eval runs can distinguish *real* work from runs that simply got luckier with
paging. All counters are best-effort: a missing file yields None, never 0
per the plan's "null = unmeasured, not zero" rule.

Usage (as a context manager):

    from memtrace import MemTracer
    with MemTracer(llama_pid) as mt:
        ... run one eval case ...
    snap = mt.stop()
    # snap -> {"mem": {...}, "psi": {...}, "swap": {...}, "faults": {...}, ...}

Or standalone:  python3 memtrace.py --pid <llama-server pid> --seconds 10
"""
import argparse
import os
import time
from pathlib import Path

PRESSURE = Path("/proc/pressure")
VMSTAT = Path("/proc/vmstat")
MEMINFO = Path("/proc/meminfo")


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def _psi(path):
    """Parse a /proc/pressure/<res> file -> {some_total_us, full_total_us} or None."""
    txt = _read(path)
    if not txt:
        return None
    out = {}
    for line in txt.splitlines():
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]  # some / full
        for tok in parts[1:]:
            if tok.startswith("total="):
                try:
                    out[f"{kind}_total_us"] = int(tok.split("=", 1)[1])
                except ValueError:
                    pass
    return out or None


def _vmstat():
    """Return dict of the swap counters we care about, or None."""
    txt = _read(VMSTAT)
    if not txt:
        return None
    want = ("pswpin", "pswpout", "pgmajfault", "pgfault")
    out = {}
    for line in txt.splitlines():
        k, _, v = line.partition(" ")
        if k in want:
            try:
                out[k] = int(v)
            except ValueError:
                pass
    return out or None


def _memavailable():
    txt = _read(MEMINFO)
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1])  # kB
            except (ValueError, IndexError):
                return None
    return None


def _proc_rss(pid):
    """Resident + major faults for a single pid from /proc/<pid>/status + stat."""
    if not pid:
        return None
    status = _read(f"/proc/{pid}/status")
    if not status:
        return None
    rss = None
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            try:
                rss = int(line.split()[1])  # kB
            except (ValueError, IndexError):
                pass
            break
    return {"rss_kb": rss}


def _proc_majflt(pid):
    """Major faults for a pid from /proc/<pid>/stat field 12 (1-indexed)."""
    if not pid:
        return None
    txt = _read(f"/proc/{pid}/stat")
    if not txt:
        return None
    try:
        # comm (field 2) may contain spaces/parens; split after the last ')'.
        rest = txt.rsplit(")", 1)[1].split()
        # rest[0] is state (field 3); field 12 (majflt) is rest[12-3] = rest[9]
        return int(rest[9])
    except (ValueError, IndexError):
        return None


def snapshot(pid=None):
    """One point-in-time sample of all counters."""
    return {
        "mem_available_kb": _memavailable(),
        "psi_memory": _psi(PRESSURE / "memory"),
        "psi_io": _psi(PRESSURE / "io"),
        "vmstat": _vmstat(),
        "proc_rss": _proc_rss(pid),
        "proc_majflt": _proc_majflt(pid),
        "ts": time.monotonic(),
    }


def _delta(a, b):
    """b - a for numeric leaves; None if either side missing."""
    if a is None or b is None:
        return None
    return b - a


def diff(before, after):
    """Compute deltas between two snapshots (after - before)."""
    out = {}
    # mem available: report the min (lowest free) seen and the end value
    out["mem_available_min_kb"] = min(
        x for x in (before.get("mem_available_kb"), after.get("mem_available_kb")) if x is not None
    ) if (before.get("mem_available_kb") is not None or after.get("mem_available_kb") is not None) else None
    out["mem_available_end_kb"] = after.get("mem_available_kb")
    # PSI totals (cumulative microseconds stalled) — nested dicts
    for key in ("psi_memory", "psi_io"):
        b, a = before.get(key) or {}, after.get(key) or {}
        for sub in ("some_total_us", "full_total_us"):
            out[f"{key}_{sub}_delta"] = _delta(b.get(sub), a.get(sub))
    # vmstat swap/fault deltas
    bv, av = before.get("vmstat") or {}, after.get("vmstat") or {}
    for k in ("pswpin", "pswpout", "pgmajfault", "pgfault"):
        out[f"{k}_delta"] = _delta(bv.get(k), av.get(k))
    # per-process
    out["proc_rss_end_kb"] = (after.get("proc_rss") or {}).get("rss_kb")
    out["proc_majflt_delta"] = _delta(before.get("proc_majflt"), after.get("proc_majflt"))
    return out


class MemTracer:
    """Context manager that snapshots memory pressure before/after a window."""

    def __init__(self, pid=None):
        self.pid = pid
        self.before = None
        self.after = None

    def __enter__(self):
        self.before = snapshot(self.pid)
        return self

    def __exit__(self, *exc):
        self.after = snapshot(self.pid)
        return False

    def stop(self):
        if self.after is None:
            self.after = snapshot(self.pid)
        return diff(self.before, self.after)


def _find_llama_pid():
    """Best-effort: first llama-server pid, or None."""
    import subprocess
    try:
        out = subprocess.run(["pgrep", "-f", "llama-server"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
    except Exception:
        pass
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()
    pid = args.pid or _find_llama_pid()
    import json
    with MemTracer(pid) as mt:
        time.sleep(args.seconds)
    print(json.dumps(mt.stop(), indent=2))