# RecoveryLens: AI-assisted recovery intelligence

**Live demo**: https://recoverylens-production.up.railway.app

> **Recover what exists. Infer relationships carefully. Never invent evidence.**

RecoveryLens is a working prototype that turns raw storage-image recovery output into
**structured, evidence-aware recovery intelligence**. It works out which fragments exist, which
belong together, what can be reconstructed, how complete and how valid each result is, what
to review first and what is realistically unrecoverable. It explains every conclusion.

It is **not** an undelete tool. It never fabricates bytes. Missing data is reported,
zero-filled in exports, and listed with its exact byte ranges.

```
Traditional:  Find â†’ Recover
RecoveryLens: Find â†’ Understand â†’ Relate â†’ Reconstruct â†’ Validate â†’ Measure â†’ Classify â†’ Prioritize â†’ Explain
```

---

## Quick start (Windows)

```bat
setup.cmd        :: once: venv + pip install, npm install
start-dev.cmd    :: API on :8000, UI on :5173, opens the browser
```

Manual equivalent:

```bat
cd backend && .venv\Scripts\python -m uvicorn app.main:app --port 8000
cd frontend && npm.cmd run dev
```

Single process: run `npm.cmd run build` in `frontend/`. The API then also serves the UI at
http://127.0.0.1:8000.

On first start the API generates the demo images into `backend/data/demo/` (about 1 s).

---

## Public demo deployment

A multi-stage `Dockerfile` at the repo root (Node builds the frontend, Python serves it via the
same single-process pattern as local dev) is deployed to Railway for a shareable public link.

**Real Recovery Mode and Recovery Shield only make sense on the machine whose drives/folders they
read** â€” a public server would only ever see its own filesystem, never a visitor's. The image sets
`RECOVERYLENS_PUBLIC_DEMO=true`, which `app/main.py` uses to:
* not register the Real Recovery (`/api/real/*`) or Recovery Shield (`/api/shield/*`) routers at all
* return 403 from `/api/cases/upload` (free-form storage-image upload) and the restore-to-folder
  endpoint, since both write to the server's own local filesystem
* expose this via `GET /api/config` (`{"public_demo": true}`), which the frontend reads to hide the
  Real Recovery Mode section on New Analysis, the Restore-to-folder button, and the Recovery Shield
  nav link/badge â€” a public visitor only ever sees Demo Mode and read-only analysis pages, exactly
  what still works.

Every Demo Mode scenario, Benchmark Lab, Calibration and all read-only analysis pages (Fragments,
Digital Twin, Relationship Graph, Guardian, Cross-Artifact Intelligence, AI Investigator, Reports)
work identically to local dev. Covered by `tests/test_public_demo.py` (6 tests): config reports
the flag correctly, upload/restore are blocked, the real-recovery and shield routes genuinely don't
exist, and a full demo case still runs end-to-end.

Redeploy after code changes with `railway up -y --ci` from the repo root (needs a git repository â€”
`railway up` only honours `.gitignore` inside one â€” and the Railway CLI logged in). Case data lives
in the container's own ephemeral filesystem and is not persisted across redeploys; that is
intentional for a public demo (`pipeline.ensure_demo()` regenerates the Demo Mode datasets on every
startup) and keeps a redeploy an easy way to reset a shared public instance.

---

## Guided demo (one click)

Click **Guided demo** in the header or on the landing page. It opens the realistic
"Damaged USB drive" case (analysing it first if needed; about 20 s) and walks through 12 steps.
Each step's floating panel says what to **show** and what to **say**:

evidence â†’ recovered files â†’ proof (xref placement) â†’ Digital Twin â†’ possibility + simulator â†’
structure â†’ Safety Guardian â†’ cross-artifact links â†’ AI Investigator â†’ Benchmark Lab â†’
calibration â†’ forensic PDF report.

Every claim in the narration was checked against the case's actual records.

**Forensic PDF report**: Reports â†’ *Forensic PDF report* (`GET /api/cases/{id}/report.pdf`). It covers
evidence hashes, a byte-level reconstruction map per artifact, validator checks, security, possibility,
cross-artifact links, investigator decisions and the audit log. It is rendered from the same records as
the JSON report, and each generation is written to the audit trail with the PDF's SHA-256.

**Engine robustness**: the realistic dataset is tested in four random layouts (`tests/test_realistic.py`).
Placement precision and link accuracy are 100% in every layout, and anything not located is reported
missing. Handled cases:

* PDF xref and `startxref` split across fragments
* ZIP central directory split across fragments
* the scanner merging unrelated clusters onto a fragment (released when CRC-32 or JPEG structure rejects them)
* JPEG chains competing for the same fragment (the strongest evidence wins)
* stored pictures carrying bit-rot (placed by restart-marker continuity and row geometry; the corruption stays flagged)

Gaps that need three or more separate pieces are reported as missing.

## Demo walkthrough (5 minutes)

1. **New analysis â†’ "Corrupted USB image (full scenario)"**. This is a 14 MB synthetic image
   holding 13 deleted files. The files are fragmented into up to 8 pieces, partially
   overwritten, bit-rotted and duplicated. Every demo screen carries a **SIMULATED EVIDENCE** banner.
2. **Progress**: a live audit trail shows each stage: hash, scan, identify, relate,
   reconstruct, validate, prioritize, and a re-hash that proves the evidence is unchanged.
3. **Overview**: KPIs, *Review first* list, challenges, charts, a **storage map** (one cell per
   4 KiB cluster, coloured by the file that used it), and deleted FAT directory entries
   whose long file names are rebuilt (the lost first character is recovered through the LFN checksum).
4. Open **Project_Proposal_v3.docx**:
   * *Reconstruction 68.8%* but *Integrity 85.5*. The full document text is CRC-32 verified; the
     missing 16 KB belong to the embedded image, and one of its clusters was overwritten.
   * The reconstruction map shows every byte range with its source offset.
   * The AI analysis answers why fragments were linked, why this status, why this integrity
     and why this priority.
   * *Further recovery: UNCERTAIN*. Headerless JPEG data exists on the medium, but it cannot be
     verified against the member's CRC.
5. **Relationship graph**: fragments â†’ files, directory-entry metadata nodes, content-similarity
   and temporal links. Rejected alternative joins are shown in red with their scores. Click an
   edge to see its evidence and the model's per-feature contributions.
