"""DICOMSWEEP MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from dicomsweep.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-dicomsweep[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-dicomsweep[mcp]'")
        return 1
    app = FastMCP("dicomsweep")

    @app.tool()
    def dicomsweep_scan(target: str) -> str:
        """De-identify DICOM imaging studies per the DICOM PS3.15 Annex E profile, scrubbing tags and burned-in pixel text.. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
