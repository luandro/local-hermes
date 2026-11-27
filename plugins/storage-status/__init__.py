"""storage-status plugin — deterministic pre-LLM route for read-only disk queries.

Why this exists (W1 in the perf plan): the `disk` agent task takes 25–137 s because
the model has to (a) pick the terminal tool, (b) recall/construct `df`, (c) read the
output, (d) format an answer — a multi-round loop on a memory-bandwidth-bound 1.7B.
A deterministic `pre_llm_call` hook can answer an *unambiguous read-only* storage
question with **zero model calls** (the hook returns `{"action":"block","message":...}`,
which ends the turn before any inference — see agent/turn_context.py
`_collect_pre_llm_call_context` / `PRE_LLM_BLOCK_EXIT_REASON`).

The hard part is NOT the scan — it is the *partial-intent trap* (Codex's biggest-risk
note): "check disk" and "check disk and clean it up if needed" must NOT both get a
fluent read-only summary while the conditional *action* silently vanishes. So the
rules router below blocks ONLY when the request is unambiguously read-only. Anything
with a write/clean/delete/install verb, a conditional, or ambiguity falls through to
normal Hermes (no block, no injected context) — the model keeps full agency.

Design constraints honored:
- Read-only: the scan runs `df` and `du` only. It NEVER deletes, writes, or installs.
- Bounded: fixed command list, capped output, a hard timeout. No shell interpolation
  of user text (argv lists only).
- Fail-open: any error → return {} (no block, no context) → normal Hermes path.
- No capability removal: this does not touch tools, permissions, or the system prompt;
  it only short-circuits one narrow, provably-safe intent class.
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- read-only intent classification (PT-BR + EN) ---------------------------------
# A request is "read-only status" ONLY if the WHOLE request matches a known-safe
# pattern (anchored, not substring) AND carries no disqualifying token. This is a
# whole-request allowlist, not "one substring + a finite denylist" — the difference
# is that "Extend the filesystem" or "How much space? Back up my photos" can no
# longer slip through on a single matching noun. When in doubt → None (fall through).
#
# Each pattern is a regex matched against the full lowercased request (anchored with
# ^...$). A request must match exactly one family to be eligible.

import re as _re

# Whole-request patterns for unambiguous read-only status queries. Strict fullmatch:
# the ENTIRE request must match, and every pattern requires mandatory query content
# (no all-optional arms). Ambiguous/broad/imperative phrasings fall through. A
# storage target is admitted only when the root-filesystem probe covers it (`/`).
_READONLY_PATTERNS = (
    # Each entry is (family, compiled_regex): family embedded -> no parallel-list
    # alignment bug. fullmatch against the whole normalized request. The disk probe
    # answers ONLY for `/` root fs, so a disk query is admitted only when it sets
    # root scope ("/", "root") OR is idiomatic root ("the disk", "disk space", "df").
    # Ambiguous targets ("partition", named disks) fall through.
    ("disk", _re.compile(r"how much (free )?(disk )?(space|storage)( is (there )?)?(on |in )?(the )?(/|root( filesystem)?|disk|filesystem|file system)")),
    ("disk", _re.compile(r"how much (free )?(disk |storage )?(space|storage)( is (there )?)?")),
    ("disk", _re.compile(r"what('s| is) (the )?(free )?(disk |storage |filesystem |file system )?(space|usage|capacity)( on | in )?(the )?(/|root( filesystem)?|disk|filesystem|file system)")),
    ("disk", _re.compile(r"(check|show|what('s| is)|get|tell me)( me)? (the )?(disk|storage|filesystem|file system) (space|usage|capacity)( on | in )?(the )?(/|root( filesystem)?|disk|filesystem|file system)")),
    ("disk", _re.compile(r"is (the )?(/|root( filesystem)?) (full|almost full|running out)")),
    ("disk", _re.compile(r"(check |show |get )?(the )?(disk|storage)( space|usage)")),
    ("disk", _re.compile(r"(df|disk free|disk usage|disk space|storage left|storage remaining|remaining space|space left)")),
    ("docker", _re.compile(r"docker (system df|disk (usage|space))")),
    ("memory", _re.compile(r"how much (free )?(memory|ram)( is (left|free|available))?")),
    ("memory", _re.compile(r"what('s| is) (the )?(free )?(memory|ram)( left| free| available| usage| used)?")),
    ("memory", _re.compile(r"(check|show|what('s| is)|get)( me)? (the )?(memory|ram)( usage| left| free| status)")),
    ("memory", _re.compile(r"(memory left|memory free|ram left|ram free|available memory|available ram|memory info|ram info)")),
    ("status", _re.compile(r"uptime")),
    ("status", _re.compile(r"(system load|load average)")),
    ("disk", _re.compile(r"(quanto|qual) (espaço|espaco)( livre)? (tem|há|esta|disponivel|sobra)( no| do| da)?( disco| disco rígido| hd| armazenamento)?")),
    ("disk", _re.compile(r"(quanto|qual) espaço (livre )?(tem|há|sobra)( no| do| da)?( disco| armazenamento)?")),
    ("disk", _re.compile(r"espaço( livre)? (em|no|do|da)? ?(disco|armazenamento)")),
    ("disk", _re.compile(r"(mostra|mostre|qual|checar|verificar)( o| a)? (uso|espaço|espaco)( do| da)? (disco|armazenamento)")),
    ("disk", _re.compile(r"(o )?(disco|armazenamento) (está|esta) (cheio|lotado|acabando)")),
    ("disk", _re.compile(r"uso (do|da) (disco|armazenamento)")),
    ("memory", _re.compile(r"(quanto|qual)( de)? (memória|memoria|ram)( livre| tem| há| sobra| disponivel)?")),
    ("memory", _re.compile(r"memória livre|memoria livre")),
    ("status", _re.compile(r"(ligado|no ar) há quanto tempo")),
)

# Disqualifiers: if ANY appears anywhere in the request, fall through. This is a
# broad net (verbs, conditionals, multi-intent, scope, named targets we can't serve)
# — deliberately generous, because a false fall-through is cheap and a false block
# is a silent capability loss.
_DISQUALIFY = (
    # action verbs (EN) — any write/mutate
    "clean", "cleanup", "clean up", "delete", "remove", "rm ", "purge", "free up",
    "make space", "clear", "wipe", "install", "uninstall", "move", "mv ", "resize",
    "format", "mkfs", "truncate", "archive", "compress", "deduplicate", "dedupe",
    "prune", "vacuum", "drop cache", "restart", "reboot", "shutdown", "kill",
    "stop", "start", "enable", "disable", "reload", "refresh", "extend", "grow",
    "shrink", "backup", "back up", "copy", "rsync", "sync", "upload", "download",
    "docker prune", "docker rm", "docker rmi", "docker stop", "docker kill",
    "docker run", "docker pull", "docker compose up", "docker compose down",
    # action verbs (PT-BR)
    "limpar", "limpeza", "apagar", "apague", "remover", "remova", "deletar",
    "liberar", "libere", "instalar", "instale", "desinstalar", "mover", "mova",
    "formatar", "compactar", "comprimir", "podar", "reiniciar", "reinicie",
    "desligar", "parar", "matar", "aumentar", "aumente", "estender", "redimir",
    "fazer backup", "backup", "copiar", "sincronizar",
    # conditionals / follow-on
    " if ", "if needed", "if necessary", "caso", "se precisar", "se necessário",
    "se necessario", "e depois", " then ", "depois", "and clean", "e limpar",
    "e remover", "e apagar", "and restart", "e reiniciar", "and backup",
    # multi-intent / disjunction
    " and ", " e ", " also ", " também", " tambem", " or ", " ou ",
    "maybe", "talvez", "perhaps",
    # named targets we cannot serve (we only answer for / or the CWD)
    "/mnt", "/media", "/home/", "/var", "/tmp/", "c:", "d:", "sdb", "sda",
    "nvme1", "second disk", "segundo disco", "outro disco",
)

# Precompile one combined regex for the disqualifier scan (word-ish boundaries where
# it matters; substring otherwise — conservative).
_DISQ_RE = _re.compile("|".join(_re.escape(d) for d in _DISQUALIFY))


def classify_intent(text: str) -> Optional[str]:
    """Return the probe family ('disk'|'memory'|'status'|'docker') iff the WHOLE
    request matches a known-safe read-only status pattern AND contains no
    disqualifying token. Otherwise None (fall through).

    Whole-request allowlist (anchored patterns), not substring admission: a single
    matching noun can no longer admit a request that also carries a write/conditional/
    multi-intent/named-target clause. Conservative: when in doubt → None.
    """
    if not text:
        return None
    # Normalize whitespace so punctuation/newlines can't defeat multi-intent checks.
    norm = _re.sub(r"\s+", " ", text.lower().strip())
    low = " " + norm + " "
    # 1. Broad disqualifier net first (cheap, catches the dangerous cases).
    if _DISQ_RE.search(low):
        return None
    # 2. Whole request must match a known-safe anchored pattern; the family is
    #    inferred from which pattern matched.
    req = text.lower().strip().rstrip("?").strip()
    # 2b. A bare "?" or empty request can never be a real status query.
    if not req:
        return None
    for family, pat in _READONLY_PATTERNS:
        if pat.fullmatch(req):
            return family
    return None


# --- deterministic read-only scan -------------------------------------------------
def _run(argv: list[str], timeout: float = 8.0) -> Optional[str]:
    """Run a fixed argv (no shell, no user interpolation). None on any failure."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if p.returncode == 0:
            return p.stdout
    except Exception as exc:  # fail-open: never raise from a hook
        logger.debug("storage-status: %r failed: %s", argv, exc)
    return None


