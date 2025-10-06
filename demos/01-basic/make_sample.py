"""Generate demos/01-basic/sample.dcm with the standard library only.

Produces a real DICOM Part-10 file (preamble + DICM + File Meta in Explicit VR
Little Endian + a dataset carrying PHI). Re-run if you want to regenerate it:

    python demos/01-basic/make_sample.py
"""
import os
import struct

_SHORT_VR = {"AE", "AS", "AT", "CS", "DA", "DS", "DT", "FL", "FD", "IS",
             "LO", "LT", "PN", "SH", "SL", "SS", "ST", "TM", "UI", "UL", "US"}


def _even(b: bytes, pad: bytes) -> bytes:
    return b if len(b) % 2 == 0 else b + pad


def elem(group: int, element: int, vr: str, value: str) -> bytes:
    pad = b"\x00" if vr == "UI" else b" "
    raw = _even(value.encode("latin-1"), pad)
    out = struct.pack("<HH", group, element) + vr.encode("ascii")
    if vr in _SHORT_VR:
        out += struct.pack("<H", len(raw))
    else:
        out += b"\x00\x00" + struct.pack("<I", len(raw))
    return out + raw


def build() -> bytes:
    ts = "1.2.840.10008.1.2.1"  # Explicit VR Little Endian
    meta_body = b"".join([
        elem(0x0002, 0x0002, "UI", "1.2.840.10008.5.1.4.1.1.7"),
        elem(0x0002, 0x0003, "UI", "1.2.3.4.5.6.7.8.9.0"),
        elem(0x0002, 0x0010, "UI", ts),
        elem(0x0002, 0x0012, "UI", "1.2.3.4.5"),
    ])
    meta = elem(0x0002, 0x0000, "UL", "")  # placeholder, fixed below
    meta = (struct.pack("<HH", 0x0002, 0x0000) + b"UL" +
            struct.pack("<H", 4) + struct.pack("<I", len(meta_body)))

    dataset = b"".join([
        elem(0x0008, 0x0020, "DA", "20240115"),
        elem(0x0008, 0x0050, "SH", "ACC123456"),
        elem(0x0008, 0x0060, "CS", "CT"),
        elem(0x0008, 0x0090, "PN", "WELBY^MARCUS"),
        elem(0x0010, 0x0010, "PN", "DOE^JANE^Q"),
        elem(0x0010, 0x0020, "LO", "MRN-0098765"),
        elem(0x0010, 0x0030, "DA", "19850642"),
        elem(0x0010, 0x0040, "CS", "F"),
        elem(0x0020, 0x0010, "SH", "STUDY-77"),
    ])

    return b"\x00" * 128 + b"DICM" + meta + meta_body + dataset


def main() -> None:
    out = os.path.join(os.path.dirname(__file__), "sample.dcm")
    with open(out, "wb") as fh:
        fh.write(build())
    print(f"wrote {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main()
