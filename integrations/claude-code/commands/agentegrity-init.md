---
description: Set up agentegrity for this Claude Code session — check the library, install it if needed, and verify the wiring
---

Guide the user through getting agentegrity active for this session. The
plugin's hook runs the `python3` on PATH and imports `agentegrity` at
evaluation time, so that interpreter must be the one where the library
is installed. Walk these steps and report what you find.

**Step 1 — identify the interpreter and check the library.**

```bash
echo "python3 -> $(command -v python3)"
python3 - <<'EOF'
import importlib.util, sys
print(f"interpreter: {sys.executable}")
spec = importlib.util.find_spec("agentegrity")
if spec is None:
    print("agentegrity: NOT importable for this python3")
else:
    import agentegrity
    print(f"agentegrity: {agentegrity.__version__} (importable)")
EOF
```

**Step 2 — install if it is missing.** Only if step 1 reported "NOT
importable", ask the user to confirm, then install for the SAME
interpreter (never a different one — a mismatch is why the hook fails
open):

```bash
python3 -m pip install "agentegrity[crypto]"
```

The `[crypto]` extra signs the per-session decision chain. Do not use a
different python, a global install, or `sudo` unless the user asks — if
they work inside a virtualenv, install there and make sure Claude Code
launches with that environment active.

**Step 3 — verify end to end.**

```bash
agentegrity doctor
```

Expect it to print a composite score and exit OK.

**Step 4 — explain the controls.** Tell the user, briefly:

- Enforcement mode is set by the backend/env, default **enforce** (denies
  high-risk calls, prompts for approval on escalations). `AGENTEGRITY_CC_MODE=alert`
  records verdicts without ever blocking — the safe way to trial it.
- Every evaluated tool call appends to a hash-linked decision chain at
  `~/.agentegrity/claude-code/<session>.chain.json`, verifiable afterwards
  with `agentegrity verify-decisions <file>` and renderable with
  `agentegrity report <file> -o report.md`.
- `/agentegrity-status` shows current mode, risk tier, and record counts.

If step 1 already reported the library as importable, skip the install
and just run steps 3–4 to confirm and orient the user.
