import asyncio

from src.agent.registry import AgentStatus
from src.orchestrator.engine import OrchestrationEngine


def test_engine_rejects_disabled_agent_before_execution():
    engine = OrchestrationEngine()
    agent_id = engine.registry.register(
        "agent-1",
        "worker.processor",
        {"disabled": True},
    )
    errors = []

    async def on_error(task, error):
        errors.append((task["id"], str(error)))

    engine.register_hook("on_error", on_error)
    asyncio.run(
        engine._execute_task(
            {
                "id": "task-1",
                "target_agent": agent_id,
            }
        )
    )

    agent = engine.registry.get(agent_id)
    assert agent["status"] == AgentStatus.DISABLED.value
    assert errors == [("task-1", f"Agent {agent_id} not found or disabled")]
    assert any(
        event["event"] == "disabled_agent_resolution_rejected"
        and event["agent_id"] == agent_id
        for event in engine.registry.audit_events()
    )
