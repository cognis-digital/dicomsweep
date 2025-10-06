"""Smoke tests: import core, run on a freshly built demo file, assert behavior.

No network. We regenerate the demo DICOM from the bundled builder so the test
is deterministic regardless of how the binary sample was checked out.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dicomsweep import (  # noqa: E402
    TOOL_NAME,
    TOOL_VERSION,
    Action,
    DicomError,
    parse_dicom,
    scan_file,
    sweep_file,
)
from dicomsweep.cli import main  # noqa: E402


def _build_sample(tmp_path):
    """Use the demo builder to write a real DICOM file into tmp_path."""
    builder_path = os.path.join(ROOT, "demos", "01-basic", "make_sample.py")
    spec = importlib.util.spec_from_file_location("make_sample", builder_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = mod.build()
    p = tmp_path / "sample.dcm"
    p.write_bytes(data)
    return str(p)


def test_version_constants():
    assert TOOL_NAME == "dicomsweep"
    assert TOOL_VERSION.count(".") == 2


def test_parse_real_dicom(tmp_path):
    path = _build_sample(tmp_path)
    with open(path, "rb") as fh:
        df = parse_dicom(fh.read())
    assert df.transfer_syntax == "1.2.840.10008.1.2.1"
    assert df.explicit_vr and df.little_endian
    tags = {el.tag for el in df.elements}
    assert (0x0010, 0x0010) in tags  # PatientName parsed
    assert (0x0002, 0x0010) in tags  # File Meta transfer syntax parsed


def test_non_dicom_raises():
    with pytest.raises(DicomError):
        parse_dicom(b"this is not a dicom file at all")


def test_scan_detects_phi(tmp_path):
    path = _build_sample(tmp_path)
    findings = scan_file(path)
    by_tag = {(f.group, f.element): f for f in findings}

    assert (0x0010, 0x0010) in by_tag
    assert by_tag[(0x0010, 0x0010)].current_value == "DOE^JANE^Q"
    assert by_tag[(0x0010, 0x0010)].action == Action.REPLACE

    assert (0x0008, 0x0050) in by_tag  # AccessionNumber
    assert by_tag[(0x0008, 0x0050)].action == Action.REMOVE

    # Modality is clinically useful and must NOT be a finding.
    assert (0x0008, 0x0060) not in by_tag


def test_sweep_removes_phi(tmp_path):
    path = _build_sample(tmp_path)
    out = str(tmp_path / "safe.dcm")
    applied = sweep_file(path, out)
    assert applied  # something was changed

    # Re-parse the swept file: it must still be valid DICOM...
    with open(out, "rb") as fh:
        df = parse_dicom(fh.read())
    vals = {el.tag: el.text() for el in df.elements}

    assert vals[(0x0010, 0x0010)] == "ANONYMOUS"   # replaced
    assert vals[(0x0010, 0x0020)] == "ANON-ID"     # replaced
    assert vals[(0x0010, 0x0030)] == ""            # birth date removed
    assert vals[(0x0008, 0x0050)] == ""            # accession removed
    assert vals[(0x0008, 0x0090)] == ""            # referring phys removed
    assert vals[(0x0008, 0x0060)] == "CT"          # modality preserved

    # The original real patient name must be gone from the raw bytes.
    assert b"DOE^JANE" not in df.data


def test_cli_scan_exit_code(tmp_path, capsys):
    path = _build_sample(tmp_path)
    rc = main(["scan", path, "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 1  # PHI present -> non-zero gate
    assert '"finding_count"' in out
    assert "DOE^JANE^Q" in out


def test_cli_sweep_then_clean(tmp_path):
    path = _build_sample(tmp_path)
    out = str(tmp_path / "clean.dcm")
    assert main(["sweep", path, "-o", out]) == 0

    # After sweeping, no real identifiers remain in the bytes.
    with open(out, "rb") as fh:
        raw = fh.read()
    assert b"DOE^JANE" not in raw
    assert b"ACC123456" not in raw
    assert b"ANONYMOUS" in raw
