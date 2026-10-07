"""Static security analysis of recovered artifacts. Recovered bytes are NEVER executed or opened.

Pipeline:  bytes -> type identification -> SHA-256 -> antivirus engine (ClamAV) -> rule scan
           (YARA when installed, else built-in matcher over the same rules) -> container/archive
           inspection -> suspicious-characteristic detection -> classification -> explanation

Engines
  ClamAVScanner  real antivirus engine: clamd (TCP INSTREAM) or clamdscan/clamscan CLI.
                 The artifact is streamed/written as data; it is never executed.
  DemoScanner    SIMULATED engine used ONLY in labelled DEMO MODE when ClamAV is absent. It
                 recognises a harmless test signature and is never presented as antivirus.

Status: CLEAN | SUSPICIOUS | MALWARE_DETECTED | SCAN_FAILED | NOT_SCANNED
"""
from __future__ import annotations

import io
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .common import sha256

RULES_FILE = Path(__file__).resolve().parent / "rules" / "recoverylens.yar"
TEST_SIGNATURE = b"RECOVERYLENS-HARMLESS-AV-TEST-SIGNATURE"
# The EICAR string is assembled at runtime (a join call is not constant-folded), so neither this
# source file nor its cached .pyc contains the contiguous test string that desktop antivirus flags.
_EICAR = b"".join([b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$", b"EICAR-STANDARD-ANTIVIRUS", b"-TEST-FILE!$H+H*"])

CLEAN_NOTE = ("Clean means no detection was returned by the configured scanner. "
              "It does not constitute proof that the file is completely safe.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------
# antivirus engines
# ------------------------------------------------------------------------------------------

@dataclass
class EngineResult:
    engine: str
    version: str | None
    status: str  # CLEAN | MALWARE_DETECTED | SCAN_FAILED
    detections: list[str] = field(default_factory=list)
    error: str | None = None
    simulated: bool = False
    is_test: bool = False


class MalwareScanner(ABC):
    @abstractmethod
    def scan_bytes(self, data: bytes) -> EngineResult: ...

    def scan_file(self, path: str) -> EngineResult:
        with open(path, "rb") as fh:  # read as data only
            return self.scan_bytes(fh.read())

    @abstractmethod
    def get_scan_engine(self) -> str: ...

    @abstractmethod
    def get_signature_version(self) -> str | None: ...


class ClamAVScanner(MalwareScanner):
    """ClamAV via clamd (preferred) or the clamdscan/clamscan command line."""

    def __init__(self) -> None:
        self.host = os.environ.get("CLAMD_HOST", "127.0.0.1")
        self.port = int(os.environ.get("CLAMD_PORT", "3310"))
        self.cli = shutil.which("clamdscan") or shutil.which("clamscan")
        self._version: str | None = None

    def _clamd(self, cmd: bytes, payload: bytes | None = None, timeout: float = 20) -> bytes:
        with socket.create_connection((self.host, self.port), timeout=timeout) as s:
            s.sendall(b"z" + cmd + b"\0")
            if payload is not None:
                for i in range(0, len(payload), 1 << 16):
                    chunk = payload[i:i + (1 << 16)]
                    s.sendall(struct.pack(">I", len(chunk)) + chunk)
                s.sendall(struct.pack(">I", 0))
            out = b""
            while True:
                b = s.recv(4096)
                if not b:
                    break
                out += b
            return out.rstrip(b"\0\n")

    def available(self) -> bool:
        try:
            return self._clamd(b"PING", timeout=1.5) == b"PONG"
        except OSError:
            return bool(self.cli)

    def get_scan_engine(self) -> str:
        return "ClamAV"

    def get_signature_version(self) -> str | None:
        if self._version is None:
            try:
                self._version = self._clamd(b"VERSION", timeout=2).decode(errors="replace")
            except OSError:
                if self.cli:
                    try:
                        self._version = subprocess.run([self.cli, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
                    except Exception as e:  # pragma: no cover
                        self._version = f"unknown ({e})"
        return self._version

    def scan_bytes(self, data: bytes) -> EngineResult:
        ver = self.get_signature_version()
        try:
            reply = self._clamd(b"INSTREAM", data).decode(errors="replace")
            return self._parse(reply, ver)
        except OSError:
            pass
        if not self.cli:
            return EngineResult("ClamAV", ver, "SCAN_FAILED", error="clamd not reachable and no clamscan/clamdscan binary found")
        try:
            with tempfile.TemporaryDirectory(prefix="rl_scan_") as td:
                p = Path(td) / "artifact.bin"  # neutral name/extension: never opened by any application
                p.write_bytes(data)
                r = subprocess.run([self.cli, "--no-summary", str(p)], capture_output=True, text=True, timeout=300)
            if r.returncode in (0, 1):
                return self._parse(r.stdout, ver)
            return EngineResult("ClamAV", ver, "SCAN_FAILED", error=(r.stderr or r.stdout).strip()[:300])
        except Exception as e:
            return EngineResult("ClamAV", ver, "SCAN_FAILED", error=f"{type(e).__name__}: {e}")

    @staticmethod
    def _parse(reply: str, ver: str | None) -> EngineResult:
        found = re.findall(r":\s*(\S+)\s+FOUND", reply)
        if found:
            return EngineResult("ClamAV", ver, "MALWARE_DETECTED", found, is_test=any("Eicar" in f or "Test" in f for f in found))
        if "ERROR" in reply:
            return EngineResult("ClamAV", ver, "SCAN_FAILED", error=reply.strip()[:300])
        return EngineResult("ClamAV", ver, "CLEAN")


class DemoScanner(MalwareScanner):
    """SIMULATED engine for DEMO MODE only. Recognises harmless test signatures; it is NOT antivirus."""

    def get_scan_engine(self) -> str:
        return "DemoScanner (SIMULATED – not an antivirus engine)"

    def get_signature_version(self) -> str | None:
        return "demo-1 (test signatures only)"

    def scan_bytes(self, data: bytes) -> EngineResult:
        hits = []
        if TEST_SIGNATURE in data:
            hits.append("RecoveryLens.Test.Signature (SIMULATED TEST DETECTION)")
        if _EICAR in data:
            hits.append("EICAR-Test-File (SIMULATED TEST DETECTION)")
        return EngineResult(self.get_scan_engine(), self.get_signature_version(),
                            "MALWARE_DETECTED" if hits else "CLEAN", hits, simulated=True, is_test=bool(hits))


_clam: ClamAVScanner | None = None


def get_scanner(mode: str) -> MalwareScanner | None:
    """ClamAV when available; the simulated DemoScanner only for demo cases; otherwise none."""
    global _clam
    if _clam is None:
        _clam = ClamAVScanner()
    if _clam.available():
        return _clam
    if mode == "demo":
        return DemoScanner()
    return None


# ------------------------------------------------------------------------------------------
# rules: real YARA when installed, else a built-in matcher over the same definitions
# ------------------------------------------------------------------------------------------

def _s(text: str) -> tuple[bytes, bool]:
    return text.encode(), False


BUILTIN_RULES: list[dict[str, Any]] = [
    {"name": "RL_Test_Malware_Signature", "severity": "test",
     "description": "RecoveryLens harmless antivirus-test signature (EICAR-equivalent for demos)",
     "strings": {"a": (TEST_SIGNATURE, False)}, "cond": lambda m: "a" in m},
    {"name": "RL_EICAR_Test_File", "severity": "test", "description": "EICAR standard antivirus test string",
     "strings": {"e": (_EICAR, False)}, "cond": lambda m: "e" in m},
    {"name": "RL_Embedded_PE_Executable", "severity": "suspicious",
     "description": "Windows PE executable header (MZ + PE signature) inside data",
     "strings": {"mz": (b"MZ", False), "pe": (b"PE\x00\x00", False), "stub": (b"This program cannot be run in DOS mode", False)},
     "cond": lambda m: "mz" in m and ("pe" in m or "stub" in m)},
    {"name": "RL_Embedded_ELF_Executable", "severity": "suspicious", "description": "ELF executable header inside data",
     "strings": {"elf": (b"\x7fELF", False)}, "cond": lambda m: "elf" in m},
    {"name": "RL_Script_Downloader_Indicators", "severity": "suspicious",
     "description": "Script/command strings typical of droppers (encoded PowerShell, WScript, cmd /c)",
     "strings": {k: (v.encode(), True) for k, v in {"ps1": "powershell -enc", "ps2": "powershell.exe -encodedcommand",
                                                    "ps3": "frombase64string", "ws": "wscript.shell", "cmd": "cmd.exe /c",
                                                    "dl": "downloadstring("}.items()},
     "cond": lambda m: bool(m)},
    {"name": "RL_PDF_Active_Content", "severity": "suspicious", "description": "PDF with auto-run JavaScript or launch actions",
     "strings": {"pdf": (b"%PDF-", False), "js1": (b"/JavaScript", False), "js2": (b"/JS", False), "oa": (b"/OpenAction", False),
                 "aa": (b"/AA", False), "launch": (b"/Launch", False), "emb": (b"/EmbeddedFile", False)},
     "cond": lambda m: "pdf" in m and ((("oa" in m or "aa" in m) and ("js1" in m or "js2" in m)) or "launch" in m or "emb" in m)},
    {"name": "RL_Office_Macro_Container", "severity": "suspicious", "description": "OOXML/OLE container carrying a VBA macro project",
     "strings": {"vba": (b"vbaproject.bin", True), "a1": (b"autoopen", True), "a2": (b"document_open", True)},
     "cond": lambda m: bool(m)},
]

try:  # optional real YARA
    import yara  # type: ignore
    _YARA = yara.compile(filepath=str(RULES_FILE))
    YARA_ENGINE = f"YARA {getattr(yara, '__version__', '')}".strip()
except Exception:
    _YARA = None
    YARA_ENGINE = "built-in static rules (YARA not installed; same rule set as rules/recoverylens.yar)"

_SEVERITY = {r["name"]: r["severity"] for r in BUILTIN_RULES}
_DESC = {r["name"]: r["description"] for r in BUILTIN_RULES}


def rule_scan(data: bytes) -> list[dict[str, Any]]:
    out = []
    if _YARA is not None:
        for m in _YARA.match(data=data):
            out.append({"rule": m.rule, "description": m.meta.get("description", ""), "severity": m.meta.get("severity", "suspicious"),
                        "offsets": sorted({s.instances[0].offset for s in m.strings})[:5] if m.strings else []})
        return out
    low = None
    for r in BUILTIN_RULES:
        hits: dict[str, int] = {}
        for sid, (pat, nocase) in r["strings"].items():
            if nocase:
                low = low if low is not None else data.lower()
                i = low.find(pat)
            else:
                i = data.find(pat)
            if i != -1:
                hits[sid] = i
        if hits and r["cond"](hits):
            out.append({"rule": r["name"], "description": r["description"], "severity": r["severity"], "offsets": sorted(hits.values())[:5]})
    return out


# ------------------------------------------------------------------------------------------
# static characteristics
# ------------------------------------------------------------------------------------------

def _entropy(b: bytes) -> float:
    if not b:
        return 0.0
    c = np.bincount(np.frombuffer(b, dtype=np.uint8), minlength=256).astype(float)
    p = c[c > 0] / len(b)
    return float(-(p * np.log2(p)).sum())


def _magic(data: bytes) -> str:
    table = [(b"%PDF-", "PDF document"), (b"\xff\xd8\xff", "JPEG image"), (b"\x89PNG", "PNG image"), (b"PK\x03\x04", "ZIP container"),
             (b"SQLite format 3", "SQLite database"), (b"MZ", "Windows executable (MZ)"), (b"\x7fELF", "ELF executable"),
             (b"\xcf\xfa\xed\xfe", "Mach-O executable"), (b"#!", "script with shebang"), (b"\xd0\xcf\x11\xe0", "OLE2 compound document")]
    for sig, name in table:
        if data.startswith(sig):
            return name
    return "unknown binary" if _entropy(data[:65536]) > 5 else "unknown / text-like data"


def embedded_executables(data: bytes, limit: int = 8) -> list[dict[str, Any]]:
    found = []
    for m in re.finditer(rb"MZ", data):
        o = m.start()
        if o + 0x40 > len(data):
            break
        lfanew = struct.unpack("<I", data[o + 0x3C:o + 0x40])[0]
        if 0x40 <= lfanew < 0x1000 and data[o + lfanew:o + lfanew + 4] == b"PE\x00\x00":
            found.append({"offset": o, "format": "PE", "detail": f"MZ header with PE signature at +0x{lfanew:X}"})
            if len(found) >= limit:
                break
    for m in re.finditer(rb"\x7fELF[\x01\x02][\x01\x02]\x01", data):
        found.append({"offset": m.start(), "format": "ELF", "detail": "ELF identification header"})
        if len(found) >= limit:
            break
    return found


RISKY_EXT = (".exe", ".dll", ".scr", ".js", ".jse", ".vbs", ".vbe", ".bat", ".cmd", ".ps1", ".hta", ".lnk", ".jar", ".msi")


def inspect_archive(data: bytes) -> dict[str, Any] | None:
    if not data.startswith(b"PK\x03\x04"):
        return None
    info: dict[str, Any] = {"members": 0, "risky_members": [], "nested_archives": [], "encrypted_members": 0, "macro_project": False}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        for zi in z.infolist():  # metadata only; members are never extracted to disk or executed
            info["members"] += 1
            n = zi.filename.lower()
            if n.endswith(RISKY_EXT):
                info["risky_members"].append(zi.filename)
            if n.endswith((".zip", ".rar", ".7z", ".cab", ".iso")):
                info["nested_archives"].append(zi.filename)
            if zi.flag_bits & 1:
                info["encrypted_members"] += 1
            if n.endswith("vbaproject.bin"):
                info["macro_project"] = True
    except Exception as e:
        info["error"] = f"central directory unreadable ({type(e).__name__}); inspected by signature only"
    return info


def polyglot_indicators(data: bytes) -> list[dict[str, Any]]:
    """A second, independently parseable format hidden in the same bytes (e.g. JPEG+ZIP, EXE+PDF)."""
    out = []
    head = _magic(data)
    pdf_at = data.find(b"%PDF-", 1, 1024)
    if pdf_at > 0 and not data.startswith(b"%PDF-"):
        out.append({"offset": pdf_at, "detail": f"PDF header at +{pdf_at} inside {head} (readers accept a header within the first 1 KiB)"})
    eocd = data.rfind(b"PK\x05\x06")
    if eocd != -1 and not data.startswith(b"PK\x03\x04") and len(data) - eocd <= 22 + 0xFFFF:
        cd_off = struct.unpack("<I", data[eocd + 16:eocd + 20])[0] if eocd + 20 <= len(data) else None
        if cd_off is not None and data[cd_off:cd_off + 4] == b"PK\x01\x02" or data.find(b"PK\x01\x02") != -1:
            out.append({"offset": eocd, "detail": f"ZIP end-of-central-directory appended to {head}: the bytes also open as an archive"})
    if head.startswith(("Windows executable", "ELF")) and (data.find(b"%PDF-") != -1 or data.find(b"PK\x05\x06") != -1):
        out.append({"offset": 0, "detail": f"{head} also carries a document/archive structure"})
    return out


DANGEROUS_MAGIC = ("Windows executable", "ELF executable", "Mach-O executable", "script with shebang")


def static_profile(data: bytes, ftype: str) -> dict[str, Any]:
    ent = _entropy(data)
    windows = [_entropy(data[i:i + 4096]) for i in range(0, min(len(data), 1 << 22), 4096)] or [0.0]
    urls = re.findall(rb"https?://[\x21-\x7e]{4,80}", data)[:10]
    ips = re.findall(rb"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", data)[:10]
    exe = embedded_executables(data)
    arch = inspect_archive(data)
    magic = _magic(data)
    indicators: list[dict[str, str]] = []
    if exe:
        indicators.append({"severity": "suspicious", "text": f"{len(exe)} embedded executable header(s): " + ", ".join(f"{e['format']} @0x{e['offset']:X}" for e in exe[:3])})
    if arch:
        if arch["risky_members"]:
            indicators.append({"severity": "suspicious", "text": f"archive contains executable/script members: {', '.join(arch['risky_members'][:4])}"})
        if arch["macro_project"]:
            indicators.append({"severity": "suspicious", "text": "Office container carries a VBA macro project"})
        if arch["encrypted_members"]:
            indicators.append({"severity": "info", "text": f"{arch['encrypted_members']} encrypted archive member(s) could not be inspected"})
        if arch["nested_archives"]:
            indicators.append({"severity": "info", "text": f"nested archive(s): {', '.join(arch['nested_archives'][:3])}"})
    known_compressed = ftype in ("jpeg", "png", "zip", "docx", "xlsx", "mp4", "gif")
    if max(windows) > 7.6 and not known_compressed and magic.startswith("unknown"):
        indicators.append({"severity": "info", "text": f"high-entropy unidentified data (max window {max(windows):.2f} bits/byte): possibly packed or encrypted"})
    if urls:
        indicators.append({"severity": "info", "text": f"{len(urls)} embedded URL(s), e.g. {urls[0][:60].decode('latin-1')}"})
    poly = polyglot_indicators(data)
    for p in poly:
        indicators.append({"severity": "suspicious", "text": f"polyglot file: {p['detail']}", "offset": p["offset"]})
    if magic.startswith(DANGEROUS_MAGIC) and not exe:
        indicators.append({"severity": "suspicious" if magic != "script with shebang" else "info",
                           "text": f"dangerous file type: {magic}", "offset": 0})
    embedded_objects = []
    if arch and not arch.get("error"):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            embedded_objects = [zi.filename for zi in z.infolist() if "/embeddings/" in zi.filename.lower() or "oleobject" in zi.filename.lower()
                                or zi.filename.lower().endswith((".bin",)) and "vbaproject" not in zi.filename.lower()]
        except Exception:
            pass
    if embedded_objects:
        indicators.append({"severity": "suspicious", "text": f"embedded OLE/object part(s): {', '.join(embedded_objects[:3])}"})
    return {"entropy": round(ent, 3), "max_window_entropy": round(max(windows), 3), "identified_as": magic,
            "embedded_executables": exe, "archive": arch, "urls": [u.decode("latin-1") for u in urls],
            "ipv4": [i.decode() for i in ips], "indicators": indicators, "polyglot": poly, "embedded_objects": embedded_objects}


# ------------------------------------------------------------------------------------------
# orchestration
# ------------------------------------------------------------------------------------------

def scan_artifact(data: bytes, ftype: str, mode: str, scanner: MalwareScanner | None = None, use_engine: bool = True) -> dict[str, Any]:
    ts = _now()
    h = sha256(data)
    try:
        prof = static_profile(data, ftype)
        rules = rule_scan(data)
    except Exception as e:  # static analysis failure is reported, never treated as clean
        return {"status": "SCAN_FAILED", "label": "SECURITY SCAN FAILED", "sha256": h, "scanner": None, "scanner_version": None,
                "simulated": False, "detections": [], "yara_matches": [], "yara_engine": YARA_ENGINE, "static": {},
                "scan_timestamp": ts, "error": f"static analysis error: {e}", "executed": False,
                "explanation": f"SECURITY SCAN FAILED: static analysis raised {type(e).__name__}. The artifact is treated as unverified."}
    eng: EngineResult | None = None
    if use_engine:
        sc = scanner if scanner is not None else get_scanner(mode)
        if sc is not None:
            try:
                eng = sc.scan_bytes(data)
            except Exception as e:
                eng = EngineResult(sc.get_scan_engine(), sc.get_signature_version(), "SCAN_FAILED", error=f"{type(e).__name__}: {e}")
    suspicious_rules = [r for r in rules if r["severity"] in ("suspicious", "test")]
    susp_static = [i for i in prof["indicators"] if i["severity"] == "suspicious"]
    if eng and eng.status == "MALWARE_DETECTED":
        status = "MALWARE_DETECTED"
    elif suspicious_rules or susp_static:
        status = "SUSPICIOUS"
    elif eng and eng.status == "SCAN_FAILED":
        status = "SCAN_FAILED"
    elif eng is None:
        status = "NOT_SCANNED"
    else:
        status = "CLEAN"
    is_test = bool(eng and eng.is_test)
    label = {"MALWARE_DETECTED": "MALWARE DETECTED" + (" (TEST SIGNATURE)" if is_test else ""), "SUSPICIOUS": "SUSPICIOUS",
             "SCAN_FAILED": "SECURITY SCAN FAILED", "NOT_SCANNED": "NOT SCANNED", "CLEAN": "CLEAN"}[status]
    if status == "MALWARE_DETECTED":
        expl = ("Malicious content detected. The recovered artifact has NOT been executed. "
                f"{eng.engine} reported: {', '.join(eng.detections)}."
                + (" This is a SIMULATED / TEST MALWARE DETECTION of a harmless test signature." if is_test or eng.simulated else ""))
    elif status == "SUSPICIOUS":
        bits = [f"matched rule {r['rule']} ({r['description']})" for r in suspicious_rules[:3]] + [i["text"] for i in susp_static[:3]]
        expl = ("Suspicious characteristics found: " + "; ".join(bits) + ". A rule match is an indicator, not a confirmed infection"
                + (f"; {eng.engine} returned no detection." if eng and eng.status == "CLEAN" else "."))
    elif status == "SCAN_FAILED":
        expl = f"SECURITY SCAN FAILED. Reason: {eng.error if eng else 'unknown'}. Scan failure is not treated as clean."
    elif status == "NOT_SCANNED":
        expl = ("NOT SCANNED by an antivirus engine: ClamAV is not available on this server. Static rules and characteristics found "
                "no indicators, but that is not an antivirus result.")
    else:
        expl = f"CLEAN ({eng.engine}). {CLEAN_NOTE}"
    return {
        "status": status, "label": label, "sha256": h,
        "scanner": eng.engine if eng else None, "scanner_version": eng.version if eng else None,
        "simulated": bool(eng and eng.simulated), "is_test": is_test,
        "detections": eng.detections if eng else [], "engine_error": eng.error if eng else None,
        "yara_matches": rules, "yara_engine": YARA_ENGINE, "static": prof,
        "scan_timestamp": ts, "executed": False, "explanation": expl,
        "clean_note": CLEAN_NOTE if status == "CLEAN" else None,
    }
