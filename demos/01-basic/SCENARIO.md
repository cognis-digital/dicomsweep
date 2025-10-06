# Demo 01 - Basic de-identification

## What this shows

A minimal but **real** DICOM Part-10 file (`sample.dcm`) that carries patient
PHI in its metadata: a patient name, ID, birth date, accession number,
referring physician, and study date.

The file was generated with the small builder script `make_sample.py` (also in
this folder) using **only the standard library** - no pydicom needed. It has a
proper 128-byte preamble, the `DICM` magic, a File Meta group declaring the
Explicit VR Little Endian transfer syntax, and the dataset elements.

## Run it

```bash
# 1. Detect PHI (read-only). Exits 1 because PHI is present.
python -m dicomsweep scan demos/01-basic/sample.dcm

# 2. Machine-readable for CI:
python -m dicomsweep scan demos/01-basic/sample.dcm --format json

# 3. Produce a research-safe copy:
python -m dicomsweep sweep demos/01-basic/sample.dcm -o /tmp/sample.safe.dcm

# 4. Verify the copy is clean (exits 0):
python -m dicomsweep scan /tmp/sample.safe.dcm
```

## Expected result

- `scan` on `sample.dcm` reports several findings, including:
  - `(0010,0010) PatientName`  -> replace with `ANONYMOUS`
  - `(0010,0020) PatientID`    -> replace with `ANON-ID`
  - `(0010,0030) PatientBirthDate` -> remove
  - `(0008,0050) AccessionNumber`  -> remove
  - `(0008,0090) ReferringPhysicianName` -> remove
  - `(0008,0020) StudyDate` -> replace with `19000101`
  and the process exits with status **1** (PHI present).

- `sweep` writes `sample.safe.dcm` and exits **0**.

- `scan` on the swept file finds the same *tags* but their values are now
  blanked or replaced; running it as a gate, you can confirm no real patient
  identifiers remain. The structurally valid DICOM file is preserved.
