from __future__ import annotations

from typing import TYPE_CHECKING

from .claude import ClaudeProvider
from .codex import CodexProvider
from .gemini import GeminiProvider

if TYPE_CHECKING:
    from pathlib import Path
    from .base import BaseProvider
    from ..core.config import AppConfig


__all__ = ["ClaudeProvider", "CodexProvider", "GeminiProvider", "get_provider", "get_all_providers"]


def get_provider(name: str, config: AppConfig) -> BaseProvider | None:
    if name == "claude":
        return ClaudeProvider(
            settings_path=config.claude_settings_path,
            hook_command_prefix=config.hook_command_prefix,
            socket_path=config.event_socket_path,
            legacy_hook_script_paths=(config.claude_hook_script_path,),
        )
    if name == "codex":
        return CodexProvider(
            state_db_path=config.codex_state_db_path,
            history_path=config.codex_history_path,
            hooks_config_path=config.codex_hooks_path,
            hook_command_prefix=config.hook_command_prefix,
            hook_script_path=config.codex_hook_script_path,
            hook_script_source_path=config.codex_hook_script_source_path,
            managed_hook_script_paths=(config.codex_hook_script_source_path,),
        )
    if name == "gemini":
        return GeminiProvider(
            settings_path=config.gemini_settings_path,
            tmp_dir=config.gemini_tmp_dir,
            hook_command_prefix=config.hook_command_prefix,
        )
    return None


def get_all_providers(config: AppConfig) -> list[BaseProvider]:
    providers = []
    for name in ["claude", "codex", "gemini"]:
        p = get_provider(name, config)
        if p:
            providers.append(p)
    return providers
