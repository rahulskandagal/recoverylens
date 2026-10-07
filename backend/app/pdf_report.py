"""Printable forensic-style PDF report, rendered from the same data as the JSON report.

Nothing here computes new findings: every statement is copied from the analysis records (JSON report
schema 1.3), so the PDF and the JSON can never disagree. All text from evidence (file names, metadata)
is HTML-escaped before rendering.
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from html import escape
from typing import Any

import pymupdf

STATUS_COLOR = {"FULLY_RECOVERED": "#22a06b", "MOSTLY_RECOVERED": "#1fa6a6", "PARTIALLY_RECOVERED": "#c77d12",
                "FRAGMENT_ONLY": "#e07b39", "UNCERTAIN": "#7a5bc7", "UNRECOVERABLE": "#d9534f"}
EXPORT_SHORT = {"validated_file": "Validated file", "partially_validated_file": "Partially validated",
                "forensic_artifact": "Forensic byte artifact (not verified)"}
CSS = """
* { font-family: sans-serif; }
body { font-size: 9pt; color: #17323a; }
h1 { font-size: 20pt; color: #0f4c5c; margin: 0 0 4pt 0; }
h2 { font-size: 13pt; color: #0f4c5c; border-bottom: 1px solid #1fa6a6; margin: 14pt 0 5pt 0; padding-bottom: 2pt; }
h3 { font-size: 10.5pt; color: #123f4a; margin: 10pt 0 3pt 0; }
p { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; margin: 3pt 0 6pt 0; }
th { background-color: #e4f3f2; color: #0f4c5c; text-align: left; font-size: 8pt; padding: 2pt 3pt; border: 1px solid #c9dcdc; }
td { font-size: 8pt; padding: 2pt 3pt; border: 1px solid #e3d7c0; vertical-align: top; }
.mono { font-family: monospace; font-size: 7.5pt; }
.muted { color: #6b7c83; }
.box { background-color: #fff8ea; border: 1px solid #f4a340; padding: 5pt; margin: 4pt 0; }
.demo { background-color: #fdecec; border: 1px solid #d9534f; padding: 5pt; margin: 4pt 0; color: #8a1f1c; }
.tag { font-weight: bold; }
"""


def _e(v: Any) -> str:
    return escape("—" if v is None or v == "" else str(v))


def _status(s: str) -> str:
    return f'<span class="tag" style="color:{STATUS_COLOR.get(s, "#17323a")}">{_e(s.replace("_", " "))}</span>'


def _pct(v: Any) -> str:
    return "N/A" if v is None else f"{v}%"


def build_html(rep: dict[str, Any], case: dict[str, Any]) -> str:
    md, s, files = rep["report_metadata"], rep["summary"], rep["recovered_files"]
    adv = rep.get("advanced_analysis") or {}
    h: list[str] = [f"<html><head><style>{CSS}</style></head><body>"]
    h.append("<h1>RecoveryLens – Forensic Recovery Report</h1>")
    h.append(f'<p class="muted">Report {_e(md["report_id"])} · generated {_e(md["generated_at"])} · schema {_e(md["schema_version"])} · '
             f'{_e(md["analysis_version"])}</p>')
    notice = md.get("mode_notice")
    h.append(f'<div class="{"demo" if md.get("mode") == "demo" else "box"}"><b>{"DEMO / SYNTHETIC DATA" if md.get("mode") == "demo" else "Scope"}:</b> {_e(notice)}</div>')
    ev = md["evidence"]
    h.append("<h2>1. Evidence</h2><table>")
    for k, v in [("Case", f"{case.get('name')} ({case.get('id')})"), ("Evidence file", ev["name"]), ("Size", f"{ev['size_bytes']:,} bytes"),
                 ("SHA-256", ev["sha256"]), ("Unchanged after analysis", "YES – re-hashed after analysis" if ev["unchanged_after_analysis"] else "NOT VERIFIED"),
                 ("Input type", (s.get("input_type") or {}).get("label")), ("Handling", "Write-protected copy, opened read-only; never modified or executed")]:
        h.append(f'<tr><th style="width:28%">{_e(k)}</th><td class="{"mono" if k == "SHA-256" else ""}">{_e(v)}</td></tr>')
    h.append("</table>")
    h.append("<h2>2. Summary</h2><table><tr>" + "".join(f"<th>{x}</th>" for x in
             ("Scanned", "Fragments", "Candidates", "Fully", "Mostly", "Partial", "Fragment only", "Uncertain", "Unrecoverable")) + "</tr><tr>")
    for v in (f"{s['storage_size_bytes']:,} B", s["potential_fragments_identified"], s["candidate_files_identified"], s["fully_recovered"],
              s.get("mostly_recovered", 0), s["partially_recovered"], s.get("fragment_only", 0), s["uncertain"], s["unrecoverable"]):
        h.append(f"<td>{_e(v)}</td>")
    h.append("</tr></table>")
    cov = s.get("overall_recovery_coverage")
    h.append(f"<p><b>Recovery coverage:</b> {_pct(cov)} – {_e(s.get('coverage_reason'))}</p>")
    h.append("<h3>Key findings</h3><ul>" + "".join(f"<li>{_e(f['text'])}</li>" for f in s.get("findings", [])) + "</ul>")
    h.append("<h2>3. Recovered artifacts</h2><table><tr><th>ID</th><th>File</th><th>Type</th><th>Status</th><th>Integrity</th>"
             "<th>Recon.</th><th>Security</th><th>Export class</th></tr>")
    for f in files:
        h.append(f"<tr><td>{_e(f['file_id'])}</td><td>{_e(f['file_name'])}</td><td>{_e(f['file_type'])}</td><td>{_status(f['recovery_status'])}</td>"
                 f"<td>{_e(f['integrity_score'])}</td><td>{_pct(f['reconstruction_percentage'])}</td>"
                 f"<td>{_e(str(f['security_analysis']['status']).replace('_', ' '))}</td>"
                 f"<td>{_e(EXPORT_SHORT.get(f['export_validation'].get('export_class'), f['export_validation'].get('label')))}</td></tr>")
    h.append("</table>")
    h.append("<h2>4. Artifact detail and provenance</h2>")
    for f in files:
        h.append(f"<h3>{_e(f['file_id'])} · {_e(f['file_name'])}</h3>")
        h.append(f"<p>{_status(f['recovery_status'])} – {_e(f['status_reason'])}</p>")
        h.append(f'<p class="mono">Reconstruction SHA-256 {_e(f["export_validation"].get("reconstruction_sha256"))}</p>')
        prov = f.get("provenance") or {}
        fsha = {r.get("id"): r.get("sha256") for r in prov.get("fragments") or []}
        rows = f.get("reconstruction_map") or []
        if rows:
            h.append("<table><tr><th>Bytes in artifact</th><th>State</th><th>Source fragment</th><th>Source offset in evidence</th>"
                     "<th>Fragment SHA-256</th></tr>")
            for r in rows[:40]:
                st = r["state"].upper()
                col = {"MISSING": "#d9534f", "CORRUPTED": "#e07b39"}.get(st, "#22a06b")
                h.append(f'<tr><td>{r["start"]:,}–{r["end"]:,} ({r["length"]:,} B)</td><td style="color:{col}"><b>{st}</b></td>'
                         f'<td>{_e(r.get("fragment_id"))}</td><td class="mono">{_e(r.get("source_offset"))}</td>'
                         f'<td class="mono">{_e(str(fsha.get(r.get("fragment_id")) or "—")[:32])}</td></tr>')
            h.append("</table>")
        if f.get("missing_ranges"):
            h.append("<p><b>Missing (not located, never synthesized):</b> " +
                     ", ".join(f"{m['start']:,}–{m['end']:,} ({m['length']:,} B)" for m in f["missing_ranges"][:10]) + "</p>")
        if f.get("corrupted_ranges"):
            h.append("<p><b>Corrupted (present but fail validation):</b> " +
                     "; ".join(f"{m['start']:,}–{m['end']:,}: {_e(m.get('reason'))}" for m in f["corrupted_ranges"][:6]) + "</p>")
        ops = prov.get("operations") or []
        if ops:
            h.append("<p><b>Operations:</b></p><ol>" + "".join(f"<li>{_e(o)}</li>" for o in ops[:10]) + "</ol>")
        chk = f.get("validator_checks") or []
        if chk:
            h.append("<table><tr><th>Validator check</th><th>Result</th><th>Evidence</th></tr>" +
                     "".join(f"<tr><td>{_e(c.get('name'))}</td><td>{_e(str(c.get('status')).upper())}</td><td>{_e(str(c.get('detail'))[:180])}</td></tr>"
                             for c in chk[:12]) + "</table>")
    guard = adv.get("security_guardian") or []
    if guard:
        h.append("<h2>5. Security (static analysis only; nothing executed)</h2><table><tr><th>Artifact</th><th>Status</th><th>Detections</th><th>Note</th></tr>")
        for g in guard:
            det = "; ".join(f"{d['name']} ({d['scanner']})" for d in g["detections"][:3]) or "none"
            h.append(f"<tr><td>{_e(g['file_id'])}</td><td>{_e(g['headline'])}</td><td>{_e(det)}</td><td>{_e(g.get('absence_note'))}</td></tr>")
        h.append("</table>")
    pos = adv.get("recovery_possibility") or []
    if pos:
        h.append("<h2>6. Recovery possibility</h2><table><tr><th>Artifact</th><th>Level</th><th>Supporting evidence</th><th>Opposing evidence</th></tr>")
        for p in pos:
            h.append(f"<tr><td>{_e(p['file_name'])}</td><td><b>{_e(p['level'])}</b></td><td>{_e('; '.join(p['supporting']))}</td>"
                     f"<td>{_e('; '.join(p['opposing']))}</td></tr>")
        h.append("</table>")
    rels = sorted(adv.get("cross_artifact_relationships") or [], key=lambda r: -r["confidence"])[:15]
    if rels:
        h.append("<h2>7. Cross-artifact relationships (inferred, not proof)</h2><table><tr><th>Type</th><th>Source</th><th>Target</th><th>Conf.</th><th>Evidence</th></tr>")
        for r in rels:
            h.append(f"<tr><td>{_e(r['type'])}</td><td>{_e(r['source'])}</td><td>{_e(r['target'])}</td><td>{_e(r['confidence'])}%</td><td>{_e(r['evidence'][0] if r['evidence'] else '')}</td></tr>")
        h.append("</table>")
    dec = (adv.get("ai_investigator") or {}).get("decisions") or []
    if dec:
        h.append("<h2>8. Investigator decisions (user-approved actions)</h2><table><tr><th>Time</th><th>Recommendation</th><th>Decision</th><th>Result</th></tr>")
        for d in dec[:25]:
            h.append(f"<tr><td>{_e(d['timestamp'][:19])}</td><td>{_e(d['recommendation'])}</td><td>{_e(d['decision'])}</td><td>{_e(d.get('result'))}</td></tr>")
        h.append("</table>")
    h.append("<h2>9. Audit log</h2><table><tr><th>Time (UTC)</th><th>Stage</th><th>Event</th></tr>")
    log = rep.get("audit_log", [])
    for a in log[:150]:
        h.append(f"<tr><td class='mono'>{_e(a['ts'][11:19])}</td><td>{_e(a['stage'])}</td><td>{_e(a['message'][:220])}</td></tr>")
    h.append("</table>")
    if len(log) > 150:
        h.append(f'<p class="muted">{len(log) - 150} further audit entries are in the JSON report.</p>')
    h.append("<h2>10. Interpretation</h2><ul>")
    for k, v in (adv.get("data_class_legend") or {}).items():
        h.append(f"<li><b>{_e(k)}</b>: {_e(v)}</li>")
    h.append(f"<li>{_e(adv.get('confidence_note'))}</li>")
    for r in rep.get("analysis_insights", {}).get("recommendations", []):
        h.append(f"<li>{_e(r)}</li>")
    h.append("</ul></body></html>")
    return "".join(h)


def render_pdf(rep: dict[str, Any], case: dict[str, Any]) -> bytes:
    story = pymupdf.Story(html=build_html(rep, case))
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    page = pymupdf.paper_rect("a4")
    where = page + (40, 40, -40, -50)
    more = 1
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for i, p in enumerate(doc):
        p.insert_text((40, page.height - 25), f"RecoveryLens forensic report · {case.get('id')} · {stamp} · page {i + 1} of {doc.page_count}",
                      fontsize=7, color=(0.42, 0.49, 0.51))
        if case.get("mode") == "demo":
            p.insert_text((page.width - 160, 28), "DEMO / SYNTHETIC DATA", fontsize=8, color=(0.85, 0.33, 0.31))
        else:
            p.insert_text((page.width - 170, 28), "REAL RECOVERY MODE", fontsize=8, color=(0.13, 0.63, 0.42))
    doc.set_metadata({"title": f"RecoveryLens report {case.get('id')}", "author": "RecoveryLens", "creator": "RecoveryLens"})
    out = doc.tobytes(deflate=True)
    doc.close()
    return out


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
