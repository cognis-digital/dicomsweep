"""Hardening tests: error paths, edge cases, and input validation.

All existing tests remain untouched; these add coverage for bad / edge inputs.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dicomsweep.core import (  # noqa: E402
    DicomError,
    parse_dicom,
    scan_file,
    sweep_file,
)
from dicomsweep.cli import main  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_sample(tmp_path):
    """Re-use the demo builder to create a real DICOM bytes blob."""
    builder_path = os.path.join(ROOT, "demos", "01-basic", "make_sample.py")
    spec = importlib.util.spec_from_file_location("make_sample", builder_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = mod.build()
    p = tmp_path / "sample.dcm"
    p.write_bytes(data)
    return str(p)


# ---------------------------------------------------------------------------
# parse_dicom edge cases
# ---------------------------------------------------------------------------

class TestParseDicomEdgeCases:
    def test_empty_bytes_raises(self):
        """Empty input is not valid DICOM and must raise DicomError."""
        with pytest.raises(DicomError):
            parse_dicom(b"")

    def test_truncated_header_raises(self):
        """Fewer than 132 bytes cannot contain a valid DICOM header."""
        with pytest.raises(DicomError):
            parse_dicom(b"\x00" * 100)

    def test_wrong_magic_raises(self):
        """Correct length but wrong magic bytes must raise DicomError."""
        bad = b"\x00" * 128 + b"NOTD" + b"\x00" * 100
        with pytest.raises(DicomError):
            parse_dicom(bad)

    def test_minimal_valid_header_no_elements(self):
        """132 bytes with correct magic but no elements is valid (empty dataset)."""
        data = b"\x00" * 128 + b"DICM"
        df = parse_dicom(data)
        assert df.elements == []


# ---------------------------------------------------------------------------
# scan_file / sweep_file input validation
# ---------------------------------------------------------------------------

class TestScanFileValidation:
    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            scan_file(str(tmp_path / "nonexistent.dcm"))

    def test_directory_path_raises_os_error(self, tmp_path):
        with pytest.raises(OSError):
            scan_file(str(tmp_path))

    def test_empty_path_string_raises(self):
        with pytest.raises(OSError):
            scan_file("")

    def test_empty_file_raises_dicom_error(self, tmp_path):
        p = tmp_path / "empty.dcm"
        p.write_bytes(b"")
        with pytest.raises(DicomError):
            scan_file(str(p))

    def test_non_dicom_file_raises_dicom_error(self, tmp_path):
        p = tmp_path / "garbage.dcm"
        p.write_bytes(b"this is not dicom at all, definitely not")
        with pytest.raises(DicomError):
            scan_file(str(p))


class TestSweepFileValidation:
    def test_missing_input_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sweep_file(str(tmp_path / "ghost.dcm"), str(tmp_path / "out.dcm"))

    def test_empty_in_path_raises(self, tmp_path):
        with pytest.raises(OSError):
            sweep_file("", str(tmp_path / "out.dcm"))

    def test_empty_out_path_raises(self, tmp_path):
        p = _build_sample(tmp_path)
        with pytest.raises(OSError):
            sweep_file(p, "")

    def test_same_in_out_path_raises(self, tmp_path):
        """Writing the output over the input must be rejected to protect the source."""
        p = _build_sample(tmp_path)
        with pytest.raises(ValueError, match="same file"):
            sweep_file(p, p)

    def test_empty_file_raises_dicom_error(self, tmp_path):
        p = tmp_path / "empty.dcm"
        p.write_bytes(b"")
        out = tmp_path / "out.dcm"
        with pytest.raises(DicomError):
            sweep_file(str(p), str(out))


# ---------------------------------------------------------------------------
# CLI error paths
# ---------------------------------------------------------------------------

class TestCliErrorPaths:
    def test_scan_missing_file_exits_2(self, tmp_path, capsys):
        rc = main(["scan", str(tmp_path / "ghost.dcm")])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err.lower() or "not found" in err.lower()

    def test_scan_non_dicom_exits_2(self, tmp_path, capsys):
        p = tmp_path / "junk.dcm"
        p.write_bytes(b"not a dicom file here at all")
        rc = main(["scan", str(p)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err

    def test_sweep_missing_file_exits_2(self, tmp_path, capsys):
        rc = main(["sweep", str(tmp_path / "ghost.dcm")])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err

    def test_no_subcommand_exits_0(self, capsys):
        """Running with no subcommand prints help and returns 0."""
        rc = main([])
        assert rc == 0

    def test_scan_json_missing_file(self, tmp_path, capsys):
        """JSON format flag must not prevent the error path from working."""
        rc = main(["scan", str(tmp_path / "ghost.dcm"), "--format", "json"])
        assert rc == 2

    def test_sweep_clean_file_exit_0(self, tmp_path):
        """After a sweep the output must be a clean DICOM file (re-parseable)."""
        p = _build_sample(tmp_path)
        out = str(tmp_path / "out.dcm")
        rc = main(["sweep", p, "-o", out])
        assert rc == 0
        assert os.path.isfile(out)
        # Verify the output is valid DICOM
        with open(out, "rb") as fh:
            df = parse_dicom(fh.read())
        assert df.elements