6. **server_rack.jpg** and the `unknown_fragment_*.bin` orphans: honest **UNRECOVERABLE**
   results. An orphan's preview is rendered with *borrowed* decoding tables and is stamped
   UNVERIFIED.
7. **Priority criteria**: change keywords or weights, and everything is re-ranked with
   itemised reasons (the change is written to the audit trail).
8. **Ground-truth check**: the generator's hidden answer key is compared *after* analysis, giving
   placement precision, recall, link accuracy and how much injected bit-rot was detected. The
   engine never reads that key.
9. **JSON report**: the full machine-readable report (schema from the brief plus provenance,
   integrity factors and data-class labels). You can download it.

### Measured results on the demo datasets (engine vs. hidden ground truth)

| Dataset | Placement precision | Link accuracy | Notes |
|---|---|---|---|
| USB scenario | **100%** | **100%** (26 links) | recall 87.8%; 0/6 flipped bytes detected (they fell in data without checksums) |
| A: JPEG | 100% | 100% | last cluster overwritten â†’ 94.7% of MCU rows; hatched in preview |
| B: PDF | 100% | 100% | overwritten fragment reported as a 4 KB missing range; 8/10 pages intact |
| C: DOCX | 100% | 100% | every XML part CRC-verified; embedded image 64% missing |
| D: SQLite | 100% | 100% | pages numbered via b-tree key ranges; 2/2 corrupted pages detected |
| E: Unrecoverable | n/a | n/a | header plus 88% overwritten â†’ UNRECOVERABLE, ~8% bytes present |

Reproduce with `python -m scripts.evaluate_datasets` (in `backend/`).

---

## Architecture

```
            STORAGE IMAGE  (copied to write-protected store, SHA-256 before and after)
                  â”‚
         DETERMINISTIC SCANNER  â”€â”€ per 4 KiB cluster: entropy, signatures, JPEG marker legality,
                  â”‚                SQLite b-tree page headers, PDF objects, ZIP local headers,
                  â”‚                timestamps, FAT/VFAT directory entries, duplicate hashes
         FRAGMENT EXTRACTION   â”€â”€ runs of same-family clusters; split at headers, footers,
                  â”‚                RST-sequence breaks, PDF object-number restarts, time reversals
     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
 FILE STRUCTURE             METADATA ENGINE
 (xref, central dir,        (FAT LFN names, sizes, times,
  b-tree, restart markers)   EXIF, OOXML core, PDF /Info)
     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
          RELATIONSHIP SCORING  â”€â”€ logistic edge model over explainable features
                  â”‚                (deterministic checks enter as features)
            RECONSTRUCTION      â”€â”€ format assemblers: PDF Â· ZIP/DOCX Â· SQLite Â· JPEG Â· logs/text Â· orphans
                  â”‚
      DETERMINISTIC VALIDATION  â”€â”€ libjpeg (Pillow), zipfile + CRC-32, sqlite3 integrity_check,
                  â”‚                PDF object/xref walk, PNG chunk CRC, MP4 box walk
        INTEGRITY Â· CONFIDENCE  â”€â”€ four separate, itemised scores
                  â”‚
      CLASSIFICATION Â· PRIORITY â”€â”€ configurable, transparent
                  â”‚
             EXPLANATION        â”€â”€ templates over recorded evidence (no LLM)
                  â”‚
         REPORT Â· AUDIT Â· PROVENANCE
```

### What is deterministic and what is AI/ML

| Component | Technique | Why |
|---|---|---|
| Cluster classification, signatures, headers | rules and parsers | technical facts must be reproducible |
| Fragment placement in PDF/ZIP/SQLite | container indexes (xref offsets, central-directory offsets + CRC-32, b-tree key ranges) | verifiable, not probabilistic |
| Validation | real parsers | an LLM cannot decide whether bytes are valid |
| **Fragment relationships** | **logistic-regression edge model** trained on synthetic images with known truth | combines hard checks with soft signals (pixel seams, timestamps, vocabulary) and stays explainable |
| **Corruption hints** | robust-z anomaly scan of JPEG MCU-row seams (advisory only) | statistical, clearly labelled as advisory |
| **Content similarity** | TF-IDF cosine between recovered texts | finds topical links between artifacts |
| Prioritisation | weighted, user-configurable model | transparent by design |
| Explanations | templates over recorded signals | every sentence traces to evidence; no hidden reasoning |

**Edge model**: 9 features (type compatibility, format-sequence continuity, container-index
verification, boundary/pixel-seam continuity, entropy and byte-distribution similarity,
proximity, content similarity, timestamp consistency). It was trained on 14 generated images and
tested on 5 held-out images (disjoint from the demo images), with **test AUC 0.999, precision 0.81,
recall 1.00**. Hand-set prior weights reach AUC 0.93 and 6% accuracy. The model card is shown in
the UI. For forensic precision, a JPEG join is accepted only at â‰¥ 70%. Joins scoring 50â€“70% are
surfaced as "further recovery: YES" leads for an analyst.

---

## Input types and single-file mode

The input type is detected first (`engine/intake.py`) and shown as the case badge:

| Input | Detected by | Mode |
|---|---|---|
| **UPLOADED FILE â€“ PDF/JPEG/DOCX/â€¦** | file signature at offset 0, no partition table or file-system structures | *file validation/repair*: no carving; the file is one contiguous candidate |
| **UPLOADED FILE â€“ TEXT/log** | â‰¥ 85% printable bytes, no binary file signature, not a disk image | *file validation/repair*: no carving; validated as UTF-8/line-structured text |
| **DISK IMAGE** | MBR/GPT partition table, FAT/NTFS/exFAT boot sector, ext superblock, ISO-9660 | carving + reconstruction |
| **DISK IMAGE â€“ E01** | EWF signature | reported as unsupported (convert with `ewfexport`) |
| **RAW STORAGE IMAGE** | none of the above | carving + reconstruction |

