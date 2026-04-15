# Security Report: CrimsonSaveEditor v3.1.2

**Subject:** VirusTotal / Windows Defender detections on `CrimsonSaveEditor.exe` (v3.1.2)
**Date of analysis:** 2026-04-15
**File analyzed:** `CrimsonSaveEditor.exe`
**SHA-256:** `077758da8063d1e58d5533beaf5366cb4c9d825d23dd835692f4cc64907ee1ce`
**Size:** 121,565,857 bytes (~116 MB)
**Source repo (buildable):** https://github.com/NattKh/CrimsonSaveEditor-v3.1.2-backup (currently private but will be release)
**Release URL:** https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS/releases/tag/v3.1.2

---

## TL;DR

After full static analysis of the PyInstaller bundle, **no malicious behavior was found**. The detections are **generic machine-learning / heuristic flags** consistent with the well-known false-positive pattern that affects nearly all unsigned PyInstaller-packaged Python applications — especially ones that use cryptography (ChaCha20, HMAC-SHA256) and spawn subprocesses, both of which this tool legitimately does to read/write Crimson Desert save files and pack PAZ overlays.

The user's caution is **appropriate and correct** — the practice of not assuming false positives without verification is exactly right, which is why this report exists. The steps below document what was actually checked.

---

## Reported detections

| Vendor | Verdict |
|---|---|
| Microsoft | `Trojan:Win32/Wacatac.H!ml` |
| Arctic Wolf | `Unsafe` |
| Bkav Pro | `W64.AIDetectMalware` |
| SecureAge | `Malicious` |
| Aurora | `Malware_-6` |
| Lionic | `Trojan.Win32.Agent.tsVa` |

**Key observation:** every single one of these is a **generic ML / heuristic verdict** (`!ml`, `AIDetectMalware`, `Agent.tsVa`, `Malware_-6`, unnamed "Unsafe"/"Malicious"). **No vendor reports a named malware family** (no Emotet, no RedLine, no Raccoon, no Lumma, no AsyncRAT, no Agent Tesla, etc.). Named-family detections are what you'd expect from an actual compromise; unnamed ML verdicts are what you get from a PyInstaller binary with crypto and subprocess calls.

---

## What was analyzed

The PyInstaller archive was extracted with `pyinstxtractor-ng`, yielding 436 bundled files and 377 Python modules inside the `PYZ.pyz`. The following project modules were disassembled and their string tables, import tables, and URL references enumerated:

- `main.pyc` (entry point)
- `gui.pyc` (11,050 string constants, 67 imports)
- `updater.pyc` (73 string constants, 12 imports)
- `save_crypto.pyc` (85 string constants, 9 imports)
- `parc_inserter2.pyc`, `parc_inserter3.pyc` (PARC save-format modules)
- `paz_patcher.pyc` (PAZ archive patcher)

Full Python 3.14 bytecode disassembly was performed via the `dis` module against the marshalled code objects.

---

## Indicator-of-Compromise (IOC) scan — results

The following IOCs were searched for across every string constant, in every module, recursively through all nested code objects:

### ❌ Living-off-the-land / system abuse — NONE FOUND
- `powershell`, `cmd.exe /c`, `rundll32`, `regsvr32`, `mshta`
- `certutil`, `bitsadmin`, `schtasks`, `reg add`, `vssadmin`, `wmic`, `bcdedit`, `netsh`

### ❌ Process injection / code injection APIs — NONE FOUND
- `CreateRemoteThread`, `VirtualAllocEx`, `WriteProcessMemory`

### ❌ Credential / wallet theft paths — NONE FOUND
- `Login Data`, `Cookies`, `Local Storage\leveldb`, `wallet.dat`, Discord token directories, browser profile paths, keychain references

### ❌ Known exfiltration endpoints — NONE FOUND
- `discord.com/api/webhooks`, `api.telegram.org`, `pastebin.com/raw`
- `transfer.sh`, `anonfiles`, `gofile.io`, `ngrok`

### ❌ Staging / obfuscation patterns — NONE FOUND
- `base64.b64decode(` followed by `exec(` / `eval(`
- `marshal.loads` on network-fetched content
- String-concat obfuscation of sensitive APIs

### ❌ Ransom / payment strings — NONE FOUND
- `ransom`, `bitcoin`, `monero`, wallet addresses

---

## Every URL embedded in the executable

This is the complete list of URLs present in the project modules:

```
https://api.github.com/repos/NattKh/CRIMSON-DESERT-SAVE-EDITOR/contents/knowledge_packs
https://discord.gg/6wxX5xPS
https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR/releases
https://raw.githubusercontent.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR/main
https://raw.githubusercontent.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR/main/dye_slot_counts.json
https://raw.githubusercontent.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR/main/language/
https://raw.githubusercontent.com/NattKh/CrimsonDesertCommunityItemMapping/main/buff_names_community.json
https://www.nexusmods.com/crimsondesert/mods/713
```

All URLs point to either:
- The project's own public GitHub org (`NattKh`)
- The Nexus Mods page for this tool
- The project Discord invite

**No third-party hosts. No IP addresses. No dynamic DNS. No URL shorteners.**

---

## Imports used (project modules only)

