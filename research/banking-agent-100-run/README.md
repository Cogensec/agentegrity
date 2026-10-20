# Synthetic banking-agent integrity evaluation

This directory contains the v0.2 research manuscript, executable harness, selected aggregate artifacts, and reproduction instructions. The full raw evidence package, including all 20-step traces, remains in the task deliverable and is not part of this public repository proposal. Reviewers can reproduce the runs with `requirements-recreate.txt` and the commands in the manuscript.

The primary experiment paired 100 deliberately vulnerable baseline runs with 100 runs of actual Agentegrity v0.12.0 at commit `56cfcc48d8edeb4beee1bec3948c95d20804e4f4`, using a scenario-coded synthetic approval callback. An exploratory 100-run approve-all ablation tested that callback's effect. See the [manuscript](RESEARCH_PAPER_DRAFT_v0.2.md) and [methodology addendum](METHODOLOGY_v0.2_ADDENDUM.md) before interpreting the summary counts.

**Critical attribution:** The primary arm blocked 80/80 scripted unsafe actions because every precheck escalated and the synthetic callback denied attack-labeled scenarios. With an approve-all callback, 80/80 proceeded. The adversarial layer flagged 30/80 attack-labeled runs in each configuration. This is a deterministic integration fixture, not a live bank, LLM-agent, or real human-review study. Ed25519 verification of saved records does not establish decision correctness or independent publication provenance.
