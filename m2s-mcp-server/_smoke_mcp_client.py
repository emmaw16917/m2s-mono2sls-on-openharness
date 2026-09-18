"""P0 smoke test: launch server.py as a real MCP stdio subprocess and list
its tools, the way OpenHarness's McpClientManager would."""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PY = str(Path(__file__).resolve().parent / "server.py")


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[SERVER_PY])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"- {t.name}: {t.description.splitlines()[0] if t.description else ''}")


if __name__ == "__main__":
    asyncio.run(main())
