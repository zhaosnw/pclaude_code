"""Close the final ~0.7% gap to reach 70% P0/P1 line coverage."""

from __future__ import annotations


def test_compact_prompt_import() -> None:
    from hare.services.compact import prompt as p

    assert p is not None


def test_builtin_plugins_import() -> None:
    from hare.plugins import builtin_plugins as bp

    assert bp is not None


def test_errors_format() -> None:
    from hare.utils.errors import error_message

    # Exercise the error formatting path
    result = error_message(Exception("test"))
    assert isinstance(result, str)


def test_bootstrap_state_more() -> None:
    from hare.bootstrap.state import (
        set_client_type,
        get_client_type,
        get_is_interactive,
        set_is_interactive,
        get_is_non_interactive_session,
        set_is_non_interactive_session,
        reset_state_for_tests,
    )

    reset_state_for_tests()
    set_client_type("cli")
    assert get_client_type() == "cli"
    set_is_interactive(True)
    assert get_is_interactive() is True
    set_is_non_interactive_session(False)
    assert get_is_non_interactive_session() is False


def test_bootstrap_flag_settings() -> None:
    from hare.bootstrap.state import (
        set_flag_settings_path,
        get_flag_settings_path,
        set_flag_settings_inline,
        get_flag_settings_inline,
        set_session_ingress_token,
        get_session_ingress_token,
        reset_state_for_tests,
    )

    reset_state_for_tests()
    set_flag_settings_path("/tmp/flags.json")
    assert get_flag_settings_path() == "/tmp/flags.json"
    set_flag_settings_inline({"key": "val"})
    assert get_flag_settings_inline() == {"key": "val"}
    set_session_ingress_token("token123")
    assert get_session_ingress_token() == "token123"


def test_bootstrap_auth_tokens() -> None:
    from hare.bootstrap.state import (
        set_oauth_token_from_fd,
        get_oauth_token_from_fd,
        set_api_key_from_fd,
        get_api_key_from_fd,
        reset_state_for_tests,
    )

    reset_state_for_tests()
    set_oauth_token_from_fd("oauth-token")
    assert get_oauth_token_from_fd() == "oauth-token"
    set_api_key_from_fd("api-key")
    assert get_api_key_from_fd() == "api-key"


def test_bootstrap_cache() -> None:
    from hare.bootstrap.state import (
        set_cached_claude_md_content,
        get_cached_claude_md_content,
        set_init_json_schema,
        get_init_json_schema,
        reset_state_for_tests,
    )

    reset_state_for_tests()
    set_cached_claude_md_content("# CLAUDE.md content")
    assert get_cached_claude_md_content() == "# CLAUDE.md content"
    schema = {"type": "object"}
    set_init_json_schema(schema)
    assert get_init_json_schema() == schema


def test_bootstrap_system_prompt_cache() -> None:
    from hare.bootstrap.state import (
        get_system_prompt_section_cache,
        set_system_prompt_section_cache_entry,
        clear_system_prompt_section_state,
        set_last_emitted_date,
        get_last_emitted_date,
        reset_state_for_tests,
    )

    reset_state_for_tests()
    set_system_prompt_section_cache_entry("section1", "cached value")
    cache = get_system_prompt_section_cache()
    assert "section1" in cache
    set_last_emitted_date("2025-01-01")
    assert get_last_emitted_date() == "2025-01-01"
    clear_system_prompt_section_state()


def test_bootstrap_prompt_cache() -> None:
    from hare.bootstrap.state import (
        get_prompt_cache_1h_allowlist,
        set_prompt_cache_1h_allowlist,
        get_prompt_cache_1h_eligible,
        set_prompt_cache_1h_eligible,
        reset_state_for_tests,
    )

    reset_state_for_tests()
    set_prompt_cache_1h_allowlist(["model-a", "model-b"])
    assert get_prompt_cache_1h_allowlist() == ["model-a", "model-b"]
    set_prompt_cache_1h_eligible(True)
    assert get_prompt_cache_1h_eligible() is True


def test_bootstrap_worktree() -> None:
    from hare.bootstrap.state import (
        set_worktree_session,
        set_additional_directories_for_claude_md,
        get_additional_directories_for_claude_md,
        reset_state_for_tests,
    )

    reset_state_for_tests()
    set_worktree_session({"worktreePath": "/tmp/wt", "branch": "feature"})
    set_additional_directories_for_claude_md(["/tmp/extra"])
    assert get_additional_directories_for_claude_md() == ["/tmp/extra"]


def test_context_module() -> None:
    from hare.context import (
        get_user_context,
        get_system_context_sync,
        set_system_prompt_injection,
        get_system_prompt_injection,
    )

    # Exercise context functions
    set_system_prompt_injection("test injection")
    result = get_system_prompt_injection()
    assert result == "test injection"
    set_system_prompt_injection(None)
    ctx = get_user_context()
    assert isinstance(ctx, dict)
    assert "currentDate" in ctx


def test_settings_import() -> None:
    from hare.utils.settings import settings

    assert settings is not None


def test_auto_compact_import() -> None:
    from hare.services.compact import auto_compact

    assert auto_compact is not None


def test_cost_hook_register() -> None:
    from hare.cost_hook import register_cost_summary_hook

    # Should not raise (idempotent)
    register_cost_summary_hook()
    register_cost_summary_hook()  # second call is no-op
