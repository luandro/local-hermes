#!/usr/bin/env python3
"""R1.5 shadow evaluation: a small ML classifier as a VETO on W1-approved requests.

Architecture (codex plan, 2026-11-27):
- W1 (plugins/storage-status `classify_intent`) stays the AUTHORITY for probe admission
  and family selection. The classifier is a *veto*: it can only turn a W1 "block" into a
  fall-through (when P(safe) is below the tuned cutoff), never add a new interception.
- This is the safe direction: a false veto = we merely pay for one normal LLM turn (cheap);
  a false block = silent capability loss (dangerous). So the classifier is tuned for >=98%
  recall on the "must fall through" (unsafe) class and only vetoes below a high cutoff.

This module is stdlib-only and calls NO probes (offline shadow). The trained artifact
(calibrated TF-IDF+LogReg) is loaded lazily by `load_model()`; until it exists, the veto
is a no-op (everything W1 approves is kept), so the module is importable and testable
before training.

Data format: eval/r15_cases.jsonl, one JSON object per line:
    {"id","family_id","split","lang","text","expected_route","source","reason"}
  expected_route is null (must fall through / unsafe) or one of disk|memory|status|docker.
  split in {train,calibration,tune,test}. lang in {en,pt}.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "r15_cases.jsonl"
MODEL_PATH = HERE / "r15_model.json"  # artifact written by r15_train.py

VALID_ROUTES = ("disk", "memory", "status", "docker")
VALID_SPLITS = ("train", "calibration", "tune", "test")
VALID_LANGS = ("en", "pt")


# --- data loading + validation ----------------------------------------------------
def load_cases(path: Path = CASES_PATH) -> list[dict]:
    """Load and validate the labelled cases. Raises ValueError on any malformed row,
    cross-split group leak, normalized duplicate, or conflicting label."""
    cases = []
    seen_norm: dict[str, str] = {}  # normalized text -> id (duplicate guard)
    group_split: dict[str, str] = {}  # group id -> split (leakage guard)
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{ln}: bad JSON: {e}") from e
            _validate_case(c, ln)
            # normalized duplicate guard
            norm = _normalize(c["text"])
            if norm in seen_norm and seen_norm[norm] != c["id"]:
                raise ValueError(f"{path}:{ln}: duplicate of {seen_norm[norm]}: {norm!r}")
            seen_norm[norm] = c["id"]
            # group leakage guard: a group must live in exactly one split
            g = c.get("group", c["id"])
            if g in group_split and group_split[g] != c["split"]:
                raise ValueError(
                    f"{path}:{ln}: group {g!r} spans splits "
                    f"{group_split[g]!r} and {c['split']!r}")
            group_split[g] = c["split"]
            cases.append(c)
    return cases


def _validate_case(c: dict, ln: int) -> None:
    for k in ("id", "split", "lang", "text"):
        if k not in c:
            raise ValueError(f"line {ln}: missing {k!r}")
    if c["split"] not in VALID_SPLITS:
        raise ValueError(f"line {ln}: bad split {c['split']!r}")
    if c["lang"] not in VALID_LANGS:
        raise ValueError(f"line {ln}: bad lang {c['lang']!r}")
    if not str(c["text"]).strip():
        raise ValueError(f"line {ln}: empty text")
    er = c.get("expected_route")
    if er is not None and er not in VALID_ROUTES:
        raise ValueError(f"line {ln}: bad expected_route {er!r}")


def _normalize(text: str) -> str:
    return " ".join(str(text).lower().split())


# --- the veto policy (pure, score-injected; no model needed) ----------------------
def veto(w1_family: Optional[str], p_safe: Optional[float], cutoff: float) -> Optional[str]:
    """Return the route to actually use, given W1's decision and the classifier's
    P(safe). Pure and deterministic — the core safety rule.

    - W1 returned None (abstain) -> fall through (None). The classifier never ADDS
      an interception (safe direction).
    - p_safe unavailable / non-finite -> fall through (fail-open).
    - p_safe < cutoff -> VETO W1's block (fall through).
    - otherwise -> keep W1's family.

    cutoff is inclusive: p_safe == cutoff keeps W1 (block), matching the plan.
    """
    if w1_family is None:
        return None
    if p_safe is None or not math.isfinite(p_safe):
        return None
    if p_safe < cutoff:
        return None
    return w1_family


# --- scoring against a labelled set ----------------------------------------------
def score_rows(rows: list[dict], predict: Any, cutoff: float) -> dict:
    """Score a candidate (veto) against gold rows using an injected `predict` fn
    (text -> p_safe). Returns counts for the acceptance gate. `predict` is injected so
    this is testable without a trained model.

    A row is an "interception" if the final route is not None. It is "unsafe
    interception" if the final route is not None but gold expected_route is None.
    """
    n = len(rows)
    unsafe_intercept = 0
    correct = 0
    w1_correct = 0
    safe_total = 0
    safe_correct = 0
    per_lang: dict[str, dict] = {}
    for r in rows:
        gold = r.get("expected_route")
        text = r["text"]
        w1 = _w1_family(text)
        p = predict(text)
        final = veto(w1, p, cutoff)
        # candidate correctness
        if final == gold:
            correct += 1
        # W1 baseline correctness
        if w1 == gold:
            w1_correct += 1
        # unsafe interception (final blocks a gold-fall-through)
        if final is not None and gold is None:
            unsafe_intercept += 1
        # safe coverage
        if gold is not None:
            safe_total += 1
            if final == gold:
                safe_correct += 1
        lang = r["lang"]
        pl = per_lang.setdefault(lang, {"n": 0, "correct": 0, "w1_correct": 0,
                                        "unsafe_intercept": 0, "safe_total": 0,
                                        "safe_correct": 0})
        pl["n"] += 1
        pl["correct"] += final == gold
        pl["w1_correct"] += w1 == gold
        pl["unsafe_intercept"] += final is not None and gold is None
        if gold is not None:
            pl["safe_total"] += 1
            pl["safe_correct"] += final == gold
    return {
        "n": n, "correct": correct, "w1_correct": w1_correct,
        "accuracy": correct / n if n else None,
        "w1_accuracy": w1_correct / n if n else None,
        "unsafe_intercept": unsafe_intercept,
        "safe_total": safe_total, "safe_correct": safe_correct,
        "safe_coverage": safe_correct / safe_total if safe_total else None,
        "per_lang": per_lang,
    }


def _w1_family(text: str) -> Optional[str]:
    """Import W1's classifier lazily (plugins/ is not a package)."""
    import importlib.util
    p = HERE.parent / "plugins" / "storage-status" / "__init__.py"
    spec = importlib.util.spec_from_file_location("storage_status", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.classify_intent(text)


# --- model artifact (lazy; None until r15_train.py writes it) ---------------------
_MODEL_CACHE: dict = {}


def load_model(path: Path = MODEL_PATH) -> Optional[dict]:
    """Load the trained artifact if present, else None (veto becomes a no-op)."""
    if "m" in _MODEL_CACHE:
        return _MODEL_CACHE["m"]
    if not path.exists():
        _MODEL_CACHE["m"] = None
        return None
    _MODEL_CACHE["m"] = json.loads(path.read_text())
    return _MODEL_CACHE["m"]


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(CASES_PATH))
    ap.add_argument("--cutoff", type=float, default=0.99)
    args = ap.parse_args()
    rows = load_cases(Path(args.cases))
    model = load_model()
    if model is None:
        print("no trained model yet (eval/r15_model.json); veto is a no-op — "
              "run eval/r15_train.py first. W1 baseline only.")
        # W1-only baseline
        base = score_rows(rows, lambda t: 1.0, args.cutoff)  # p_safe=1 -> never veto
        print(json.dumps(base, indent=2))
        return 0
    raise SystemExit("trained-model scoring not wired yet (post-train step)")


if __name__ == "__main__":
    raise SystemExit(main())