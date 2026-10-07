/*
  RecoveryLens static rules. They are used by real YARA (yara-python) when it is installed;
  otherwise app/engine/security.py evaluates the same rule definitions with its built-in
  matcher and labels the result as "built-in static rules" (not YARA).
  A rule match means "matched rule", never "confirmed malware".
*/

rule RL_Test_Malware_Signature
{
    meta:
        description = "RecoveryLens harmless antivirus-test signature (EICAR-equivalent for demos)"
        severity = "test"
    strings:
        $a = "RECOVERYLENS-HARMLESS-AV-TEST-SIGNATURE"
    condition:
        $a
}

rule RL_Embedded_PE_Executable
{
    meta:
        description = "Windows PE executable header (MZ + PE signature) inside data"
        severity = "suspicious"
    strings:
        $mz = "MZ"
        $pe = { 50 45 00 00 }
        $stub = "This program cannot be run in DOS mode"
    condition:
        $mz and ($pe or $stub)
}

rule RL_Embedded_ELF_Executable
{
    meta:
        description = "ELF executable header inside data"
        severity = "suspicious"
    strings:
        $elf = { 7F 45 4C 46 }
    condition:
        $elf
}

rule RL_Script_Downloader_Indicators
{
    meta:
        description = "Script/command strings typical of droppers (encoded PowerShell, WScript, cmd /c)"
        severity = "suspicious"
    strings:
        $ps1 = "powershell -enc" nocase
        $ps2 = "powershell.exe -encodedcommand" nocase
        $ps3 = "FromBase64String" nocase
        $ws = "WScript.Shell" nocase
        $cmd = "cmd.exe /c" nocase
        $dl = "DownloadString(" nocase
    condition:
        any of them
}

rule RL_PDF_Active_Content
{
    meta:
        description = "PDF with auto-run JavaScript or launch actions"
        severity = "suspicious"
    strings:
        $pdf = "%PDF-"
        $js1 = "/JavaScript"
        $js2 = "/JS"
        $oa = "/OpenAction"
        $aa = "/AA"
        $launch = "/Launch"
        $emb = "/EmbeddedFile"
    condition:
        $pdf and (($oa or $aa) and ($js1 or $js2) or $launch or $emb)
}

rule RL_Office_Macro_Container
{
    meta:
        description = "OOXML/OLE container carrying a VBA macro project"
        severity = "suspicious"
    strings:
        $vba = "vbaProject.bin" nocase
        $auto1 = "AutoOpen" nocase
        $auto2 = "Document_Open" nocase
    condition:
        $vba or $auto1 or $auto2
}
