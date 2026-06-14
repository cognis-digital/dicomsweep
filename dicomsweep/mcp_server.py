"""DICOMSWEEP MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations

import json
import sys

from dicomsweep.core import DicomError, scan_file


def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-dicomsweep[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "Install the MCP extra: pip install 'cognis-dicomsweep[mcp]'",
            file=sys.stderr,
        )
        return 1
    app = FastMCP("dicomsweep")

    @app.tool()
    def dicomsweep_scan(target: str) -> str:
        """De-identify DICOM imaging studies per the DICOM PS3.15 Annex E profile,
        scrubbing tags and burned-in pixel text. Returns JSON findings."""
        if not target or not target.strip():
            return json.dumps({"error": "target path must not be empty"})
        try:
            findings = scan_file(target)
        except (DicomError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(
            {"finding_count": len(findings), "findings": [f.to_dict() for f in findings]},
            indent=2,
        )

    app.run()
    return 0
