# DTStoDDPlus

Command-line Python tool that recursively scans video files, identifies English DTS audio (when no existing compatible lossy track is present), and converts exactly that track to Dolby Digital Plus (E‑AC‑3) at 640 kbit/s while stream‑copying everything else (video, other audio tracks, subtitles, attachments).

---

## Key Features
* Recursive directory scan with filename wildcard filtering (`--filter`).
* MediaInfo XML parsing (no external Python deps) to enumerate audio tracks.
* Skip logic: if ANY AC-3 / E-AC-3 / AAC audio track already exists, the file is skipped (already has a compatible lossy track).
* Targeting: first English (`en`) DTS track only (current limitation).
* ffmpeg one-pass command maps all streams, re-encodes only the target audio to E-AC-3 640k, copies everything else.
* Dry run (`--dry-run`) prints actions + per-file ffmpeg command without modifying disk.
* Batch generation (`--dry-run-batch path.bat`) writes executable Windows batch file with all ffmpeg commands (implies dry run) plus helpful REM metadata & a MediaInfo GUI sample command.
* Extensive safeguards: temporary output, validation of track count & codec change, size tolerance (±10% by default) unless the source DTS is lossless (DTS-HD MA / DTS:X) where shrink is expected.
* Lossless DTS detection heuristics (keywords: MA, Master Audio, XLL, DTS:X) to conditionally skip size tolerance check.
* Preservation of failed attempts: invalid conversions are renamed to `.BAD_CONVERT[_{n}]` instead of being deleted, enabling later re‑verification.
* Re-verification mode (`--reverify-bad-convert <percent>`) promotes previously failed conversions if they now satisfy relaxed (or tightened) size variance and content checks.
* Cleanup mode (`--clean-temp-files`) promotes orphaned `*.temp.<ext>` outputs OR re-labels them as BAD when the original still exists.
* End-of-run dry-run summary block with aggregate statistics (total size, average size, lossless count, per-file track index).

---

## Requirements
* Python 3.8+
* MediaInfo CLI (needed for parsing): default expected path
  * `C:\Program Files\MediaInfo_CLI\MediaInfo.exe`
* (Optional for batch comments) MediaInfo GUI path referenced internally:
  * `C:\Program Files\MediaInfo\MediaInfo.exe`
* ffmpeg executable:
  * `C:\Program Files\ffmpeg\bin\ffmpeg.exe`

If your installs differ, edit the constants near the top of `DTStoDDPlus.py`:
```python
MEDIAINFO_PATH = r"C:\\Program Files\\MediaInfo_CLI\\MediaInfo.exe"
MEDIAINFO_GUI_PATH = r"C:\\Program Files\\MediaInfo\\MediaInfo.exe"  # optional
FFMPEG_PATH = r"C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"
```

No non‑standard Python packages are required (standard library only).

---

## Installation
Single-file script. Options:
1. Place `DTStoDDPlus.py` in a tools folder already on your PATH.
2. Keep it in a project directory and invoke with an absolute / relative path.

Optional virtual environment (only needed if you plan to extend/develop):
```cmd
python -m venv .venv
.venv\Scripts\activate
```

---

## CLI Usage Overview
Basic dry run (recommended first):
```cmd
python DTStoDDPlus.py "D:\Media\Movies" --dry-run
```
Live conversion:
```cmd
python DTStoDDPlus.py "D:\Media\Movies"
```
Wildcard filtering (basename only):
```cmd
python DTStoDDPlus.py "D:\Media\Movies" --filter "*.mkv"
```
Generate batch file of commands (implies dry run):
```cmd
python DTStoDDPlus.py "D:\Media\Movies" --dry-run-batch C:\Temp\ddpconvert.bat
```
Re-verify previous BAD conversions with ±20% size window:
```cmd
python DTStoDDPlus.py "D:\Media\Movies" --reverify-bad-convert 20
```
Cleanup orphaned temp outputs:
```cmd
python DTStoDDPlus.py "D:\Media\Movies" --clean-temp-files
```

### Command Line Options
| Option | Description |
|--------|-------------|
| `directory` | Root directory to scan recursively. |
| `--dry-run` | Analyze & print intended conversions without writing files. |
| `--dry-run-batch BATCH_FILE` | Write ffmpeg commands to a Windows batch file (auto prepends `@echo off`). Implies dry run. |
| `--filter PATTERN` | Wildcard (fnmatch style) applied to filenames (default `*`). Example: `*.mkv`. |
| `--reverify-bad-convert PERCENT` | Scan for `.BAD_CONVERT*` files and re-validate using a new ±percent size variance (e.g. `25` or `25%`). |
| `--clean-temp-files` | Promote `*.temp.<ext>` to `<base>.<ext>` when original missing, else rename temp to `.BAD_CONVERT`. |

---

## Qualification Rules (What Gets Converted)
A file is a candidate only if ALL are true:
1. Extension in: `.mkv`, `.mp4`, `.m4v`, `.mov`.
2. At least one audio track present.
3. Contains an English (`en`) DTS track.
4. Contains NO audio tracks of format: AC-3, E-AC-3, AAC (these imply an existing compatible lossy option already present).

If multiple English DTS tracks exist, the first is used (see Limitations).

---

## ffmpeg Strategy
* Build command: map all streams (`-map 0`).
* Global copy (`-c copy`) then override only the target audio index: `-c:a:<idx> eac3 -b:a:<idx> 640k`.
* Output written to sibling temporary file: `<name>.temp<ext>`.
* Upon validation success, original is replaced atomically (delete original; rename temp to original name).

