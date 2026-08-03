---
description: Show agentegrity hook status, configuration, and the current session's decision chain
---

Report the agentegrity plugin's status for this session. Run:

```bash
python3 - <<'EOF'
import glob, json, os

print("agentegrity Claude Code plugin status")
try:
    import agentegrity
    print(f"  library: agentegrity {agentegrity.__version__} (importable)")
except ImportError:
    print("  library: NOT importable — the hook is failing open.")
    print("  fix: pip install agentegrity")

mode = os.environ.get("AGENTEGRITY_CC_MODE", "enforce")
disabled = os.environ.get("AGENTEGRITY_CC_DISABLED") == "1"
risk = os.environ.get("AGENTEGRITY_CC_RISK_TIER", "high")
chain_dir = os.environ.get("AGENTEGRITY_CC_CHAIN_DIR") or os.path.expanduser(
    "~/.agentegrity/claude-code"
)
print(f"  mode: {'disabled' if disabled else mode}")
print(f"  risk tier: {risk}")
print(f"  chain dir: {chain_dir}")
for path in sorted(glob.glob(os.path.join(chain_dir, "*.chain.json")))[-5:]:
    with open(path) as fh:
        records = json.load(fh).get("records", [])
    print(f"  {os.path.basename(path)}: {len(records)} decision records")
EOF
```

Then summarize for the user: whether enforcement is active, which mode
is set, and how many decisions the current session has recorded. If the
library is not importable, tell the user the hook is failing open and
how to fix it. Mention that any chain file can be verified with
`agentegrity verify-decisions <file>`.
