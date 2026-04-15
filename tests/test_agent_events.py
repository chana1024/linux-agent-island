from linux_agent_island.runtime.agent_events import AgentEvent, AgentEventType


def test_agent_event_from_payload_parses_event_source() -> None:
    event = AgentEvent.from_payload(
        {
            "event_type": AgentEventType.ACTIVITY_UPDATED.value,
            "event_source": "UserPromptSubmit",
            "provider": "codex",
            "session_id": "thread-1",
            "updated_at": 123,
            "phase": "running",
        }
    )

    assert event.type is AgentEventType.ACTIVITY_UPDATED
    assert event.source == "UserPromptSubmit"


def test_agent_event_from_payload_parses_structured_actionable_state() -> None:
    event = AgentEvent.from_payload(
        {
            "event_type": AgentEventType.PERMISSION_REQUESTED.value,
            "provider": "claude",
            "session_id": "thread-1",
            "updated_at": 123,
            "permission_request": {
                "title": "Permission required",
                "summary": "needs approval",
                "affected_path": "/tmp/demo",
            },
        }
    )

    assert event.type is AgentEventType.PERMISSION_REQUESTED
    assert event.permission_request is not None
    assert event.permission_request.summary == "needs approval"


def test_agent_event_from_payload_parses_codex_metadata_update() -> None:
    event = AgentEvent.from_payload(
        {
            "event_type": AgentEventType.METADATA_UPDATED.value,
            "provider": "codex",
            "session_id": "thread-1",
            "updated_at": 456,
            "metadata_kind": "codex",
            "codex_metadata": {
                "transcript_path": "/tmp/rollout.jsonl",
                "last_user_prompt": "hello",
                "last_assistant_message": "hi",
            },
        }
    )

    assert event.type is AgentEventType.METADATA_UPDATED
    assert event.metadata_kind == "codex"
    assert event.codex_metadata is not None
    assert event.codex_metadata.last_assistant_message == "hi"


def test_agent_event_from_payload_parses_claude_metadata_update() -> None:
    event = AgentEvent.from_payload(
        {
            "event_type": AgentEventType.METADATA_UPDATED.value,
            "provider": "claude",
            "session_id": "thread-1",
            "updated_at": 456,
            "metadata_kind": "claude",
            "claude_metadata": {
                "transcript_path": "/tmp/claude.jsonl",
                "current_tool": "Write",
                "last_assistant_message": "done",
            },
        }
    )

    assert event.type is AgentEventType.METADATA_UPDATED
    assert event.metadata_kind == "claude"
    assert event.claude_metadata is not None
    assert event.claude_metadata.current_tool == "Write"
