"""
Custom Validator Example
========================

Demonstrates how to extend the Agentegrity Framework with
custom threat detectors, policy rules, and the high-level
AgentegrityClient.
"""

from agentegrity.layers.adversarial import ThreatAssessment
from agentegrity.layers.governance import PolicyDecision, PolicyRule
from agentegrity.sdk.client import AgentegrityClient

# --- Custom threat detector ---

def detect_data_exfiltration(profile, context):
    """
    Custom threat detector that flags potential data exfiltration
    patterns in tool outputs.
    """
    threats = []
    tool_outputs = context.get("tool_outputs", [])

    for output in tool_outputs:
        if isinstance(output, dict):
            # Check for base64-encoded data in responses (exfil indicator)
            content = str(output.get("content", ""))
            if len(content) > 10000 and "==" in content[-10:]:
                threats.append(ThreatAssessment(
                    channel="tool_responses",
                    threat_type="data_exfiltration",
                    severity=0.80,
                    confidence=0.65,
                    description=(
                        "Large base64-encoded payload in tool output "
                        "— potential exfiltration"
                    ),
                    indicators=["large_payload", "base64_encoding"],
                ))

    return threats


# --- Custom policy rule ---

pii_access_rule = PolicyRule(
    rule_id="CUSTOM-PII-001",
    name="PII Access Requires Clearance",
    description="Agents accessing PII-classified data must have pii_clearance capability",
    condition=lambda profile, action, ctx: (
        action.get("data_classification") == "pii"
        and not profile.has_capability("pii_clearance")
    ),
    decision=PolicyDecision.DENY,
    severity=0.90,
)


def main():
    # 1. Set up the client (high-level interface)
    client = AgentegrityClient(
        policy_set="enterprise-default",
        coherence_threshold=0.80,
        drift_tolerance=0.10,
    )

    # Register custom threat detector
    client.adversarial_layer._custom_detectors.append(detect_data_exfiltration)

    # Register custom policy rule
    client.governance_layer.add_rule(pii_access_rule)

    # 2. Create a profile
    profile = client.create_profile(
        name="customer-service-bot",
        agent_type="tool_using",
        capabilities=["tool_use", "memory_access"],  # Note: no pii_clearance
        risk_tier="medium",
    )

    print(f"Agent: {profile}")
    print()

    # 3. Clean evaluation
    print("=" * 60)
    print("Clean evaluation")
    print("=" * 60)
    result = client.evaluate(profile, {"action": {"type": "respond"}})
    print(f"Score: {result.composite:.3f} | Passed: {result.passed} | Action: {result.action}")
    print()

    # 4. PII access without clearance → BLOCKED
    print("=" * 60)
    print("PII access without clearance")
    print("=" * 60)
    result = client.evaluate(profile, {
        "action": {"type": "data_query", "data_classification": "pii"},
    })
    print(f"Score: {result.composite:.3f} | Passed: {result.passed} | Action: {result.action}")
    for lr in result.layer_results:
        if not lr.passed:
            print(f"  [{lr.layer_name}] {lr.action}: score={lr.score:.3f}")
    print()

    # 5. Generate an attestation record
    print("=" * 60)
    print("Attestation")
    print("=" * 60)
    clean_result = client.evaluate(profile, {"action": {"type": "respond"}})
    attestation = client.attest(profile, clean_result)
    print(f"Record: {attestation}")
    print(f"Content hash: {attestation.content_hash[:24]}...")
    print(f"Evidence items: {len(attestation.evidence)}")
    print()

    # 6. Set up a monitor using the client
    print("=" * 60)
    print("Runtime monitor")
    print("=" * 60)
    monitor = client.monitor(profile, threshold=0.60, on_violation="alert")
    score = monitor.evaluate({"action": {"type": "respond"}})
    print(f"Monitor score: {score.composite:.3f}")
    print(f"Chain length: {len(monitor.attestation_chain)}")


if __name__ == "__main__":
    main()
