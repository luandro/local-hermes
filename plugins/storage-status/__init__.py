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
# A request is "read-only storage" only if it asks to *inspect* space/usage AND
# carries NO write/conditional intent. We are deliberately conservative: when in
# doubt, return None → fall through to Hermes.

# Verbs/nouns that indicate the user wants to SEE storage / memory / status state.
_READ_SIGNALS = (
    # EN — disk/storage
    "disk space", "disk usage", "free space", "free disk", "how much space",
    "how much free", "how much disk", "how much storage", "storage left",
    "storage remaining", "remaining space", "space left", "df ", "disk free",
    "filesystem", "file system", "partition usage", "disk full", "is the disk full",
    "docker system df", "docker disk", "docker usage", "docker space",
    # EN — memory / load / uptime / status (require a quantity/availability word so
    # "program usage" / "memory usage of the app" do NOT false-positive)
    "memory left", "free memory", "ram left", "how much memory", "how much ram",
    "memory free", "ram free", "available memory", "available ram",
    "system status", "system load", "load average", "uptime", "free -h",
    "running processes", "memory info", "ram info",
    # PT-BR
    "espaço", "espaco", "espaço livre", "espaco livre", "disco", "partição",
    "particao", "armazenamento", "quanto espaço", "quanto espaco", "livre no disco",
    "uso do disco", "consumo do disco", "disco cheio", "está cheio", "esta cheio",
    # PT-BR — memória / status
    "memória", "memoria", "memória livre", "memoria livre", "quanto de memória",
    "quanto de memoria", "status do sistema", "carga do sistema", "ligado há",
    "no ar há", "processos",
)

# Any of these => NOT a pure read-only request => fall through (never block).
# This is the partial-intent guard: a conditional or an action verb means the model
# must stay in the loop. NOTE: we do NOT list the bare word "docker"/"container" —
# "docker system df" is read-only and must route here; only docker *mutations* below
# disqualify.
_WRITE_OR_CONDITIONAL = (
    # action verbs (EN)
    "clean", "cleanup", "clean up", "delete", "remove", "rm ", "purge", "free up",
    "make space", "clear", "wipe", "install", "uninstall", "move", "mv ", "resize",
    "format", "mkfs", "truncate", "archive", "compress", "deduplicate", "dedupe",
    "prune", "vacuum", "drop caches", "drop cache", "restart", "reboot", "shutdown",
    "kill", "stop ", "start ", "enable", "disable", "reload", "refresh",
    # docker *mutations* (read-only `docker system df` / `docker ps` stay allowed)
    "docker prune", "docker rm", "docker rmi", "docker stop", "docker kill",
    "docker run", "docker pull", "docker compose up", "docker compose down",
    "container prune", "image prune", "volume prune",
    # action verbs (PT-BR)
    "limpar", "limpeza", "apagar", "apague", "remover", "remova", "deletar",
    "liberar", "libere", "instalar", "instale", "desinstalar", "mover", "mova",
    "formatar", "compactar", "comprimir", "prune", "podar", "reiniciar",
    "reinicie", "desligar", "parar", "matar", "limpar cache",
    # conditionals / scope words that imply follow-on action or ambiguity
    " if ", " if", "if needed", "if necessary", "caso", "se precisar",
    "se necessário", "se necessario", "e depois", "then ", "depois",
    "and clean", "e limpar", "e remover", "e apagar", "and restart", "e reiniciar",
)

# Ambiguity / multi-intent markers => fall through. NOTE: a bare "?" is NOT
# ambiguous — questions are the normal form of a read-only query ("how much
# space is free?"). Only genuine multi-intent / disjunction markers disqualify.
_AMBIGUOUS = (
    " and ", " e ", " also ", " também", " tambem", " or ", "ou ",
    "maybe", "talvez", "perhaps",
)


def classify_intent(text: str) -> Optional[str]:
    """Return 'storage_readonly' iff the request is unambiguously a read-only
    storage inspection. Otherwise None (fall through to Hermes).

    Conservative by design: ANY write/conditional/ambiguous signal → None.
    """
    if not text:
        return None
    low = " " + text.lower().strip() + " "
    # 1. Must express a read signal.
    if not any(sig in low for sig in _READ_SIGNALS):
        return None
    # 2. Must NOT carry any write/conditional intent (partial-intent guard).
    if any(sig in low for sig in _WRITE_OR_CONDITIONAL):
        return None
    # 3. Must NOT be ambiguous / multi-intent.
    if any(sig in low for sig in _AMBIGUOUS):
        return None
    return "storage_readonly"


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


def scan_storage() -> Optional[str]:
    """Return a compact, bounded, human-readable storage+status summary, or None.

    Read-only: df + bounded du of the CWD + free + uptime + docker system df.
    Every sub-command is optional and fail-open (a failure just omits that line).
    No writes, no deletes, no network, no docker engine mutations.
    """
    df = _run(["df", "-h", "/"])
    if not df:
        return None
    lines = df.strip().splitlines()
    if len(lines) < 2:
        return None
    header, row = lines[0], lines[1]
    # row: fs  size  used  avail  use%  mount
    parts = row.split()
    if len(parts) < 5:
        return None
    use_pct = parts[4]
    size, used, avail = parts[1], parts[2], parts[3]
    summary = (f"Root filesystem `/` is {use_pct} full "
               f"({used} used of {size}, {avail} available).")

    # Bounded du of the working dir (top-level only, capped) — helps "what's big" but
    # stays read-only and fast. Skipped silently if it fails.
    du = _run(["du", "-h", "-d", "1", "--max-depth=1", "."], timeout=6.0)
    if du:
        top = [l for l in du.strip().splitlines() if l and not l.endswith("\t.")]
        top = top[:8]
        if top:
            summary += " Largest items in the current directory:\n" + "\n".join(
                "  " + l for l in top
            )

    # Memory (free -h): read-only. The "Mem:"/"Memória:" line is the useful one.
    # Locale-robust: match any line whose first token starts with "Mem".
    free = _run(["free", "-h"], timeout=4.0)
    if free:
        for ln in free.splitlines():
            tok = ln.split(None, 1)[0] if ln.split() else ""
            if tok.startswith("Mem"):
                summary += f" Memory: {ln[len(tok):].strip()}."
                break

    # Uptime/load (uptime): read-only.
    up = _run(["uptime"], timeout=4.0)
    if up:
        summary += f" {up.strip()}"

    # Docker disk usage (docker system df): read-only, no engine mutation. Only if
    # docker is reachable; silently skipped otherwise (the common no-docker case).
    ddf = _run(["docker", "system", "df"], timeout=6.0)
    if ddf:
        rows = [l for l in ddf.strip().splitlines() if l.strip()]
        if len(rows) >= 2:
            summary += " Docker disk usage:\n" + "\n".join("  " + l for l in rows[:6])
    return summary


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
        if classify_intent(msg) != "storage_readonly":
            return {}
        summary = scan_storage()
        if not summary:
            return {}  # scan failed → let Hermes handle it normally
        return {"action": "block", "message": summary}
    except Exception as exc:  # a hook must never break the turn
        logger.warning("storage-status pre_llm_call failed: %s", exc)
        return {}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)