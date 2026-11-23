# Methodology addendum: actual Agentegrity execution

**20 October 2026.** Read with the frozen `METHODOLOGY.md` v0.1 protocol. This addendum records execution and deviations; it does not retrospectively change the primary endpoints.

## Source and implementation

The actual protected arm was run from [Cogensec/agentegrity](https://github.com/Cogensec/agentegrity), commit `56cfcc48d8edeb4beee1bec3948c95d20804e4f4`, version `0.12.0`. The v0.1 protocol named older commit `fb9ea5b621321d3b2033287f8b263524a0f438d9`. We updated the harness's `UPSTREAM_SHA` to the commit actually cloned **before** the protected runs. The behavioral change between these commits was not separately studied. The Python package and crypto support were installed in an isolated environment. The harness imported the installed source; no mocked detector was used.

The 100 baseline and 100 protected trials were run in one `both` invocation with seed 4815. The original baseline and reference-checkpoint outputs remain in the package as historical fixture work; the new paired baseline is the correct timing comparator for the new protected arm. A separate approve-all callback run was added after seeing that every protected precheck escalated. It is explicitly exploratory and must not be mixed with the primary paired estimate.

## What the integration actually observed

The monitor received a synthetic prompt, one tool-output content field, one memory content field, the proposed action, and a short reasoning chain. It did **not** receive a trusted identity-verification result, independent beneficiary registry, signed customer authorization, or immutable transaction intent as domain-policy inputs. Consequently, a missed S04, S06, or S09 condition is evidence about this fixture's integration, not a universal incapacity of the Agentegrity source. The callback's S00/S01 allow decision uses the known scenario label and is an idealized oracle.

All primary precheck composite actions were `escalate`. In the real monitor implementation, `block` halts unconditionally; `escalate` consults the configured approval handler and halts if it denies. This configuration's 80 attack-run stops therefore depended on denial by the scenario-coded callback. The approve-all ablation demonstrates the same path by permitting all 80 attack-run effects. Adversarial-layer flags and guard blocks remain distinct measurements.

## Evidence and recovery checks

The audit script reloaded each saved chain with `AttestationChain.from_dict_list()` and called `verify_chain()`, `verify_signatures({trace_public_key})`, and `verify_decision_links()`. It compared trace and row data to the manifest hashes and challenged first and last record content, predecessor linkage, signature presence, and trusted-key substitution in disposable copies. All 100 primary and 100 ablation traces passed. The key is stored in the trace, so this is not a separately anchored publication signature. The source's hash-link check alone does not authenticate modified record contents; Ed25519 verification is the relevant content-integrity test.

S09 called `RecoveryLayer.restore_to()` using the checkpoint. The primary arm did not post a wrong transfer, so equality with the initial business ledger is prevention rather than restoration. The ablation posted a wrong transfer; the Agentegrity checkpoint returned but did not revert the in-memory ledger. These outcomes must be reported separately.

## Next experimental gate

Before making a general efficacy claim, use independently varied attack and benign content, a real LLM agent, a blinded reviewer or external approval service, explicit trusted business-policy signals, a factorial approval design, externally pinned signer keys, and a compensating transaction/recovery mechanism. The current benchmark remains useful as an integration and evidence-boundary study.
