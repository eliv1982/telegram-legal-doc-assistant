"""
Guards for the test setup itself: the app imports without credentials, and the network guard really blocks.
"""
import importlib
import socket

import pytest

MODULES = [
    "bot",
    "config",
    "handlers.callbacks",
    "handlers.document",
    "handlers.start",
    "prompts.analysis_prompt",
    "prompts.response_prompt",
    "services.checklist_generator",
    "services.openai_service",
    "services.pdf_converter",
    "services.tts_service",
    "states.user_states",
    "utils.helpers",
    "utils.logging_config",
]


def test_all_modules_import_without_credentials():
    for module in MODULES:
        importlib.import_module(module)

    # Proves the conftest isolation: neither a real .env nor the ambient environment reached `config`.
    import config

    assert config.BOT_TOKEN == ""
    assert config.OPENAI_API_KEY == ""


def test_network_guard_blocks_external_hosts():
    with pytest.raises(RuntimeError, match="network access blocked"):
        socket.getaddrinfo("api.openai.com", 443)  # DNS: what httpx/aiohttp/requests all do first
    with pytest.raises(RuntimeError, match="network access blocked"):
        socket.create_connection(("api.telegram.org", 443), timeout=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match="network access blocked"):
            sock.connect(("8.8.8.8", 53))  # IP literal: bypasses DNS
