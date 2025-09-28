## DTStoDDPlus – AI Coding Agent Instructions

Purpose: Single-file Python CLI that scans media libraries and converts exactly one English DTS audio track to Dolby Digital Plus (E-AC-3) at 640k when (and only when) no compatible lossy track (AC-3 / E-AC-3 / AAC) already exists. Everything else (video, other audio, subs, attachments) is stream‑copied. No third‑party Python packages—only stdlib plus external binaries (MediaInfo CLI + ffmpeg).

Source Control Policy: Do NOT stage/add/commit or push git changes automatically; the maintainer will handle all VCS operations manually. Limit actions to editing files only.

### Core Architecture (All in `DTStoDDPlus.py`)
1. Argument parsing (`parse_args`) defines mutually exclusive “modes” that short‑circuit: listing, reverify, clean temp, batch (implies dry run), live conversion.
2. Environment validation (`validate_environment`) ensures required external binaries are present before any mode that needs them.
3. Directory traversal (`scan_directory`) recursively enumerates candidate video files filtered by extension + optional `--filter` (fnmatch on basename) and calls `process_file`.
4. Media analysis pipeline per file:
   * `run_mediainfo` executes MediaInfo CLI with XML output.
   * `_strip_xml_namespaces` normalizes tags; `extract_audio_tracks` builds a lightweight list of dicts: `{format, language, lossless}`.
   * `find_target_dts_index` enforces conversion rules (skip if any compatible format present; select first English DTS track).
5. Command construction: `build_ffmpeg_command` maps all streams, globally copies, overrides only the target audio index to E-AC-3 640k, writes to a sibling temp file `<name>.temp<ext>`.
6. Live conversion path runs ffmpeg then invokes `_validate_converted_file` (size tolerance unless lossless DTS, track count, target codec). On failure, temp is renamed to `.BAD_CONVERT[_{n}]`; on success original is atomically replaced.
7. Dry run + batch accumulate candidate metadata via `_record_dry_run_candidate` for a deterministic summary (`_print_dry_run_summary`). Batch writing uses `_write_batch_command` adding REM metadata (input path, target index, MediaInfo GUI sample).
8. Ancillary maintenance modes: `reverify_bad_converts` (promotes previously failed conversions given relaxed/tighter size variance) and `clean_temp_files` (promotes or re-labels orphaned temp outputs).

### Key Constants (top of file)
MEDIAINFO_PATH, MEDIAINFO_GUI_PATH, FFMPEG_PATH – hardcoded Windows paths (user edits here; no CLI/env overrides yet).
SUPPORTED_EXTENSIONS – { .mkv, .mp4, .m4v, .mov }.
COMPATIBLE_EXISTING_FORMATS – { AC-3, E-AC-3, AAC } triggers skip.
SIZE_TOLERANCE_FRACTION – ±10% (only enforced for lossy DTS sources; skipped for lossless DTS-HD variants detected heuristically).

### Mode Decision Flow (in `main`)
Order of evaluation matters: list_dts_no_dd → reverify_bad_convert → clean_temp_files → (optional batch init toggles dry-run) → standard scan (dry or live). Any earlier mode returns immediately and bypasses later logic. Keep this ordering if adding new top-level modes.

### File Naming Conventions
<base>.temp.ext – freshly encoded candidate awaiting validation.
<base>.BAD_CONVERT[_n].ext – failed validation (preserved for forensic / later reverify).
<base>.ORIG_BACKUP[_n].ext – original preserved when a BAD file supersedes it during reverify.

### Validation Logic (Do NOT Diverge Lightly)
_validate_converted_file enforces: existence + non-zero size, optional size window, same audio track count, target index now E-AC-3, at least one E-AC-3 overall. Any change here should simultaneously update README Safeguards section. Lossless DTS detection lives in `_is_lossless_dts` (heuristic keywords: ma, master audio, xll, dts:x).

### Dry Run Summary Stability
Collected in DRY_RUN_CANDIDATES; summary sorted by path for deterministic output (important for batch diffs and scripting). Preserve the sort to avoid noisy diffs.

### Exit Codes (Synchronize if Edited)
0 success / nothing actionable; 1 directory or (in reverify) none promoted; 2 environment validation failed; 3 batch file init error; 4 invalid reverify percent. Maintain mapping with README table when modifying.

### Typical Developer Workflows
Dry test: `python DTStoDDPlus.py D:\Media --dry-run` (ensures only logging, populates summary).
Generate batch: `python DTStoDDPlus.py D:\Media --dry-run-batch C:\Temp\ddpconvert.bat` (creates Windows .bat with REM metadata; implies dry run; does not mutate media).
Live run: same without flags (after verifying dry run output).
Investigate English DTS lacking Dolby: `--list-dts-no-dd` (ignores AAC presence for discovery only).
Re-validate failed conversions: `--reverify-bad-convert 25` (percent or percent%).
Housekeeping orphaned temps: `--clean-temp-files`.

### Adding Features – Patterns to Follow
1. New mode? Place early in `main` before standard scan, mirror existing short‑circuit pattern, return explicit exit code.
2. New CLI flag? Add to `parse_args`; prefer argparse defaults over post-processing. Keep flag names kebab‑case.
3. External tool invocation? Use `subprocess.run([...], stdout=PIPE, stderr=PIPE, check=True)` for analysis (like MediaInfo) OR capture_output for ffmpeg. Always log a tail of stderr on failure (see existing patterns).
4. Output artifacts should NEVER overwrite originals directly; write to temp then validate then replace.
5. When generating additional batch/script outputs, append REM metadata lines with stable ordering for diffability.

### Conventions & Style
Single-file script intentionally dependency-free (keep `requirements.txt` comment-only unless adding vetted minimal dependencies with pinning).
Logging uses plain `print` via `log()`; maintain the bracketed prefixes ([INFO], [SCAN], [DRY RUN], [SUMMARY], [SAFEGUARD], [REVERIFY], [CLEAN], [FFMPEG], [CONVERT], [SUCCESS], [ABORT], [SKIP]) for grep-friendly diagnostics.
Prefer small, pure helper functions; avoid introducing classes unless complexity grows substantially.

### Safe Extension Examples
Add bitrate override: new `--bitrate 640k` flag feeding `CONVERT_BITRATE` (keep default constant if flag omitted).
Add retention flag to keep original DTS: update ffmpeg command to duplicate stream instead of transcoding in place (map original + new E-AC-3), but then adjust validation (track count will increase) and README Safeguards section accordingly.

### Pitfalls / Edge Cases
Multiple English DTS tracks: only first selected—document if you change selection logic (update README Qualification + Limitations).
Lossless detection false negatives mean large shrink may fail size tolerance; users rely on ability to re-run with relaxed variance or manual reverify.
Renaming collisions for BAD/ORIG_BACKUP handled by numeric suffix loops—preserve that pattern to avoid data loss.

### Where to Look Before Changing Behavior
Target selection: `find_target_dts_index`.
Heuristics for lossless: `_is_lossless_dts`.
Validation/safety: `_validate_converted_file`.
Reverify mechanics: `reverify_bad_converts`.
Cleanup behavior: `clean_temp_files`.

### Keep In Sync
If you alter constants, exit codes, or validation rules, immediately update both this file and README sections: Requirements, Safeguards & Validation, Exit Codes, Limitations.

---
Questions or ambiguous areas? Ask the maintainer to confirm intended behavior before refactoring safety / validation logic.