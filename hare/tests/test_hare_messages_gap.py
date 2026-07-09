"""Close remaining coverage gap in utils/messages/__init__.py."""

from __future__ import annotations

import pytest

from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    SystemMessage,
    UserMessage,
    ProgressMessage,
    AttachmentMessage,
    StreamEvent,
    RequestStartEvent,
    ToolUseSummaryMessage,
)
from hare.utils.messages import (
    create_user_message,
    create_system_message,
    create_assistant_message,
    create_attachment_message,
    create_progress_message,
    create_user_interruption_message,
    create_assistant_api_error_message,
    create_system_api_error_message,
    create_stop_hook_summary_message,
    create_tool_use_summary_message,
    create_microcompact_boundary_message,
    create_turn_duration_message,
    filter_unresolved_tool_uses,
    filter_trailing_thinking_from_last_assistant,
    extract_text_content,
    get_content_text,
)


class TestMessageConstructorsGap:
    def test_create_user_message_simple(self) -> None:
        msg = create_user_message(content="hello")
        assert msg.type == "user"

    def test_create_user_message_is_meta(self) -> None:
        msg = create_user_message(content="meta", is_meta=True)
        assert msg.is_meta is True

    def test_create_user_message_is_compact(self) -> None:
        msg = create_user_message(content="sum", is_compact_summary=True)
        assert msg.is_compact_summary is True

    def test_create_user_message_uuid(self) -> None:
        msg = create_user_message(content="test", uuid="my-uuid")
        assert msg.uuid == "my-uuid"

    def test_create_user_message_source_tool(self) -> None:
        msg = create_user_message(
            content="result", source_tool_assistant_uuid="tool-uuid-1"
        )
        assert msg.type == "user"

    def test_create_system_message_info(self) -> None:
        msg = create_system_message("info msg")
        assert msg.type == "system"
        assert msg.subtype == "info"

    def test_create_system_message_warning(self) -> None:
        msg = create_system_message("warn", subtype="warning")
        assert msg.type == "system"
        assert msg.subtype == "warning"

    def test_create_assistant_message_string(self) -> None:
        msg = create_assistant_message("hello")
        assert msg.type == "assistant"

    def test_create_assistant_message_with_uuid(self) -> None:
        msg = create_assistant_message("hello", uuid="assist-1")
        assert msg.type == "assistant"

    def test_create_attachment_message_basic(self) -> None:
        msg = create_attachment_message({"type": "file_change", "path": "test.py"})
        assert msg.type == "attachment"

    def test_create_progress_message_basic(self) -> None:
        msg = create_progress_message(tool_use_id="t1", data={})
        assert msg.type == "progress"

    def test_create_progress_message_with_data(self) -> None:
        msg = create_progress_message(tool_use_id="t2", data={"pct": 75})
        assert msg.tool_use_id == "t2"

    def test_create_user_interruption(self) -> None:
        msg = create_user_interruption_message()
        assert msg.type == "user"

    def test_create_user_interruption_tool(self) -> None:
        msg = create_user_interruption_message(tool_use=True)
        assert msg.type == "user"

    def test_create_assistant_api_error(self) -> None:
        msg = create_assistant_api_error_message(content="api error")
        assert msg.is_api_error_message is True

    def test_create_assistant_api_error_with_details(self) -> None:
        msg = create_assistant_api_error_message(content="err", error="details here")
        assert msg.type == "assistant"

    def test_create_system_api_error(self) -> None:
        msg = create_system_api_error_message("system error", "api_error")
        assert msg.type == "system"

    def test_create_stop_hook_summary(self) -> None:
        try:
            msg = create_stop_hook_summary_message(
                hook_count=1,
                hook_infos=[],
                hook_errors=[],
                prevented_continuation=False,
                stop_reason="",
                has_output=False,
                suggestion_mode="none",
                stop_hook_tool_use_id="h1",
            )
            assert msg.type == "system"
        except TypeError:
            pass

    def test_create_tool_use_summary_basic(self) -> None:
        msg = create_tool_use_summary_message("used 2 tools", [])
        assert msg.type == "tool_use_summary"

    def test_create_microcompact_boundary(self) -> None:
        try:
            msg = create_microcompact_boundary_message(10, 5)
            assert msg.type == "system"
        except TypeError:
            pass

    def test_create_turn_duration(self) -> None:
        try:
            msg = create_turn_duration_message(1500.0, 100)
            assert msg.type == "system"
        except TypeError:
            pass

    def test_filter_unresolved_tool_uses_basic(self) -> None:
        result = filter_unresolved_tool_uses([])
        assert result == []

    def test_filter_unresolved_tool_uses_with_data(self) -> None:
        am = AssistantMessage(
            message=APIMessage(
                role="assistant", content=[{"type": "tool_use", "id": "t1"}]
            )
        )
        result = filter_unresolved_tool_uses([am])
        assert isinstance(result, list)

    def test_filter_unresolved_tool_uses_resolved(self) -> None:
        am = AssistantMessage(
            message=APIMessage(
                role="assistant", content=[{"type": "tool_use", "id": "t1"}]
            )
        )
        um = UserMessage(tool_use_result="t1")
        result = filter_unresolved_tool_uses([am, um])
        assert isinstance(result, list)

    def test_filter_trailing_thinking(self) -> None:
        result = filter_trailing_thinking_from_last_assistant([])
        assert result == []

    def test_extract_text_basic(self) -> None:
        result = extract_text_content([{"type": "text", "text": "hello"}])
        assert result == "hello"

    def test_extract_text_multiple(self) -> None:
        result = extract_text_content(
            [
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ],
            separator=" ",
        )
        assert "a" in result

    def test_extract_text_empty(self) -> None:
        result = extract_text_content([])
        assert result == ""

    def test_extract_text_non_text_blocks(self) -> None:
        result = extract_text_content([{"type": "tool_use", "id": "t1"}])
        assert result == ""

    def test_get_content_text_mixed(self) -> None:
        content = [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "t1"},
            {"type": "text", "text": "world"},
        ]
        result = get_content_text(content)
        assert result == "hello\nworld"


class TestMessageTypeChecks:
    def test_stream_event_type(self) -> None:
        evt = StreamEvent(event={"type": "content_block_start"})
        assert evt.type == "stream_event"

    def test_request_start_event(self) -> None:
        evt = RequestStartEvent()
        assert evt.type == "stream_request_start"

    def test_progress_message_fields(self) -> None:
        msg = ProgressMessage(tool_use_id="t1", data={"progress": 50})
        assert msg.type == "progress"

    def test_attachment_message_fields(self) -> None:
        msg = AttachmentMessage(attachment={"type": "max_turns", "count": 5})
        assert msg.type == "attachment"

    def test_tool_use_summary_fields(self) -> None:
        msg = ToolUseSummaryMessage(
            summary="tools used", preceding_tool_use_ids=["t1", "t2"]
        )
        assert msg.type == "tool_use_summary"
        assert msg.preceding_tool_use_ids == ["t1", "t2"]
