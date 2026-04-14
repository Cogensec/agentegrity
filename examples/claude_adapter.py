"""
Example: Instrumenting a Claude Agent SDK agent with agentegrity.

Registers agentegrity hooks at the Claude Agent SDK's extension points
so every tool call, user prompt, and stop event is evaluated for
structural integrity.

Requirements:
    pip install agentegrity[claude]

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/claude_adapter.py
"""

from __future__ import annotations

import asyncio

from agentegrity.sdk.client import AgentegrityClient


async def main() -> None:
    client = AgentegrityClient()
    profile = client.create_profile(
        name="research-agent",
        agent_type="tool_using",
        capabilities=["web_search", "code_execution"],
        risk_tier="medium",
    )

    # Measure-only mode: hooks observe but never block tool calls.
    adapter = client.create_claude_adapter(profile=profile, enforce=False)

    # To wire into an SDK session:
    #
    #     from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    #
    #     options = ClaudeAgentOptions(hooks=adapter.create_hooks())
    #     async with ClaudeSDKClient(options=options) as sdk:
    #         await sdk.query("Research the latest LLM benchmarks")
    #         async for message in sdk.receive_response():
    #             print(message)
    #
    # For this standalone demo, simulate the hook events directly:

    await adapter.on_event("user_prompt_submit", {"prompt": "Research LLM benchmarks"})
    await adapter.on_event(
        "pre_tool_use",
        {"tool_name": "WebSearch", "tool_input": {"query": "LLM benchmarks 2026"}},
    )
    await adapter.on_event(
        "post_tool_use",
        {"tool_name": "WebSearch", "tool_response": "Found 10 results..."},
    )
    await adapter.on_event("stop", {})

    summary = adapter.get_summary()
    print("Session summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    print(f"\nAttestation chain verified: {adapter.attestation_chain.verify_chain()}")
    print(f"Records: {len(adapter.attestation_chain.records)}")


if __name__ == "__main__":
    asyncio.run(main())
