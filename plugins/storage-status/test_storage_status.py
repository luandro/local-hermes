#!/usr/bin/env python3
"""Unit tests for the storage-status intent classifier + scan (no Hermes needed).

Run: python3 plugins/storage-status/test_storage_status.py
Validates the partial-intent guard (the plan's biggest risk) and the read-only scan.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util

spec = importlib.util.spec_from_file_location(
    "storage_status", Path(__file__).resolve().parent / "__init__.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

FAILS = 0


def check(name, cond):
    global FAILS
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS += 1


print("== classify_intent: read-only (exact family) ==")
for t, fam in [
    ("How much free space is on the disk?", "disk"),
    ("Quanto espaço livre tem no disco?", "disk"),
    ("check disk space", "disk"),
    ("mostra o uso do disco", "disk"),
    ("what's the filesystem usage on /", "disk"),
    ("espaço em disco", "disk"),
    ("df", "disk"),
    ("disk free", "disk"),
    ("how much memory is free?", "memory"),
    ("how much ram is left?", "memory"),
    ("memory left", "memory"),
    ("uptime", "status"),
    ("docker system df", "docker"),
    ("docker disk usage", "docker"),
    ("quanto de memória livre?", "memory"),
]:
    check(f"readonly[{fam}]: {t!r}", mod.classify_intent(t) == fam)

print("== classify_intent: write/conditional (must fall through) ==")
for t in [
    "check disk and clean it up if needed",   # the partial-intent trap
    "how much space is free, and delete temp files",
    "limpar o disco",
    "free up disk space",
    "check disk then install nginx",
    "quanto espaço livre e depois apaga os temporários",
    "remove old docker images to free space",
    # new traps: memory/status/docker mutations
    "free memory and drop caches",
    "system status and restart the service",
    "docker system df and prune",
    "status do sistema e reiniciar",
    "how much memory and kill the process",
    # codex blocker counterexamples (appended clause + unsupported target + imperative)
    "Check disk space. Email me the numbers.",
    "Quanto espaço livre tem no disco? Esvazie a lixeira.",
    "How much free disk space? When it drops below 10 GB, notify me.",
    "How much free space is on the USB drive?",
    "How much free space is on the drive?",
    "mostre o uso da partição",
    # codex: ambiguous/broad/unfulfillable
    "?",
    "   ",
    "free memory",          # can be an imperative, not a query
    "system status",        # broader than the uptime probe
    "running processes",    # not served by any probe
]:
    check(f"fallthrough: {t!r}", mod.classify_intent(t) is None)

print("== classify_intent: ambiguous / multi-intent (fall through) ==")
for t in [
    "disk space and memory?",
    "how much space or how much ram?",
    "talvez o disco esteja cheio?",
]:
    check(f"ambiguous: {t!r}", mod.classify_intent(t) is None)

print("== classify_intent: unrelated (fall through) ==")
for t in ["what is the capital of Brazil?", "oi", "write a poem about disk",
          "check program usage", "the program usage is high", "memory usage of the app"]:
    check(f"unrelated: {t!r}", mod.classify_intent(t) is None)

print("== scan_storage: read-only, returns a summary ==")
s = mod.scan_storage()
check("scan returns text", bool(s))
check("scan mentions full%", s is not None and "%" in s)
check("scan does not delete (no 'rm'/'delete' in output)", s is None or "rm " not in s.lower())

print("== scan_storage: malformed output -> None (no block) ==")
_orig_run = mod._run
try:
    mod._run = lambda argv, timeout=8.0: "Mem:                "  # bare Mem:, no numbers
    check("memory bare-Mem -> None", mod.scan_storage("memory") is None)
    mod._run = lambda argv, timeout=8.0: "garbage with no load average"
    check("status garbage -> None", mod.scan_storage("status") is None)
    mod._run = lambda argv, timeout=8.0: "only one line"
    check("docker <2 rows -> None", mod.scan_storage("docker") is None)
    mod._run = lambda argv, timeout=8.0: None
    check("disk no output -> None", mod.scan_storage("disk") is None)
    mod._run = lambda argv, timeout=8.0: "Filesystem Size Used Avail Use% Mounted\nthis is not valid output"
    check("disk header+garbage -> None", mod.scan_storage("disk") is None)
    mod._run = lambda argv, timeout=8.0: "Filesystem Size Used Avail Use% Mounted-on\n/dev/sda1 100G 50G 50G 42% /mnt/data"
    check("disk wrong-mount -> None", mod.scan_storage("disk") is None)
    mod._run = lambda argv, timeout=8.0: "Filesystem Size Used Avail Use% Mounted\n/dev/sda1 100G 50G 50G 42% this is not valid 42%"
    check("disk garbage-42% -> None", mod.scan_storage("disk") is None)
    mod._run = lambda argv, timeout=8.0: "Mem: garbage 1"
    check("memory garbage -> None", mod.scan_storage("memory") is None)
    mod._run = lambda argv, timeout=8.0: "Mem: 16Gi garbage garbage garbage"
    check("memory partial-garbage -> None", mod.scan_storage("memory") is None)
    mod._run = lambda argv, timeout=8.0: "Filesystem Size Used Avail Use% Mounted\n/dev/sda1 100G 50G 50G 42% / extra"
    check("disk 7-fields -> None", mod.scan_storage("disk") is None)
    mod._run = lambda argv, timeout=8.0: "Mem: 16/2Gi 4Gi 8Gi 1Gi"
    check("memory 16/2Gi -> None", mod.scan_storage("memory") is None)
    mod._run = lambda argv, timeout=8.0: "Mem: 16Gi garbage garbage garbage"
    check("memory 16garbage -> None", mod.scan_storage("memory") is None)
    mod._run = lambda argv, timeout=8.0: "setup failed: load average unavailable"
    check("status setup-failed -> None", mod.scan_storage("status") is None)
    mod._run = lambda argv, timeout=8.0: "foo\nbar"
    check("docker 2-line garbage -> None", mod.scan_storage("docker") is None)
    mod._run = lambda argv, timeout=8.0: "TYPE SIZE\nerror: daemon unavailable"
    check("docker daemon-error row -> None", mod.scan_storage("docker") is None)
    mod._run = lambda argv, timeout=8.0: "service is up; load average unavailable"
    check("status up-no-numbers -> None", mod.scan_storage("status") is None)
    mod._run = lambda argv, timeout=8.0: "service is up; load average: 0unavailable"
    check("status 0unavailable -> None", mod.scan_storage("status") is None)
    mod._run = lambda argv, timeout=8.0: "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nImages 5 4 garbage 0B"
    check("docker garbage-size -> None", mod.scan_storage("docker") is None)
    # real docker system df must still parse
    mod._run = lambda argv, timeout=8.0: "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nImages 5 4 1.23GB 163.2MB (13%)\nContainers 18 18 500B 0B"
    check("docker real -> not None", mod.scan_storage("docker") is not None)
finally:
    mod._run = _orig_run

print("== _on_pre_llm_call: blocks readonly, passes through write ==")
r1 = mod._on_pre_llm_call(user_message="how much free disk space?", is_first_turn=True)
check("blocks readonly", r1.get("action") == "block" and "full" in r1.get("message", "").lower())
r1b = mod._on_pre_llm_call(user_message="how much memory is free?", is_first_turn=True)
check("blocks memory query", r1b.get("action") == "block")
r2 = mod._on_pre_llm_call(user_message="check disk and clean it up", is_first_turn=True)
check("falls through on write-intent", r2 == {})
r2b = mod._on_pre_llm_call(user_message="docker system df and prune", is_first_turn=True)
check("falls through on docker-prune intent", r2b == {})
r3 = mod._on_pre_llm_call(user_message="how much free disk space?", is_first_turn=False)
check("falls through on follow-up turn", r3 == {})

print()
if FAILS:
    print(f"FAILED {FAILS} check(s)")
    sys.exit(1)
print("ALL PASS")