def scan_storage(family: str = "disk") -> Optional[str]:
    """Return a compact, bounded, human-readable summary for the REQUESTED probe
    family only, or None on failure. Read-only; no writes/deletes/network.

    family: 'disk' | 'memory' | 'status' | 'docker'. Dispatching only the requested
    probe (not all five serially) avoids up to ~28s of subprocess timeouts for a
    simple 'uptime' request, and avoids blocking a turn with irrelevant data when
    the requested probe fails.
    """
    if family == "memory":
        free = _run(["free", "-h"], timeout=4.0)
        if not free:
            return None
        # Real size token: digits, optional .decimal, optional real unit (B/KiB/...).
        size_tok = r"\d+(?:[.,]\d+)?(?:[KMGTPE]?i?B?)?"
        for ln in free.splitlines():
            tok = ln.split(None, 1)[0] if ln.split() else ""
            if tok in ("Mem:", "Mem.:"):
                rest = ln[len(tok):].split()
                if len(rest) >= 4 and all(_re.fullmatch(size_tok, v) for v in rest):
                    return f"Memory: {' '.join(rest)}."
                return None
        return None

    if family == "status":
        up = _run(["uptime"], timeout=4.0)
        if not up:
            return None
        # Require the word "up" AND three numeric load averages (e.g. "load average:
        # 0.15, 0.20, 0.10") — rejects "service is up; load average: 0unavailable".
        if _re.search(r"\bup\b", up.lower()) and _re.search(
                r"load average:\s*\d+(?:[.,]\d+)?\s*,\s*\d+(?:[.,]\d+)?\s*,\s*\d+(?:[.,]\d+)?",
                up.lower()):
            return up.strip()
        return None

    if family == "docker":
        ddf = _run(["docker", "system", "df"], timeout=6.0)
        if not ddf:
            return None
        rows = [l for l in ddf.strip().splitlines() if l.strip()]
        if len(rows) < 2:
            return None
        # Header must look like `docker system df`: TYPE TOTAL ACTIVE SIZE RECLAIMABLE.
        cols0 = rows[0].split()
        if not cols0 or cols0[0] != "TYPE" or "SIZE" not in rows[0].upper():
            return None
        try:
            size_idx = [i for i, c in enumerate(cols0) if c.upper() == "SIZE"][0]
        except IndexError:
            return None
        # Every data row must have a size-like value in the SIZE column and a
        # non-empty TYPE — rejects "error:"/"warning:" rows and garbage.
        size_re = r"\d+(?:[.,]\d+)?(?:[KMGTPE]?i?B?)?"
        for r in rows[1:]:
            cols = r.split()
            if len(cols) <= size_idx or not cols[0] or cols[0].lower().startswith(
                    ("error", "warning")):
                return None
            if not _re.fullmatch(size_re, cols[size_idx]):
                return None
        return "Docker disk usage:\n" + "\n".join("  " + l for l in rows[:6])

    # default: disk
    df = _run(["df", "-h", "/"])
    if not df:
        return None
    lines = df.strip().splitlines()
    if len(lines) < 2:
        return None
    parts = lines[1].split()
    # A real `df -h /` row has EXACTLY 6 fields: fs size used avail use% mount.
    # Require exactly 6 so a trailing "/ extra" can't smuggle a wrong mount past.
    if len(parts) != 6:
        return None
    size, used, avail, use_pct, mount = parts[1], parts[2], parts[3], parts[4], parts[5]
    # Validate every field: sizes are size-like (escaped dot, real units), Use% is
    # NN%, mount is exactly `/`. Codex: a header + garbage or wrong mount must not
    # be reported as root.
    size_re = r"\d+(?:[.,]\d+)?(?:[KMGTPE]?i?B?)?"
    if not (_re.fullmatch(size_re, size) and _re.fullmatch(size_re, used)
            and _re.fullmatch(size_re, avail) and _re.fullmatch(r"\d+%", use_pct)
            and mount == "/"):
        return None
    return (f"Root filesystem `/` is {use_pct} full "
            f"({used} used of {size}, {avail} available).")


# --- hook -------------------------------------------------------------------------
def _on_pre_llm_call(**kwargs: Any) -> dict:
    """pre_llm_call hook: block unambiguous read-only storage requests with a
    deterministic answer (zero model calls); otherwise fall through.

    Contract (agent/turn_context.py): return {"action":"block","message":...} to
    veto the turn before inference, or {"context":...} to inject, or {} to no-op.
    Never raises.
    """
    try:
        msg = kwargs.get("user_message") or ""
        if not isinstance(msg, str):
            return {}
        # Only first-turn, to avoid hijacking follow-ups in an active session.
        if not kwargs.get("is_first_turn", False):
            return {}
        family = classify_intent(msg)
        if family is None:
            return {}
        summary = scan_storage(family)
        if not summary:
            return {}  # requested probe failed → let Hermes handle it normally
        return {"action": "block", "message": summary}
    except Exception as exc:  # a hook must never break the turn
        logger.warning("storage-status pre_llm_call failed: %s", exc)
        return {}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)