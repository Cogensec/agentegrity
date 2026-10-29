"""A framework-independent observation demo for a synthetic research agent.

This records host-supplied metadata only. There is no agent execution hook,
policy enforcement, remote identity verification, or network export.
Run: python examples/host_agent_observation.py
"""

from agentegrity.core.host_observer import HostAgentObserver, ToolCompletion


def run_demo() -> list[dict[str, object]]:
    observer = HostAgentObserver(
        agent_id="research-agent",
        run_id="research-session",
        host="custom-host",
        integration="python-host",
        max_events=8,
    )
    observer.start()
    observer.tool_intent(
        action_id="lookup-1", tool_name="search_index", effect="read"
    )
    observer.tool_outcome(
        action_id="lookup-1", completion=ToolCompletion.RETURNED
    )
    observer.tool_intent(
        action_id="document-2", tool_name="write_report", effect="write"
    )
    # A crashed process could leave an external action unresolved.
    # Closing marks it uncertain, never verified or rolled back.
    observer.finish()
    return observer.export()


if __name__ == "__main__":
    events = run_demo()
    for event in events:
        print(event["event_type"], event.get("outcome", ""))