Example command structure (index will vary):
```cmd
"C:\Program Files\ffmpeg\bin\ffmpeg.exe" -i "input.mkv" -map 0 -c copy -c:a:1 eac3 -b:a:1 640k -n "input.temp.mkv"
```

---

## Safeguards & Validation
After encoding, before replacing the original:
1. Temp file exists and non-zero.
2. (Unless lossless DTS source) size within ±10% of original.
3. MediaInfo parse succeeds on temp file.
4. Audio track count unchanged.
5. Target audio index now reports `E-AC-3`.
6. At least one `E-AC-3` track present overall.

On failure the temp file is preserved as `.BAD_CONVERT` (unique numeric suffix appended if needed).

Lossless DTS (detected by heuristic profile strings) skips size tolerance entirely because large shrink is expected.

---

## Dry Run Summary Block
When using `--dry-run` (or `--dry-run-batch`) a final summary prints:
* Each candidate: track index, lossless flag, human readable size, full path.
* Totals: number of files, cumulative size, how many are lossless, average size.

Use this to sanity check the set of conversions before running live.

---

## Re-verifying Failed Conversions (`--reverify-bad-convert`)
Failed validations leave behind: `name.BAD_CONVERT[_{n}].ext`.

Re-verify mode logic per BAD file:
* Original still exists.
* BAD file has at least one English E-AC-3 track.
* Audio track counts match original.
* BAD size within the specified ±variance of original.

If all pass:
* Original renamed to `name.ORIG_BACKUP[_{n}].ext`.
* BAD file renamed to original filename (promoted).

Exit code is 0 if at least one replacement occurred (or nothing found), 1 if BAD files were found but none validated.

You can supply either `25` or `25%`; must be > 0.

---

## Cleaning Temp Files (`--clean-temp-files`)
Looks for files matching pattern `*.temp.<ext>` (supported container ext). For each:
* If `<base>.<ext>` missing: promote temp -> original name.
* If `<base>.<ext>` exists: rename temp to `.BAD_CONVERT[_{n}]` (keeps forensic info) instead of deleting.

Always returns exit code 0 unless the directory argument is invalid.

---

## File Naming Conventions
| Pattern | Meaning |
|---------|---------|
| `movie.temp.mkv` | Freshly encoded candidate pending validation / replacement. |
| `movie.BAD_CONVERT.mkv` / `movie.BAD_CONVERT_2.mkv` | Failed validation (size/codec/track mismatch). |
| `movie.ORIG_BACKUP.mkv` / `movie.ORIG_BACKUP_3.mkv` | Original preserved after promoting a BAD file during re-verify. |

---

## Exit Codes
| Code | Meaning |
|------|---------|
| 0 | Success (or no actionable files) |
| 1 | Invalid / nonexistent directory |
| 2 | Environment validation failed (MediaInfo or ffmpeg missing) |
| 3 | Could not initialize batch file (when using `--dry-run-batch`) |
| 4 | Invalid `--reverify-bad-convert` value |

Re-verify mode returns 0 (success or nothing to do) or 1 (examined BAD files but none promoted) or 4 (bad argument) / 2 (env) / 1 (dir). Clean mode uses 0 unless directory invalid.

---

## Example Batch File Snippet
```
@echo off
REM Auto-generated ffmpeg commands for DTS->E-AC-3 conversion
REM File: D:\Media\Movies\Example.mkv
REM Target audio stream index: 1
REM MediaInfo GUI sample: "C:\Program Files\MediaInfo\MediaInfo.exe" "D:\Media\Movies\Example.mkv"
"C:\Program Files\ffmpeg\bin\ffmpeg.exe" -i "D:\Media\Movies\Example.mkv" -map 0 -c copy -c:a:1 eac3 -b:a:1 640k -n "D:\Media\Movies\Example.temp.mkv"
```

---

## Limitations / Roadmap
* Only first English DTS track handled (multi-track selection not yet implemented).
* Stream disposition flags (default / forced) not explicitly preserved beyond what ffmpeg inherits.
* No option yet to retain original DTS track (dual-track output).
* Bitrate fixed at 640k (no CLI override yet).
* Sequential processing (no parallelization / queueing).
* No structured logging (JSON) or log-to-file option.
* Dependency paths configured only via constants (no CLI / env overrides yet).

Planned enhancements may address the above; contributions welcome.

---

## Troubleshooting
| Symptom | Suggestion |
|---------|------------|
| "ERROR: MediaInfo not found" | Edit `MEDIAINFO_PATH` to your actual install path. |
| "ERROR: ffmpeg not found" | Edit `FFMPEG_PATH` constant. |
| File skipped unexpectedly | Run with `--dry-run` and review audio track list; an AC-3 / E-AC-3 / AAC track may already be present. |
| Size safeguard failing | For lossless DTS expect large shrink; if incorrectly classified, review heuristic strings. |
| BAD file not promoted in re-verify | Ensure `%` variance large enough and file contains English E-AC-3 track. |
| Nothing listed in dry run | Check wildcard filter pattern or ensure extensions are supported. |

Path / Unicode caveats: test first on a small sample directory; Windows long path support may vary with environment settings.

---

## Contributing
Issues / PRs welcome. Keep changes minimal, dependency-free when possible, follow PEP 8, and preserve clear logging. Consider adding optional flags instead of changing current defaults.

---

## License
Add your preferred license statement here (e.g., MIT, Apache-2.0). Until then, all rights reserved by the author.

---

## Disclaimer
ALWAYS keep backups of your media. Although the tool includes safeguards, no warranty is provided.
