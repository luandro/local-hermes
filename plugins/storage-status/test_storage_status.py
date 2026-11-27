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


print("== classify_intent: read-only (should block) ==")
for t in [
    "How much free space is on the disk?",
    "Quanto espaço livre tem no disco?",
    "check disk space",
    "mostra o uso do disco",
    "what's the filesystem usage on /",
    "espaço em disco",
]:
    check(f"readonly: {t!r}", mod.classify_intent(t) == "storage_readonly")

print("== classify_intent: write/conditional (must fall through) ==")
for t in [
    "check disk and clean it up if needed",   # the partial-intent trap
    "how much space is free, and delete temp files",
    "limpar o disco",
    "free up disk space",
    "check disk then install nginx",
    "quanto espaço livre e depois apaga os temporários",
    "remove old docker images to free space",
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
for t in ["what is the capital of Brazil?", "oi", "write a poem about disk"]:
    check(f"unrelated: {t!r}", mod.classify_intent(t) is None)

print("== scan_storage: read-only, returns a summary ==")
s = mod.scan_storage()
check("scan returns text", bool(s))
check("scan mentions full%", s is not None and "%" in s)
check("scan does not delete (no 'rm'/'delete' in output)", s is None or "rm " not in s.lower())

print("== _on_pre_llm_call: blocks readonly, passes through write ==")
r1 = mod._on_pre_llm_call(user_message="how much free disk space?", is_first_turn=True)
check("blocks readonly", r1.get("action") == "block" and "full" in r1.get("message", "").lower())
r2 = mod._on_pre_llm_call(user_message="check disk and clean it up", is_first_turn=True)
check("falls through on write-intent", r2 == {})
r3 = mod._on_pre_llm_call(user_message="how much free disk space?", is_first_turn=False)
check("falls through on follow-up turn", r3 == {})

print()
if FAILS:
    print(f"FAILED {FAILS} check(s)")
    sys.exit(1)
print("ALL PASS")