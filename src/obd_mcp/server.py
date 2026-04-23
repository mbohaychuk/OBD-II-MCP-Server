from mcp.server.fastmcp import FastMCP

mcp = FastMCP("obd-mcp")


@mcp.tool()
def ping() -> str:
    """Health check. Returns 'pong' if the server is alive."""
    return "pong"


def main() -> None:
    mcp.run()
