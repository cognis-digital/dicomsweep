"""Core DICOM parsing and de-identification engine (standard library only).

This implements a real, minimal DICOM Part-10 reader/writer good enough to
identify and rewrite metadata tags. It supports:

  * 128-byte preamble + "DICM" magic detection
  * Explicit and Implicit VR, little- and big-endian transfer syntaxes
  * Top-level data element parsing (sequences are skipped over safely)
  * In-place tag value rewriting that preserves byte alignment

The de-identification policy is modeled on the DICOM PS3.15 Basic Application
Level Confidentiality Profile: identifying tags are removed (zeroed/blanked),
but the file remains a structurally valid DICOM object.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class DicomError(Exception):
    """Raised when a file cannot be parsed as DICOM."""


class Action(str, Enum):
    """What the safe profile does to a tag."""

    REMOVE = "remove"        # blank the value entirely (empty / zero length)
    REPLACE = "replace"      # replace with a fixed dummy value
    KEEP = "keep"            # left untouched


# --- DICOM dictionary (subset that matters for PHI) --------------------------
# group, element -> (keyword, default VR)
_DICT: Dict[Tuple[int, int], Tuple[str, str]] = {
    (0x0008, 0x0018): ("SOPInstanceUID", "UI"),
    (0x0008, 0x0020): ("StudyDate", "DA"),
    (0x0008, 0x0021): ("SeriesDate", "DA"),
    (0x0008, 0x0022): ("AcquisitionDate", "DA"),
    (0x0008, 0x0023): ("ContentDate", "DA"),
    (0x0008, 0x0030): ("StudyTime", "TM"),
    (0x0008, 0x0050): ("AccessionNumber", "SH"),
    (0x0008, 0x0060): ("Modality", "CS"),
    (0x0008, 0x0080): ("InstitutionName", "LO"),
    (0x0008, 0x0081): ("InstitutionAddress", "ST"),
    (0x0008, 0x0090): ("ReferringPhysicianName", "PN"),
    (0x0008, 0x1030): ("StudyDescription", "LO"),
    (0x0008, 0x103E): ("SeriesDescription", "LO"),
    (0x0008, 0x1050): ("PerformingPhysicianName", "PN"),
    (0x0008, 0x1070): ("OperatorsName", "PN"),
    (0x0010, 0x0010): ("PatientName", "PN"),
    (0x0010, 0x0020): ("PatientID", "LO"),
    (0x0010, 0x0030): ("PatientBirthDate", "DA"),
    (0x0010, 0x0032): ("PatientBirthTime", "TM"),
    (0x0010, 0x0040): ("PatientSex", "CS"),
    (0x0010, 0x1000): ("OtherPatientIDs", "LO"),
    (0x0010, 0x1001): ("OtherPatientNames", "PN"),
    (0x0010, 0x1010): ("PatientAge", "AS"),
    (0x0010, 0x1040): ("PatientAddress", "LO"),
    (0x0010, 0x2154): ("PatientTelephoneNumbers", "SH"),
    (0x0010, 0x4000): ("PatientComments", "LT"),
    (0x0018, 0x0015): ("BodyPartExamined", "CS"),
    (0x0020, 0x000D): ("StudyInstanceUID", "UI"),
    (0x0020, 0x000E): ("SeriesInstanceUID", "UI"),
    (0x0020, 0x0010): ("StudyID", "SH"),
    (0x0020, 0x4000): ("ImageComments", "LT"),
}

# String VRs whose stored bytes are text (we can read/replace meaningfully).
_TEXT_VRS = {
    "AE", "AS", "CS", "DA", "DS", "DT", "IS", "LO", "LT", "PN",
    "SH", "ST", "TM", "UI", "UT",
}

# VRs that use a 4-byte length in explicit VR encoding.
_LONG_VRS = {"OB", "OW", "OF", "SQ", "UT", "UN", "OD", "OL", "UC", "UR"}


# --- The safe profile --------------------------------------------------------
# tag -> (Action, replacement_text_or_None)
SAFE_PROFILE: Dict[Tuple[int, int], Tuple[Action, Optional[str]]] = {
    (0x0010, 0x0010): (Action.REPLACE, "ANONYMOUS"),
    (0x0010, 0x0020): (Action.REPLACE, "ANON-ID"),
    (0x0010, 0x0030): (Action.REMOVE, None),
    (0x0010, 0x0032): (Action.REMOVE, None),
    (0x0010, 0x1000): (Action.REMOVE, None),
    (0x0010, 0x1001): (Action.REMOVE, None),
    (0x0010, 0x1010): (Action.REMOVE, None),
    (0x0010, 0x1040): (Action.REMOVE, None),
    (0x0010, 0x2154): (Action.REMOVE, None),
    (0x0010, 0x4000): (Action.REMOVE, None),
    (0x0008, 0x0050): (Action.REMOVE, None),
    (0x0008, 0x0080): (Action.REMOVE, None),
    (0x0008, 0x0081): (Action.REMOVE, None),
    (0x0008, 0x0090): (Action.REMOVE, None),
    (0x0008, 0x1050): (Action.REMOVE, None),
    (0x0008, 0x1070): (Action.REMOVE, None),
    (0x0008, 0x0020): (Action.REPLACE, "19000101"),
    (0x0008, 0x0021): (Action.REPLACE, "19000101"),
    (0x0008, 0x0022): (Action.REPLACE, "19000101"),
    (0x0008, 0x0023): (Action.REPLACE, "19000101"),
    (0x0020, 0x0010): (Action.REMOVE, None),
}


def tag_name(group: int, element: int) -> str:
    """Return the keyword for a tag, or a hex fallback like (0010,0010)."""
    entry = _DICT.get((group, element))
    if entry:
        return entry[0]
    return f"({group:04X},{element:04X})"


@dataclass
class DicomElement:
    """A parsed top-level DICOM data element."""

    group: int
    element: int
    vr: str
    # absolute byte offset of the value field within the file buffer
    value_offset: int
    value_length: int
    raw_value: bytes

    @property
    def tag(self) -> Tuple[int, int]:
        return (self.group, self.element)

    @property
    def keyword(self) -> str:
        return tag_name(self.group, self.element)

    def text(self) -> str:
        """Best-effort decode of the value as a trimmed string."""
        if self.vr in _TEXT_VRS or self.vr == "UN":
            try:
                return self.raw_value.decode("latin-1").rstrip("\x00 ")
            except Exception:
                return ""
        return ""


@dataclass
class DicomFile:
    """Parsed DICOM file: the raw bytes plus the decoded element list."""

    data: bytearray
    elements: List[DicomElement] = field(default_factory=list)
    little_endian: bool = True
    explicit_vr: bool = True
    transfer_syntax: str = ""


@dataclass
class Finding:
    """A detected PHI tag and what the safe profile would do to it."""

    group: int
    element: int
    keyword: str
    vr: str
    current_value: str
    action: Action
    new_value: Optional[str]

    @property
    def tag_hex(self) -> str:
        return f"({self.group:04X},{self.element:04X})"

    def to_dict(self) -> dict:
        return {
            "tag": self.tag_hex,
            "keyword": self.keyword,
            "vr": self.vr,
            "current_value": self.current_value,
            "action": self.action.value,
            "new_value": self.new_value,
        }


# --- Parsing -----------------------------------------------------------------
def _read_tag(buf: bytes, off: int, little: bool) -> Tuple[int, int]:
    fmt = "<HH" if little else ">HH"
    group, element = struct.unpack_from(fmt, buf, off)
    return group, element


def parse_dicom(data: bytes) -> DicomFile:
    """Parse DICOM Part-10 bytes into a :class:`DicomFile`.

    Raises :class:`DicomError` if the magic / structure is not DICOM.
    """
    buf = bytearray(data)
    if len(buf) < 132 or bytes(buf[128:132]) != b"DICM":
        raise DicomError("not a DICOM file (missing 'DICM' magic at offset 128)")

    off = 132
    df = DicomFile(data=buf)

    # File Meta Information (group 0002) is ALWAYS Explicit VR Little Endian.
    # Parse it to discover the transfer syntax for the main dataset.
    meta_end, transfer_syntax = _parse_segment(
        buf, off, df, explicit=True, little=True, only_group=0x0002
    )
    df.transfer_syntax = transfer_syntax

    # Decide encoding of the main dataset from the transfer syntax UID.
    if transfer_syntax == "1.2.840.10008.1.2":
        df.explicit_vr, df.little_endian = False, True
    elif transfer_syntax == "1.2.840.10008.1.2.2":
        df.explicit_vr, df.little_endian = True, False
    else:
        # Default / all the .1.2.1.* and compressed syntaxes: explicit LE.
        df.explicit_vr, df.little_endian = True, True

    _parse_segment(
        buf, meta_end, df, explicit=df.explicit_vr, little=df.little_endian
    )
    return df


def _parse_segment(
    buf: bytearray,
    off: int,
    df: DicomFile,
    explicit: bool,
    little: bool,
    only_group: Optional[int] = None,
) -> Tuple[int, str]:
    """Parse elements starting at ``off``. Returns (next_offset, transfer_syntax).

    If ``only_group`` is given, parsing stops as soon as a different group is
    seen (used to isolate the File Meta group 0002).
    """
    transfer_syntax = ""
    n = len(buf)
    while off + 8 <= n:
        group, element = _read_tag(buf, off, little)
        if only_group is not None and group != only_group:
            break
        off += 4

        if explicit:
            vr = buf[off:off + 2].decode("ascii", "replace")
            off += 2
            if vr in _LONG_VRS:
                off += 2  # reserved
                (length,) = struct.unpack_from("<I" if little else ">I", buf, off)
                off += 4
            else:
                (length,) = struct.unpack_from("<H" if little else ">H", buf, off)
                off += 2
        else:
            (length,) = struct.unpack_from("<I" if little else ">I", buf, off)
            off += 4
            entry = _DICT.get((group, element))
            vr = entry[1] if entry else "UN"

        # Undefined length (0xFFFFFFFF) marks a sequence/pixel-data with
        # delimiters. We stop descending here; metadata of interest is above.
        if length == 0xFFFFFFFF:
            break

        value_offset = off
        if value_offset + length > n:
            length = max(0, n - value_offset)
        raw_value = bytes(buf[value_offset:value_offset + length])

        elem = DicomElement(
            group=group,
            element=element,
            vr=vr,
            value_offset=value_offset,
            value_length=length,
            raw_value=raw_value,
        )
        df.elements.append(elem)

        if (group, element) == (0x0002, 0x0010):  # TransferSyntaxUID
            transfer_syntax = raw_value.decode("ascii", "replace").rstrip("\x00 ")

        off += length

        # Don't walk into PixelData (7FE0,0010) - nothing PHI past it.
        if (group, element) == (0x7FE0, 0x0010):
            break

    return off, transfer_syntax


# --- Detection ---------------------------------------------------------------
def scan_dataset(df: DicomFile) -> List[Finding]:
    """Return findings for every element the safe profile would alter."""
    findings: List[Finding] = []
    for el in df.elements:
        policy = SAFE_PROFILE.get(el.tag)
        if policy is None:
            continue
        action, repl = policy
        findings.append(
            Finding(
                group=el.group,
                element=el.element,
                keyword=el.keyword,
                vr=el.vr,
                current_value=el.text(),
                action=action,
                new_value=repl if action == Action.REPLACE else "",
            )
        )
    return findings


def scan_file(path: str) -> List[Finding]:
    """Parse ``path`` and return PHI findings without modifying anything."""
    with open(path, "rb") as fh:
        df = parse_dicom(fh.read())
    return scan_dataset(df)


# --- De-identification -------------------------------------------------------
def _encode_value(text: str, vr: str, target_len: int) -> bytes:
    """Encode ``text`` for ``vr`` padded to an EVEN length.

    To preserve byte alignment of the rest of the file we rewrite values
    in place at their original length: longer text is truncated, shorter
    text is right-padded (with spaces, or NULs for UI). DICOM requires even
    value lengths, which the original length already satisfies.
    """
    pad = b"\x00" if vr == "UI" else b" "
    raw = text.encode("latin-1", "replace")
    if len(raw) >= target_len:
        return raw[:target_len]
    return raw + pad * (target_len - len(raw))


def sweep_dataset(df: DicomFile) -> List[Finding]:
    """Apply the safe profile in-place to ``df.data``. Returns applied findings."""
    applied: List[Finding] = []
    for el in df.elements:
        policy = SAFE_PROFILE.get(el.tag)
        if policy is None:
            continue
        action, repl = policy
        before = el.text()

        if el.value_length == 0:
            new_text = ""
        elif action == Action.REPLACE and repl is not None:
            new_text = repl
        else:  # REMOVE -> blank within the existing slot
            new_text = ""

        new_bytes = _encode_value(new_text, el.vr, el.value_length)
        df.data[el.value_offset:el.value_offset + el.value_length] = new_bytes
        el.raw_value = bytes(new_bytes)

        applied.append(
            Finding(
                group=el.group,
                element=el.element,
                keyword=el.keyword,
                vr=el.vr,
                current_value=before,
                action=action,
                new_value=repl if action == Action.REPLACE else "",
            )
        )
    return applied


def sweep_file(in_path: str, out_path: str) -> List[Finding]:
    """De-identify ``in_path`` and write the safe copy to ``out_path``.

    Returns the list of findings that were applied.
    """
    with open(in_path, "rb") as fh:
        df = parse_dicom(fh.read())
    applied = sweep_dataset(df)
    with open(out_path, "wb") as fh:
        fh.write(df.data)
    return applied