**Real bug found and fixed via live testing** (deleting a genuine `.txt` file to a real Windows
Recycle Bin, then recovering it through Real Recovery Mode): plain text, logs, CSV, JSON,
source code and similar formats have no magic-byte signature at all, so `detect()` used to fall
through straight to `RAW STORAGE IMAGE` â†’ carving, which scans for the ~7 known binary signatures,
finds none, and reports **zero candidate files** â€” a genuinely recovered real file would silently
vanish, with no entry anywhere in the UI and no error. Fixed by adding a printable-byte-ratio
fallback (`intake.py: TEXT_PRINTABLE_THRESHOLD = 0.85`) that classifies such content as a normal
single text/log file instead, so it goes through the same validation (`validators.py: validate_text`)
and shows up in Recovered Files like any other input. Covered by three tests in
`IntakeTests` (`tests/test_single_file.py`): a real recovered `.txt` is now `single_file`/`txt`,
an empty buffer still falls back to `raw_image`, and genuine high-entropy binary data is not
misclassified as text.

### PDF validator (`engine/pdfcheck.py`)

Nine checks, each recorded as pass/partial/fail/n-a with its evidence:

| Check | Weight |
|---|---|
| `%PDF-x.y` header | 10 |
| `%%EOF` marker | 5 |
| `startxref` points to an xref table or stream | 5 |
| xref offsets land on real `N G obj` boundaries (surviving entry lines are salvaged if the xref header is overwritten) | 15 |
| trailer `/Root` â†’ intact `/Catalog`, `/Size` â‰¥ max object + 1 | 10 |
| page tree resolves, page count > 0 (catalog located by scanning if the trailer is lost) | 15 |
| **Links**: every indirect reference `N G R` resolves to an intact object | 15 |
| streams: `/Length` matches and FlateDecode decompresses | 10 |
| test render of every page with MuPDF (PyMuPDF) | 15 |

**PDF integrity** is the weighted mean of the scored checks; n/a checks are excluded, and partial checks score their pass ratio. **Status**:

| Status | Condition |
|---|---|
| FULLY_RECOVERED | all checks pass and every page renders |
| MOSTLY_RECOVERED | structure valid, all pages render, â‰¥ 90% of references resolve |
| PARTIALLY_RECOVERED | some pages render |
| FRAGMENT_ONLY | intact objects survive but no page renders |
| UNRECOVERABLE | no intact objects |

Every candidate carries a `status_reason` naming the deciding checks, for example:
*"Fragment only: 4/5 objects intact but no page renders; startxref does not lead to a readable xref; xref offsets invalid for 3/7 objects; â€¦; 2/8 checks pass"*.

**Repair**: when the xref or trailer is broken, a *repaired copy* (`name__recNNNN.repaired.pdf`) is written next to the untouched carved copy.
* The xref is rebuilt by scanning intact objects, and object bodies are copied byte-for-byte.
* If the catalog is lost, a minimal Catalog/Pages pair is synthesized and listed in `repaired_copy.synthesized_objects`.
* Both SHA-256 values go to the audit trail. The evidence is never modified.

### No unexplained blanks
* `recon_confidence_reason`: for example "Contiguous: single-file input, all N bytes presentâ€¦".
* `links_metric` states what Links means for the file: internal references, fragment relationships, or N/A with a reason.
* `coverage_reason`: for example "N/A â€“ no file-system metadata (single-file input)", with a bytes-assigned fallback.

Recovered files are named `<original or embedded name>__rec<NNNN>.<ext>`. The source of the name is the directory entry, the uploaded file name, the PDF `/Title` or DOCX title, or EXIF.

Report schema **1.1** adds `input_type`, `validator_checks[]`, `status_reason`, `recon_confidence_reason`, `links_metric`, `repaired_copy`, `coverage_reason` and `findings`. All 1.0 fields are unchanged.

**Tests**:
* Backend: `python -m unittest discover -s tests` runs 34 tests. The fixtures are built by `tests/fixtures/build_fixtures.py`: a valid PDF, a truncated trailer, a corrupted xref, a corrupted stream, a JPEG, a FAT12 disk image with a deleted file, and the real `corrupted_demo_report.pdf`.
* Frontend: `npm.cmd test` runs Vitest snapshot tests of the Overview for the single-file and disk-image cases.

---

## Scoring model

All four scores are separate. The UI shows every factor.

**Reconstruction %** = located bytes / expected size, where expected size comes from the file's
own structure: `startxref` + trailer (PDF), EOCD position (ZIP), `page_count Ã— page_size`
(SQLite), MCU-row accounting from restart markers (JPEG), or the size of a linked directory
entry. It is `n/a` when no size reference exists (logs, plain text).

**Integrity (0â€“100)** is the sum of:

| Factor | Points |
|---|---|
| Header / signature valid | 10 |
| Terminator / trailer (EOI, %%EOF, EOCD, page count) | 8 (4 if partial or undefined) |
| Parser validation (pass / partial / fail) | 25 / 12 / 0 |
| Checksum / index consistency: CRC-32 ratio, xref offset agreement, valid b-tree pages, unbroken RST sequence | 15 Ã— ratio (5 if the format has none) |
| Expected content present | 30 Ã— reconstruction % |
| Continuity | 12 Ã— max(0, 1 âˆ’ 0.25 Ã— missing ranges) |
| Corrupted ranges | âˆ’4 each (max âˆ’20) |
| Conflicting fragments | âˆ’5 each (max âˆ’10) |
| Orphan (no header) | âˆ’10 |

**Relationship confidence** is the geometric mean of the accepted fragment-link probabilities,
Ã— (1 âˆ’ 0.1 Ã— conflicts). It is shown alongside signature compatibility, sequence compatibility,
structural/index verification, boundary continuity, metadata correlation and conflicting evidence.

**Content confidence** = signature (45) + parser result (40 / 25 / 5) + agreement with the
directory-entry extension (15).

**Status**: FULLY (â‰¥ 99.5%, parser pass, no corruption) Â· MOSTLY (â‰¥ 85% and integrity â‰¥ 70) Â·
PARTIALLY (â‰¥ 40%, or a DOCX whose document text is CRC-verified) Â· FRAGMENT_ONLY Â· UNCERTAIN
(link confidence < 60% or completeness not measurable) Â· UNRECOVERABLE (< 20% or headerless
and undecodable).

**Priority** = Î£ weight Ã— {integrity, reconstruction, link confidence, relevance}, normalised.
Relevance is keyword hits (25 each, max 50) + type preference (20) + recency (15) + related
artifacts (5 each, max 15). The bands are CRITICAL â‰¥ 88, HIGH â‰¥ 72, MEDIUM â‰¥ 52, LOW â‰¥ 35.
*Priority means "review earlier under these criteria", not "proven important".*