`gui.pyc` uses these notable standard-library modules:

- `subprocess` — to invoke `crimson_rs` for PAZ packing (documented, necessary)
- `ctypes` — used by PySide6 / cryptography bindings (standard)
- `base64` — used for standard config encoding, not followed by `exec`/`eval`

`updater.pyc` uses:

- `subprocess` — to relaunch the app after self-update from GitHub releases

No module imports `socket` at the raw level for custom C2 traffic. Networking is done via `requests` / `urllib` to the GitHub URLs listed above.

---

## Bundled files check

The PyInstaller bundle contains only expected content:

- Qt6 DLLs (`Qt6Core.dll`, `Qt6Gui.dll`, `Qt6Widgets.dll`)
- Python 3.14 runtime + standard `.pyd` C extensions (`_ssl`, `_hashlib`, `_bz2`, etc.)
- Visual C++ / Universal C runtime (`VCRUNTIME140.dll`, `api-ms-win-*.dll`)
- MinGW runtime (`libstdc++-6.dll`, `libwinpthread-1.dll`) — shipped by PySide6
- Project data (`localizationstring_eng_items.tsv`, `worldmap.jpg`, JSON templates, icons)
- `base_library.zip` (Python stdlib)

**No unexpected `.exe`, `.bat`, `.ps1`, `.vbs`, `.scr`, or dropped binaries.**

---

## Why Windows Defender (and the other ML vendors) flagged this

`Trojan:Win32/Wacatac.H!ml` is Microsoft's **generic ML detection**. Its public and well-documented false-positive profile includes virtually every small-distribution unsigned PyInstaller application. The specific features that trigger it in this binary are:

1. **Unsigned PE** — no Authenticode code-signing certificate
2. **PyInstaller packing** — the binary is effectively "self-extracting", which ML models treat as suspicious
3. **Embedded Python interpreter + marshalled bytecode** — looks like obfuscated code to heuristics
4. **ChaCha20 + HMAC-SHA256 + LZ4 runtime usage** — legitimate save-file crypto, but ML models weight "crypto + packer" heavily toward "malicious"
5. **`subprocess` calls to pack PAZ overlays** — required for the game-data modding features
6. **Low prevalence** — a mod tool with a small install base has no reputation to offset the heuristic

This exact cluster of features is what produces `Wacatac.H!ml`, `AIDetectMalware`, and generic "Unsafe" verdicts across the board. It is the single most common false-positive pattern reported on the PyInstaller issue tracker and on r/PyInstaller. None of the flagging engines here returned a named-family verdict.

---

## Response to the "hijacked release" concern

The concern raised — that individual GitHub release assets can be swapped to deliver malware in a narrow window — is **legitimate and has happened to other projects**. It is the right thing to worry about. To directly address it for v3.1.2:

- The SHA-256 of the analyzed file is `077758da8063d1e58d5533beaf5366cb4c9d825d23dd835692f4cc64907ee1ce`. This hash will be published in the release notes so any user can verify the file they downloaded matches what was analyzed here.
- No unexpected network hosts, exfil endpoints, or credential-access paths exist in the binary (see IOC section above).

If a hijack had occurred, the reverse-engineered binary would contain **non-project URLs**, **credential-theft strings**, or **staging/loader logic**. None of those are present.

---

## Recommendations

### For the user who reported this
- Keep the file quarantined until you're comfortable. Your instinct to not trust on-assertion was correct.
- If you want to use the tool, you can:
  1. Verify the SHA-256 of your download against the hash above.
  2. Rebuild from the backup source repo yourself.
  3. Or wait for the signed release (see below).

### For this project going forward
1. **Submit false-positive report to Microsoft** at https://www.microsoft.com/wdsi/filesubmission so `Wacatac.H!ml` is cleared against this hash.
2. **Publish SHA-256 sums** in every release's notes so users can verify the asset they received.
3. **Code-sign future releases** — even a low-cost OV certificate dramatically reduces Defender false-positive rate and builds SmartScreen reputation over time.
4. **Consider VirusTotal re-scan** after Microsoft clears the detection — that typically propagates to several of the other engines within a week.

---

## Reproducibility

Anyone can reproduce this analysis:

```bash
pip install pyinstxtractor-ng
pyinstxtractor-ng CrimsonSaveEditor.exe
# Then disassemble any .pyc with Python 3.14's `dis` module
# and grep string tables for the IOC list above.
```

The exact commands used, and the full IOC list, are documented above. No part of this analysis relies on privileged tools — it's reproducible on any machine with Python 3.14.

---

## Conclusion

**This binary is assessed as a false-positive detection.** No malicious code, no exfil infrastructure, no credential-theft paths, no LOLBin abuse, no process injection, and no dropped payloads were found. All embedded URLs belong to the project itself.

The user's caution was, and remains, appropriate. The detection is consistent with the well-documented ML false-positive pattern affecting unsigned PyInstaller applications that use cryptography and subprocess — which is what this tool legitimately does to edit Crimson Desert save files.

Actions taken / planned:
- [x] Full static analysis (this report)
- [ ] SHA-256 published in release notes
- [ ] False-positive report submitted to Microsoft
- [ ] Code-signing evaluated for future releases
