"""Diagnostic: build an OpenHarness runtime the same way `oh` does, and dump
the real MCP connection status + registered tool names for m2s-server."""
import asyncio

from openharness.config.settings import load_settings
from openharness.mcp.client import McpClientManager
from openharness.mcp.config import load_mcp_server_configs
from openharness.tools import create_default_tool_registry


async def main() -> None:
    settings = load_settings()
    configs = load_mcp_server_configs(settings, [])
    print("configs:", list(configs.keys()))
    manager = McpClientManager(configs)
    await manager.connect_all()
    for status in manager.list_statuses():
        print(f"server={status.name} state={status.state} detail={status.detail!r} "
              f"n_tools={len(status.tools)} n_resources={len(status.resources)}")
        for t in status.tools:
            print("   tool:", t.name)
    registry = create_default_tool_registry(manager)
    m2s_tools = [name for name in registry._tools if "m2s" in name] if hasattr(registry, "_tools") else "n/a"
    print("registry m2s tool names:", m2s_tools)
    await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