---

## Evidence handling and security

* Evidence is copied into `data/evidence/<case>/`, marked read-only, hashed on intake and
  **re-hashed after analysis**. The result is shown in the UI and the report.
* The engine only ever opens evidence with `rb`. Hex views read on demand, read-only.
* Reconstructed outputs are written separately (`data/cases/<case>/reconstructed/`). Exports
  carry `X-RecoveryLens-Missing-Ranges` and SHA-256 headers.
* Uploads require an explicit "authorised to analyse" confirmation. There is no raw-device
  access, and the upload limit is 1 GiB.
* Every result keeps its provenance: source fragments, offsets and hashes, the operations
  applied, validators run, model version and timestamp.
* Five data classes are labelled throughout: **observed**, **derived**, **inferred**,
  **reconstructed**, **unverified**.

### Export integrity (`engine/postproc.py: write_export`)

Every candidate is validated before export and is put in one of three export classes:

| Class | When | Label / button |
|---|---|---|
| `validated_file` | every format check passes | VALIDATED RECOVERED FILE Â· "Export reconstructed file" |
| `partially_validated_file` | the file opens, but some checks fail | PARTIALLY VALIDATED + warning text |
| `forensic_artifact` | format validation fails, or bytes are missing | FORENSIC BYTE ARTIFACT â€“ NOT A VERIFIED RECOVERED FILE Â· "Export forensic byte artifact" |

* Bytes are written in binary mode, read back and re-hashed. The audit trail records
  `reconstruction SHA-256 â€¦ ; export file SHA-256 â€¦ ; BYTE-IDENTICAL`.
* Missing ranges are never silently zero-filled and called recovered. Artifacts with
  placeholders are named `name.FORENSIC-ARTIFACT.ext` and come with a manifest of the
  placeholder ranges. An "observed segments only (ZIP)" export contains only bytes that were
  actually read from the evidence.
* The browser downloads an `ArrayBuffer`, re-hashes it with WebCrypto, and posts the hash to
  `POST /api/cases/{id}/files/{fid}/export-verification`. The audit then shows the client
  re-hash as BYTE-IDENTICAL or HASH MISMATCH.
* PDF previews use the **same bytes** as a Blob URL. When validation fails, the preview reads
  "Preview unavailable: reconstructed bytes failed PDF validation."

### Malware / security analysis (`engine/security.py`)

* **Scanner interface**: `MalwareScanner` (`scan_bytes`, `scan_file`, `get_scan_engine`,
  `get_signature_version`).
  * `ClamAVScanner` is the primary scanner. It uses clamd INSTREAM on `127.0.0.1:3310`, or
    `clamscan`/`clamdscan` on PATH.
  * `DemoScanner` is used **only** for demo datasets and is labelled SIMULATED everywhere.
* **Rules**: `rules/recoverylens.yar` holds the YARA rules. `yara-python` has no build for
  Python 3.14, so a built-in matcher implements the same rules, and the UI labels it as such.
  With `yara-python` installed, the `.yar` file is used directly.
* **Static analysis**: runs on every artifact and on unassigned fragments. It checks entropy,
  magic bytes, embedded PE/ELF headers, archive listings, URLs/IPs, PDF JavaScript/OpenAction,
  and Office macros. Flagged unstructured fragments are promoted to `bin`/`txt` candidates.
* **Statuses**: CLEAN, SUSPICIOUS, MALWARE_DETECTED, SCAN_FAILED, NOT_SCANNED.
  * Without an engine, artifacts are reported NOT_SCANNED, never CLEAN.
  * A heuristic or AI signal alone yields SUSPICIOUS, never MALWARE_DETECTED.
  * CLEAN always carries this note: *"Clean means no detection was returned by the configured
    scanner. It does not constitute proof that the file is completely safe."*
* **MALWARE_DETECTED**: the file is never executed or opened.
  * The file is not deleted.
  * Inline preview is blocked (HTTP 403).
  * Export requires an explicit authorisation (`authorized=true`). The file is served as
    `application/octet-stream` with a `.QUARANTINED` suffix.
  * Every step is written to the audit trail.
* **Report** (schema 1.2): each file has `security_analysis` and `export_validation`.

**Enabling ClamAV (Windows)**: install ClamAV from clamav.net, run `freshclam`, then either
start `clamd` (port 3310) or put `clamscan.exe` on PATH. Restart the API; the scanner is
detected automatically.

**Security demo datasets** (New analysis â†’ Security demos). All three are harmless:

* **SEC1**: clean PDF + JPEG â†’ CLEAN.
* **SEC2**: PDF with an auto-run `/JavaScript` OpenAction, plus an inert PE-header stub that
  contains no executable code â†’ SUSPICIOUS.
* **SEC3**: a text file containing a RecoveryLens test signature â†’ **SIMULATED / TEST MALWARE
  DETECTION**.

The test signature stands in for EICAR because Windows Defender quarantines EICAR files the
moment they are written, which would break the demo and the repository. No real malicious
payload is included. The EICAR rule still exists (the string is assembled at runtime, never
stored contiguously), and a test asserts that no source or bytecode file contains it.

---

## Advanced analysis features (`backend/app/services/`)

These run on persisted analysis records plus read-only reads of the evidence copy. They never
write to evidence, never execute recovered content, and never invent bytes. Old cases work
without re-analysis, because every service computes on demand.

