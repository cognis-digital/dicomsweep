"""DICOMSWEEP command-line interface.

Examples
--------
  # Detect PHI tags (does not modify the file). Exits non-zero if any found.
  dicomsweep scan scan.dcm

  # Machine-readable output for CI / piping:
  dicomsweep scan scan.dcm --format json | jq .

  # Write a research-safe, de-identified copy:
  dicomsweep sweep scan.dcm -o scan.safe.dcm

  # Verify a swept file is clean (exit 0 == clean):
  dicomsweep scan scan.safe.dcm && echo CLEAN
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import DicomError, Finding, scan_file, sweep_file


def _print_table(findings: List[Finding], title: str) -> None:
    if not findings:
        print("No PHI tags detected against the safe profile.")
        return
    print(title)
    print(f"{'TAG':<13} {'KEYWORD':<26} {'VR':<3} {'ACTION':<8} VALUE")
    print("-" * 78)
    for f in findings:
        cur = f.current_value if f.current_value else "(empty)"
        if len(cur) > 24:
            cur = cur[:21] + "..."
        print(f"{f.tag_hex:<13} {f.keyword:<26} {f.vr:<3} {f.action.value:<8} {cur}")
    print("-" * 78)
    print(f"{len(findings)} tag(s) match the safe profile.")


def _emit(findings: List[Finding], fmt: str, mode: str, **extra) -> None:
    if fmt == "json":
        payload = {
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "mode": mode,
            "finding_count": len(findings),
            "findings": [f.to_dict() for f in findings],
        }
        payload.update(extra)
        print(json.dumps(payload, indent=2))
    else:
        title = "PHI tags found:" if mode == "scan" else "De-identified tags:"
        _print_table(findings, title)
        for k, v in extra.items():
            print(f"{k}: {v}")


def _cmd_scan(args) -> int:
    try:
        findings = scan_file(args.file)
    except (DicomError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _emit(findings, args.format, "scan")
    # Non-zero exit when PHI is present -> usable as a CI gate.
    return 1 if findings else 0


def _cmd_sweep(args) -> int:
    out = args.output or _default_out(args.file)
    try:
        applied = sweep_file(args.file, out)
    except (DicomError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _emit(applied, args.format, "sweep", output=out)
    return 0


def _default_out(path: str) -> str:
    if path.lower().endswith(".dcm"):
        return path[:-4] + ".safe.dcm"
    return path + ".safe.dcm"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="De-identify DICOM tag metadata per a research-safe profile.",
        epilog=(
            "examples:\n"
            "  dicomsweep scan scan.dcm\n"
            "  dicomsweep scan scan.dcm --format json | jq .\n"
            "  dicomsweep sweep scan.dcm -o scan.safe.dcm\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--version", action="version",
        version=f"{TOOL_NAME} {TOOL_VERSION}",
    )
    p.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="output format (default: table)",
    )

    sub = p.add_subparsers(dest="command", metavar="<command>")

    sp = sub.add_parser(
        "scan", help="detect PHI tags (read-only; exits 1 if any are found)"
    )
    sp.add_argument("file", help="path to a .dcm file")
    sp.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="output format (default: table)",
    )
    sp.set_defaults(func=_cmd_scan)

    sw = sub.add_parser(
        "sweep", help="write a de-identified copy of the file"
    )
    sw.add_argument("file", help="path to a .dcm file")
    sw.add_argument(
        "-o", "--output",
        help="output path (default: <name>.safe.dcm)",
    )
    sw.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="output format (default: table)",
    )
    sw.set_defaults(func=_cmd_sweep)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
