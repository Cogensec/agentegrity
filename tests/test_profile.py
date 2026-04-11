"""Tests for AgentProfile."""


from agentegrity.core.profile import (
    CAPABILITY_CODE_EXECUTION,
    CAPABILITY_MEMORY_ACCESS,
    CAPABILITY_MULTI_AGENT_COMM,
    CAPABILITY_PHYSICAL_ACTUATION,
    CAPABILITY_TOOL_USE,
    CAPABILITY_WEB_ACCESS,
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)


def make_profile(**overrides):
    defaults = {
        "agent_type": AgentType.TOOL_USING,
        "capabilities": [CAPABILITY_TOOL_USE],
        "deployment_context": DeploymentContext.CLOUD,
        "risk_tier": RiskTier.MEDIUM,
    }
    defaults.update(overrides)
    return AgentProfile(**defaults)


class TestAgentProfile:
    def test_creation_with_defaults(self):
        profile = make_profile()
        assert profile.agent_type == AgentType.TOOL_USING
        assert profile.risk_tier == RiskTier.MEDIUM
        assert profile.agent_id  # auto-generated

    def test_has_capability(self):
        profile = make_profile(capabilities=["tool_use", "memory_access"])
        assert profile.has_capability("tool_use")
        assert profile.has_capability("memory_access")
        assert not profile.has_capability("code_execution")

    def test_is_autonomous(self):
        assert not make_profile(agent_type=AgentType.CONVERSATIONAL).is_autonomous
        assert not make_profile(agent_type=AgentType.TOOL_USING).is_autonomous
        assert make_profile(agent_type=AgentType.AUTONOMOUS).is_autonomous
        assert make_profile(agent_type=AgentType.MULTI_AGENT).is_autonomous
        assert make_profile(agent_type=AgentType.EMBODIED).is_autonomous

    def test_is_embodied(self):
        assert make_profile(agent_type=AgentType.EMBODIED).is_embodied
        assert make_profile(
            deployment_context=DeploymentContext.PHYSICAL
        ).is_embodied
        assert make_profile(
            capabilities=[CAPABILITY_PHYSICAL_ACTUATION]
        ).is_embodied
        assert not make_profile().is_embodied

    def test_input_channels_basic(self):
        profile = make_profile(capabilities=[])
        assert profile.input_channels == ["direct_prompt"]

    def test_input_channels_full(self):
        profile = make_profile(
            capabilities=[
                CAPABILITY_TOOL_USE,
                CAPABILITY_MEMORY_ACCESS,
                CAPABILITY_MULTI_AGENT_COMM,
                CAPABILITY_WEB_ACCESS,
                CAPABILITY_CODE_EXECUTION,
            ]
        )
        channels = profile.input_channels
        assert "direct_prompt" in channels
        assert "tool_responses" in channels
        assert "memory_reads" in channels
        assert "peer_messages" in channels
        assert "web_content" in channels
        assert "code_outputs" in channels

    def test_serialization_roundtrip(self):
        profile = make_profile(
            name="test-agent",
            framework="langchain",
            model_provider="anthropic",
        )
        data = profile.to_dict()
        restored = AgentProfile.from_dict(data)
        assert restored.name == profile.name
        assert restored.agent_type == profile.agent_type
        assert restored.capabilities == profile.capabilities
        assert restored.framework == profile.framework

    def test_repr(self):
        profile = make_profile(name="my-agent")
        r = repr(profile)
        assert "my-agent" in r
        assert "tool_using" in r