| # | Feature | UI | What it does (and what it refuses to do) |
|---|---|---|---|
| 1 | **Recovery Digital Twin** (`twin.py`) | Reconstruction â†’ Recovery Digital Twin | React Flow graph: storage image â†’ fragments (offsets, hashes) â†’ ordering incl. MISSING/CORRUPTED ranges â†’ reconstruction â†’ validation â†’ security â†’ artifact. Every placement has an explanation assembled from recorded edge evidence. Replay animation and audit timeline. |
| 2 | **Recovery Possibility Map** (`possibility.py`) | Reconstruction â†’ Recovery Possibility | HIGH / MEDIUM / LOW / UNKNOWN from explicit rules. HIGH needs a structural match (e.g. the exact PDF objects the xref expects), not a score. For each region: expected content, known evidence, candidates and conflicts. Includes a storage heatmap (green, yellow, orange, red, gray). |
| 3 | **Fragment DNA** (`fragment_dna.py`) | Analysis â†’ Fragment DNA | Per-fragment fingerprint: SHA-256, entropy and entropy profile, byte histogram, signature/MIME, header/footer, structural markers, compression/encoding, alignment, parser compatibility, metadata, anomalies. Similarity engine (cosine + PCA map) labelled STRONG / POSSIBLE / WEAK / NONE, with evidence. Near-uniform (compressed/encrypted) pairs are capped at WEAK, and similarity never implies same-file. |
| 4 | **Counterfactual Recovery Simulator** (`simulator.py`) | Recovery Possibility â†’ region â†’ Simulate | Splices a candidate into a temporary copy and re-runs the same validators on baseline and simulation, then shows the before/after difference. The integrity change is split into byte coverage and validator evidence, and only validator evidence can make a verdict "IMPROVES". Otherwise the verdict is REJECTED or FILL UNCONFIRMED. Labelled SIMULATION â€” NOT EVIDENCE; nothing is saved. Tests include a positive control (the true fragment is confirmed) and a negative control. |
| 5 | **Cross-Artifact Intelligence** (`cross_artifact.py`, NetworkX) | Intelligence â†’ Cross-Artifact | Relationships between artifacts, detected from: hash duplicates, container membership (hash of DOCX media vs. JPEGs), referenced filenames, shared identifiers, shared metadata, directory metadata, timestamps, similar (non-generated) names, database values, content similarity and storage adjacency. Each has type, evidence, confidence, source and target, and is labelled INFERRED. Filterable graph. |
| 6 | **Recovery Safety Guardian** (`guardian.py` + `engine/security.py`) | Intelligence â†’ Security Guardian | Status wording: NO DETECTION ("NO MALWARE DETECTED BY AVAILABLE SCANNERS" plus the absence caveat), SUSPICIOUS, MALWARE DETECTED or SCAN UNAVAILABLE. Each detection shows name, scanner, rule, evidence, file region, timestamp and scanner status. Checks for polyglots, dangerous types and embedded OLE objects. Flagged artifacts are copied to `cases/<id>/isolation/*.QUARANTINED` (read-only). Includes a "Run security scan" re-scan. |
| 7 | **Confidence Calibration Engine** (`confidence.py`) | File detail â†’ Confidence profile; top bar â†’ Calibration | Six separate measures, each with its evidence: detection, relationship, reconstruction, classification, integrity and possibility. Calibration uses only ground-truth-labelled samples (one case per demo dataset plus Benchmark Lab runs): reliability bins, Brier, ECE, precision/recall, confusion matrix. With fewer than 30 labels it shows "CONFIDENCE CALIBRATION UNAVAILABLE â€” INSUFFICIENT VALIDATION DATA". |
| 8 | **Universal File Structure Explorer** (`structure.py`) | Reconstruction â†’ Structure Explorer | Plugin parsers (`@structure_parser`) for PDF, JPEG, ZIP/DOCX/XLSX, SQLite, MP4 and PNG. Each component shows offset, length, parser verdict, recovered/missing/corrupted state, source fragment and confidence. Other formats show "STRUCTURE PARSER UNAVAILABLE". |
| 9 | **Recovery Benchmark Lab** (`benchmark.py`) | top bar â†’ Benchmark Lab | Nine damage scenarios on clean ground-truth files: deleted ranges, random corruption, fragmentation, missing header, missing footer, reordered fragments, partial truncation, mixed fragments and duplicates. Runs the unmodified engine and measures byte recovery, linking P/R (junction level), classification, reconstruction and integrity-prediction accuracy, and FPR/FNR. Labelled SYNTHETIC TEST DATA. |
| 10 | **AI Recovery Investigator** (`investigator.py`) | Intelligence â†’ AI Investigator | An evidence planner over structured backend records. Each suggestion states ACTION, REASON, EVIDENCE, EXPECTED BENEFIT, RISK and CONFIDENCE, with APPROVE / REJECT / VIEW EVIDENCE buttons. Actions are read-only or simulations. Every decision is logged (audit trail and decision log). An optional LLM narrative runs only with `ANTHROPIC_API_KEY`; otherwise the page says "LLM NOT CONFIGURED" and no narrative is simulated. |

**Pipeline additions:**

* Fragment DNA, recovery-possibility and cross-artifact stages now run after reconstruction and
  appear in the audit trail.
* Flagged artifacts are isolated during the security stage.
* The report is schema 1.3, adding `advanced_analysis`.

**Host notes (this machine):**

* scikit-learn cannot load here: Windows Application Control blocks SciPy's native DLL. NumPy
  implements the same cosine, PCA and calibration maths, and the UI says so. scikit-learn is used
  automatically where it imports.
* `python-magic` crashed the interpreter (segfault) with the available libmagic, so it was removed.
  Signatures come from a built-in magic-number table, labelled as such.

### Measured on SYNTHETIC TEST DATA (Benchmark Lab, 9 scenarios, random seed)

* Mean byte recovery: 74%.
* Linking: precision 97%, recall 66%.
* Reconstruction accuracy: 99.5%.
* False positive rate: 0%.
* Calibration over 123 labelled links: Brier score 0.043, precision 0.97.
* The integrity score is not a well-calibrated probability of byte-exact recovery (ECE 0.34).
  Some files that pass validation still carry silent bit flips in data that has no checksum,
  which is why the export class, not the score, decides "validated".

---

## Recovery Shield (`backend/app/shield.py`, `frontend/src/pages/Shield*.tsx`)

Continuous protection for a **live local folder**, complementary to the forensic engine above (which
reconstructs files from a *static, already-damaged* storage image). Shield instead watches an
ordinary folder over time and answers: *what changed since it was last protected, and can it be put
back?* Menu: top bar â†’ **Recovery Shield**.

* **Protect a folder** (`POST /api/shield/sources`): registers a local path. The path is only ever
  opened read-only; nothing in it is written except an explicit, confirmed restore.
* **Recovery points** (`POST /api/shield/sources/{id}/snapshots`) are **real byte-for-byte copies**
  of every file, streamed in 1 MiB chunks (never loaded whole into memory), each hashed with
  SHA-256, stored under `data/shield_snapshots/<id>/`. **Verify** re-hashes the stored copies against
  their manifest to prove the protected copy itself is intact.
* **Change detection** (`POST /api/shield/sources/{id}/scan`, or continuous monitoring) compares the
  folder against the latest recovery point: **deleted**, **modified**, **new**, **renamed**
  (matched by identical hash+size), and **corrupted** â€” corruption is only claimed when the file's
  real structural validator (the *same* PDF/JPEG/ZIP/DOCX/SQLite validators the recovery engine
  uses, via `intake.detect` + `engine.validators.validate`) rejects the current content; otherwise a
  changed file is reported as plain "modified", never guessed at.
* **Continuous monitoring** (`POST /api/shield/sources/{id}/monitor`) uses real filesystem events
  (`watchdog`, debounced ~1.5 s) with a 5 s fallback poll if the platform's event backend is
  unavailable, so a live deletion is detected without the user clicking anything.
* **Incidents** are raised only for *newly observed* loss (a still-deleted file is never re-alerted
  on every poll), severity HIGH/MEDIUM/LOW by an explicit rule (Â§ `_raise_incident`), and the wording
  is deliberately "suspicious activity detected; possible unauthorized deletion" â€” never "hacker
  detected".
* **Restore** (`POST /api/shield/sources/{id}/restore`) copies selected files from a recovery point
  to a destination: a safe timestamped folder (default), a user-given folder, or â€” only with an
  extra confirmation tick in the UI â€” the original location. Every restored byte is re-hashed and
  reported BYTE-IDENTICAL or MISMATCH. A file with no recovery-point copy is reported
  NOT_RECOVERABLE via Shield (it may still be recoverable from a disk image through the forensic
  engine above, but that is a separate, explicit step, never automatic).
* Every operation is written to a per-source, append-only audit trail (`GET /api/shield/sources/{id}/audit`).

**Deliberately out of scope for this prototype** (see `tests/test_shield.py`, 14 tests, for what
*is* covered): authentication/roles, PostgreSQL, Docker, WebSocket progress streaming, a separate ML
"model center" (the forensic engine's own model card already covers this), a full dark-navy
re-theme, raw block-device acquisition (folders and the existing `.img`/`.dd`/`.raw` support only),
and a notification/global-search layer. These are infrastructure and polish, not functionality; none
of them change what the running prototype can actually do in a demo.

---

## Real Recovery Mode (`backend/app/recyclebin.py`, `app/realrecovery.py`, `app/api_realrecovery.py`)

A second, clearly separated mode on **New analysis**, next to Demo Mode. It never touches demo
data or synthetic images â€” every number it shows (scanned bytes, fragment counts, recovery
percentage) comes from the exact evidence the user provided, and it reuses the **same**
single-file analysis path (`engine/analyze.py`) that an uploaded corrupted file already goes
through, so every existing page (Fragments, Fragment DNA, Digital Twin, Relationship Graph,
Structure Explorer, Recovery Possibility, Security Guardian, Cross-Artifact Intelligence, AI
Investigator, Reports, Audit Log) works on a real-recovery case automatically. There is no second
recovery engine.

**Recycle Bin evidence** (`GET /api/real/drives`, `POST /api/real/recyclebin/scan`): reads the
real `$I<suffix>` / `$R<suffix>` records Windows writes under `<drive>:\$Recycle.Bin\<SID>\`
when a file is deleted to the Recycle Bin â€” genuine forensic technique, the same one
Recuva-class tools use, not a simulation. `$I` is a small index record (original name, path,
size, deletion time); `$R` is the original file's untouched bytes, present until the Recycle Bin
is emptied. Everything is opened `"rb"` only; nothing is ever written back.

* Each scanned item is shown as either **"Recycle Bin Evidence Found"** (the `$R` data is still
  present â€” file, original path, size and deletion time are shown, with a **Recover & analyse**
  button) or **"Data purged"** (the Recycle Bin has already been emptied for that item).
* **Recover & analyse** copies the `$R` bytes byte-for-byte into a temp file and calls the same
  `pipeline.create_case(..., mode="recyclebin", ...)` an upload uses. The resulting case is
  validated, scored and classified exactly like any other single-file input â€” a genuinely valid
  recovered PDF is marked FULLY_RECOVERED; a truncated or non-file byte sequence is honestly
  marked UNRECOVERABLE or PARTIALLY_RECOVERED. Nothing is invented to make a result look better.
* Real bug found and fixed via live testing on an actual Windows Recycle Bin: the version-2 `$I`
  record's character count includes the trailing NUL terminator, which â€” unlike the version-1
  branch â€” was not being stripped, leaving an embedded `\x00` in `original_name` that crashed
  `os.open()` as soon as it was used to build the evidence-store path. Covered by
  `test_version2_index_with_null_terminator_counted_strips_it` in `tests/test_realrecovery.py`.

**"Data purged" â†’ Recover & Analyse** (`realrecovery.carve_purged_item`, `POST
/api/real/recyclebin/scans/{sid}/items/{item_id}/carve`): once the Recycle Bin has actually been
emptied, `$R` is gone â€” there are no bytes left to copy byte-for-byte, so this is deliberately a
*different, weaker* recovery path, not the same one relabelled. Clicking **Recover & Analyse** on
a purged row opens a picker of already-analysed, non-demo disk-image cases (`GET
/api/real/recyclebin/carve-images`) â€” real evidence only; a synthetic demo case can never be
selected. "Search evidence" then looks in that image's own already-computed candidate files
(no new engine, no re-scanning) for one of the same file type (inferred from the deleted item's
extension) with a plausible size:
* same type + within 50% of the declared size + the engine already validated it FULLY/MOSTLY
  RECOVERED â†’ **Recovered**
* same type + a validated-but-imperfect or size-uncertain candidate â†’ **Partially Recoverable**
* no candidate of that type anywhere in the image, or the closest one is too different in size or
  too damaged to mean anything â†’ **Insufficient Evidence** â€” and nothing else is offered
Carving can never recover the original filename (that only ever lived in the now-purged `$I`
record), so a match is always reported as *best-effort by type and size*, explicitly worded as
"identity is not proven", never as confirmed recovery of that specific file. Recovered/Partially
Recoverable results get Preview / Download / Restore, wired to the existing per-case endpoints
(download re-hashes and proves BYTE-IDENTICAL; Restore opens the file's own export section, which
already has the full restore-to-folder flow â€” no logic is duplicated). Verified live: a genuine
FAT12 image containing a real JPEG was uploaded as ordinary evidence; searching it for the actual
`IMG_20241007_053840.jpg` entry purged from this machine's real Recycle Bin correctly matched and
downloaded byte-identically (SHA-256 confirmed), while searching the same image for a purged
`.pdf` correctly reported Insufficient Evidence, since no PDF exists anywhere in that image.
Covered by `CarvePurgedItemTests` (7 tests) in `tests/test_realrecovery.py`.

**Upload a storage image** (existing `POST /api/cases/upload`, relabelled "Upload a storage
image" in Real Recovery Mode): `.img`/`.dd`/`.raw`/`.bin`, or a single corrupted file. This was
already fully real â€” `mode="upload"` never touched demo data â€” so no backend change was needed
here; only the UI now shows filename, size, a **client-side SHA-256** (computed with WebCrypto
before upload, `sha256Hex` in `lib/format.ts`), an evidence ID and read-only status before
analysis starts.

**Restore to folder** (`POST /api/cases/{cid}/files/{fid}/restore`, `realrecovery.restore_file`):
generic â€” works for a real-recovery case exactly like it would for a demo or upload case. Copies
the file's already-produced export bytes (never the original evidence) to
`~/RecoveryLens_Recovered/<case name>/` by default, or a user-chosen folder; re-hashes the copy
and reports BYTE-IDENTICAL or HASH MISMATCH; requires `authorized=true`; never overwrites the
file's original real-world location. Recorded in the case's audit trail. Exposed as a "Restore to
folder" button in `ExportPanel.tsx`, next to the existing Verify/Export buttons.

**Browser limitation, acknowledged rather than worked around**: browser JavaScript cannot read
raw disk sectors. `scripts/acquire_drive.py` is an optional, native, elevated-only helper â€” run
by the user from an Administrator terminal â€” that opens `\\.\<DRIVE>:` read-only, streams it to a
plain `.img` file with a live SHA-256, and refuses the OS boot drive by default (real forensic
practice acquires a boot volume offline, not from inside itself). The resulting `.img` is an
ordinary file uploaded through the existing "Upload a storage image" path; no code treats it
specially.

**Mode labelling**: `report.py`'s `mode_notice` and `pdf_report.py`'s page watermark both
distinguish `demo` / `recyclebin` / everything else (upload, raw image) so a `recyclebin` case's
report explicitly states it came from an actual Recycle Bin record on the user's own machine,
never the generic "uploaded storage image" wording.

**Tests** (`tests/test_realrecovery.py`, 14 tests): `$I` v1/v2 parsing (including the NUL-strip
regression above), purged-item detection, byte-read correctness, an honest empty result when no
`$Recycle.Bin` folder exists, a recovered PDF running through the real engine and validating as
FULLY_RECOVERED with `mode == "recyclebin"` and no demo dataset tag, a purged item refusing to
recover, and restore-to-folder (authorization required, byte-identical copy, evidence store never
touched, default destination folder). A `root`/`_root` parameter on `scan_recycle_bin` /
`find_item` / `start_scan` / `recover_item` lets tests point at a hand-built temp directory
instead of a real drive letter; production callers never pass it.

**Verified live** against this machine's actual Recycle Bin (40 real entries, 19 recoverable / 21
already purged): a genuinely deleted `test.pdf` was detected, recovered into a real case with
real (non-demo) numbers, correctly reported UNRECOVERABLE for a deliberately malformed test file
(honest failure, nothing fabricated), restored byte-identically to `~/RecoveryLens_Recovered/`,
and the original `$I`/`$R` Recycle Bin entry and the evidence-store copy were both confirmed
unchanged afterwards.

---

## API (FastAPI; OpenAPI at `/docs`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/datasets` | demo datasets (all synthetic) |
| POST | `/api/cases` | analyse a demo dataset `{dataset, criteria?}` |
| POST | `/api/cases/upload` | analyse an uploaded image (multipart; `authorized=true` required) |
| GET | `/api/cases`, `/api/cases/{id}` | case list and detail (summary, evaluation, model card) |
| GET | `/api/cases/{id}/audit?after=` | audit trail (incremental) |
| GET | `/api/cases/{id}/files?q&type&status&priority&min_*&sort` | recovered-file table with full-text search |
| GET | `/api/cases/{id}/files/{fid}` | full analysis of one file |
| GET | `/api/cases/{id}/files/{fid}/preview` | preview PNG |
| GET | `/api/cases/{id}/files/{fid}/download?variant=export\|repaired\|segments&inline&authorized` | exact export bytes with SHA-256 / byte-identical / export-class headers |
| POST | `/api/cases/{id}/files/{fid}/export-verification` | record the client-side re-hash in the audit trail |
| GET | `/api/cases/{id}/fragments?page&family&assigned&q` | paginated fragment explorer |
| GET | `/api/cases/{id}/fragments/{frag}/hex` | read-only hex view |
| GET | `/api/cases/{id}/graph` Â· `/diskmap` | relationship graph Â· cluster map |
| GET/PUT | `/api/cases/{id}/criteria` | read or change priority criteria (re-ranks) |
| GET | `/api/cases/{id}/report?download=` | JSON report |
| GET | `/api/model` | edge-model card |
| GET | `/api/cases/{id}/dna?page&family&q` Â· `/dna/map` Â· `/dna/{frag}` | Fragment DNA cards Â· PCA map Â· card + similarity |
| GET | `/api/cases/{id}/twin` Â· `/files/{fid}/twin` | Recovery Digital Twin |
| GET | `/api/cases/{id}/files/{fid}/structure` | Universal File Structure Explorer |
| GET | `/api/cases/{id}/possibility` Â· `/files/{fid}/possibility` | Recovery Possibility Map + heatmap |
| POST | `/api/cases/{id}/files/{fid}/simulate` | counterfactual simulation `{fragment_id, range_index}` |
| GET | `/api/cases/{id}/cross-artifact` | Cross-Artifact Intelligence |
| GET Â· POST | `/api/cases/{id}/guardian` Â· `/guardian/rescan?file_id=` | Safety Guardian overview Â· static re-scan |
| GET | `/api/cases/{id}/files/{fid}/confidence` Â· `/api/calibration` | confidence profile Â· calibration |
| GET Â· POST | `/api/benchmarks/scenarios` Â· `/api/benchmarks` Â· `/api/benchmarks/{bid}` | Benchmark Lab |
| GET Â· POST | `/api/cases/{id}/investigator` Â· `/investigator/plan` Â· `/investigator/{rid}/decision` Â· `/investigator/narrative` | AI Investigator |
| GET | `/api/cases/{id}/files/{fid}/provenance` Â· `/api/cases/{id}/insights` | provenance chain Â· dashboard aggregates |
| GET Â· POST Â· DELETE | `/api/shield/sources` Â· `/sources/{id}` | register/list/remove a protected folder |
| POST Â· GET | `/api/shield/sources/{id}/snapshots` Â· `/snapshots/{sid}` Â· `/snapshots/{sid}/verify` | recovery points |
| POST | `/api/shield/sources/{id}/scan` Â· `/monitor` | on-demand change scan Â· start/stop continuous monitoring |
| GET | `/api/shield/incidents` Â· `/incidents/{id}` Â· `/sources/{id}/audit` | incidents Â· per-source audit trail |
| POST Â· GET | `/api/shield/sources/{id}/restore` Â· `/restores` | restore selected files Â· restore history |
| GET | `/api/shield/overview` | Recovery Shield dashboard aggregates |
| GET | `/api/real/drives` | fixed/removable drives visible to the API process, with Recycle Bin presence |
| POST | `/api/real/recyclebin/scan` `{drive}` | read-only scan of one drive's `$Recycle.Bin` |
| GET | `/api/real/recyclebin/scans` Â· `/scans/{sid}` | scan history Â· one scan's items |
| POST | `/api/real/recyclebin/scans/{sid}/items/{item_id}/recover` | copy that item's real bytes into a new real-recovery case |
| GET | `/api/real/recyclebin/carve-images` | completed, non-demo disk-image cases a purged item can be searched against |
| POST | `/api/real/recyclebin/scans/{sid}/items/{item_id}/carve` `{case_id}` | search that image for a same-type, similar-size candidate for an already-purged item |
| POST | `/api/cases/{cid}/files/{fid}/restore` | copy the file's export bytes to a safe folder (real recovery, demo or upload case alike) |

**Storage**: SQLite (`cases`, `audit`, `fragments`, `files`, `blobs`, `benchmarks`, `investigator`).
The same schema maps 1:1 to PostgreSQL for production.

---

## Project layout

```
backend/
  app/main.py            API              app/pipeline.py   case lifecycle, background jobs
  app/db.py  report.py   persistence, report
  app/engine/
    synth.py             demo image generator (real files, simulated damage, hidden ground truth)
    scanner.py           deterministic cluster scan, fragments, headers, FAT/VFAT parsing
    assemble/            pdf.py zipdoc.py sqlitedb.py jpeg.py text.py orphans.py naming.py base.py
    validators.py        JPEG, PNG, MP4, PDF, ZIP/DOCX/XLSX, SQLite, text (register more with @register)
    model.py             edge model + training fit      scoring.py  priority.py  relations.py
    explain.py           evidence-grounded explanations  evaluate.py  ground-truth comparison
    analyze.py           end-to-end pipeline
    security.py          scanner interface, ClamAV/Demo scanners, rules, static analysis
    postproc.py          export classes, byte-integrity export, unstructured-data sweep
    rules/recoverylens.yar
  app/services/          context fragment_dna structure twin possibility simulator cross_artifact
                         guardian confidence benchmark investigator      app/api_advanced.py  their API
  app/shield.py          Recovery Shield: sources, snapshots, change detection, incidents, restore
  app/api_shield.py      Recovery Shield API
  app/recyclebin.py      Real Recovery Mode: read-only Windows $I/$R Recycle Bin parser
  app/realrecovery.py    orchestrates Recycle Bin recovery into the real engine + restore-to-folder
  app/api_realrecovery.py       Real Recovery Mode API
  scripts/train_model.py        retrain the edge model   scripts/evaluate_datasets.py
  tests/                        115 tests (run: .venv\Scripts\python -m pytest -q tests)
  scripts/make_test_image.py    realistic damaged storage image + ground truth
  scripts/make_corrupted_files.py  corrupted single files (PDF/JPEG/DOCX) + manifest
  scripts/acquire_drive.py      optional, elevated-only: image a real drive to .img for Real Recovery Mode
frontend/  React + TypeScript + Vite + Tailwind + React Flow + Recharts + Framer Motion
  src/pages/ShieldHome.tsx, ShieldSource.tsx     Recovery Shield UI (top bar â†’ Recovery Shield)
  src/pages/NewAnalysis.tsx     Real Recovery Mode (Recycle Bin scan + storage-image upload) / Demo Mode
  src/api-real.ts               Real Recovery Mode API client
```

---

## Honest limitations

* The demo images are synthetic. The files inside them are real and valid (Pillow JPEGs,
  hand-built PDFs, zipfile DOCX, sqlite3 databases), but the fragmentation and damage are
  simulated, and the edge model is trained on the same generator family. Expect lower accuracy
  on real media.
* Assumptions: files start on cluster boundaries, the cluster size is 4 KiB, and SQLite page
  size equals cluster size. Overflow pages, index b-trees, progressive JPEGs and compressed
  PDF streams are not reconstructed yet (they are validated where possible).
* JPEG chaining needs restart markers. Baseline JPEGs without them fall back to
  contiguous-carve behaviour.
* Silent corruption in formats without checksums (PDF text, logs, JPEG scan data) usually
  cannot be detected. The ground-truth panel reports this openly.
* Gap filling searches single contiguous runs only. Multi-piece gaps are reported as missing.
* This is a research prototype, not a replacement for professional forensic software. Use it
  only on storage you are authorised to analyse.

**Next steps**: filesystem parsers (FAT/NTFS/ext4 allocation tables), more validators (MP4
reconstruction, PNG/GIF assembly), learned per-format models, a job queue and PostgreSQL,
and analyst feedback loops that write confirmed or rejected links back into training data.

