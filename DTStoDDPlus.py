# -*- coding: utf-8 -*-
"""DTStoDDPlus
Command-line tool to convert English DTS audio tracks to Dolby Digital Plus (E-AC-3)
while leaving other streams untouched.

Product Requirements Implemented (initial version):
- Directory scanning
- File filtering (English DTS present, and NO AC3/E-AC-3/AAC tracks)
- ffmpeg command construction to convert only the target audio track (640k)
- Dry run mode printing intended actions
- Safe replacement using temp file

Additions:
- --dry-run-batch <file> writes exact ffmpeg commands into a batch script (implies dry-run)
- Batch file includes commented metadata: target audio index & MediaInfo sample command
- --filter <pattern> simple wildcard filename filter applied to video basenames
- Temp output now keeps original extension (e.g. movie.temp.mkv)
- Post-conversion safeguards: verify new file contains expected E-AC-3 track and file size within ±10% before replacing original
- Lossless DTS (DTS-HD MA / DTS:X) detection; size safeguard skipped for these since large shrink expected
- Dry-run summary: at end of dry run (or batch) list all files that WOULD convert with stats (size, track index, lossless) + totals
- --reverify-bad-convert <percent> re-check previously failed ".BAD_CONVERT" files using a new size variance (percent) and if valid (English E-AC-3 present & within size tolerance) replace originals

Assumptions:
- MediaInfo CLI at C:\\Program Files\\MediaInfo_CLI\\MediaInfo.exe
- MediaInfo GUI at C:\\Program Files\\MediaInfo\\MediaInfo.exe (used only for batch sample comment)
- ffmpeg.exe at C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe

Limitations (future work):
- Does not handle multiple English DTS tracks (first one only)
- Does not refine stream disposition (default/forced flags) preserving
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import fnmatch
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Tuple
import re

MEDIAINFO_PATH = r"C:\\Program Files\\MediaInfo_CLI\\MediaInfo.exe"  # CLI used for parsing
MEDIAINFO_GUI_PATH = r"C:\\Program Files\\MediaInfo\\MediaInfo.exe"   # GUI reference for comments
FFMPEG_PATH = r"C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"
SUPPORTED_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".mov"}
TARGET_DTS_LANGUAGE = "en"  # English
CONVERT_BITRATE = "640k"
COMPATIBLE_EXISTING_FORMATS = {"AC-3", "E-AC-3", "AAC"}  # If any present, skip file
SIZE_TOLERANCE_FRACTION = 0.10  # +/- 10%

# Accumulator for dry-run summary (list of dicts)
DRY_RUN_CANDIDATES: List[Dict] = []


def log(msg: str) -> None:
    """Central logging helper (stdout)."""
    print(msg, flush=True)


def _strip_xml_namespaces(root: ET.Element) -> None:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def run_mediainfo(file_path: Path) -> Optional[ET.Element]:
    log(f"[INFO] Analyzing file with MediaInfo: {file_path}")
    try:
        proc = subprocess.run(
            [MEDIAINFO_PATH, "--Output=XML", str(file_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        log(f"ERROR: Failed to run MediaInfo on {file_path}: {e}")
        return None

    if not proc.stdout:
        if proc.stderr:
            tail = proc.stderr.decode(errors="replace")[-400:]
            log(
                f"ERROR: MediaInfo produced no stdout for {file_path}. Stderr tail:\n{tail}"
            )
        else:
            log(f"ERROR: MediaInfo produced empty output for {file_path}")
        return None

    xml_text = proc.stdout.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        snippet = xml_text[:400]
        log(
            f"ERROR: Failed to parse MediaInfo XML for {file_path}: {e}. Snippet:\n{snippet}"
        )
        return None

    _strip_xml_namespaces(root)
    return root


def _is_lossless_dts(track: ET.Element, fmt: str) -> bool:
    """Heuristic detection of lossless DTS variants (DTS-HD MA, DTS:X).
    Looks for typical profile/commercial/info strings containing MA/master/xll/dts:x.
    """
    if fmt != "DTS":
        return False
    # Gather possible descriptor fields
    fields = []
    for tag in [
        "Format_Profile",
        "Format_profile",
        "Format_Commercial",
        "Format/Info",
        "Format_Info",
        "Format_Addition",
        "Format_AdditionalFeatures",
    ]:
        val = track.findtext(tag, default="")
        if val:
            fields.append(val.lower())
    blob = " ".join(fields)
    keywords = ["ma", "master audio", "xll", "dts:x"]
    return any(k in blob for k in keywords)


def extract_audio_tracks(root: ET.Element) -> List[Dict]:
    tracks: List[Dict] = []
    for track in root.findall('.//track'):
        if track.get("type") != "Audio":
            continue
        fmt = track.findtext("Format", default="").strip()
        lang = (
            track.findtext("Language/String", default="").strip()
            or track.findtext("Language", default="").strip()
            or "und"
        )
        lossless = _is_lossless_dts(track, fmt)
        tracks.append({"format": fmt, "language": lang.lower(), "lossless": lossless})
    return tracks


def summarize_tracks(file_path: Path, tracks: List[Dict]) -> None:
    if not tracks:
        log(f"[INFO] No audio tracks detected: {file_path}")
        return
    log(f"[INFO] Audio tracks ({len(tracks)}): {file_path}")
    for i, t in enumerate(tracks):
        lossless_flag = " (lossless DTS-HD)" if (t['format'] == 'DTS' and t.get('lossless')) else ""
        log(f"    - index={i} format={t['format'] or '?'} language={t['language']}{lossless_flag}")


def find_target_dts_index(audio_tracks: List[Dict]) -> Tuple[Optional[int], str]:
    if not audio_tracks:
        return None, "No audio tracks found"
    has_compatible = [t for t in audio_tracks if t["format"] in COMPATIBLE_EXISTING_FORMATS]
    if has_compatible:
        fmts = ", ".join({t['format'] for t in has_compatible})
        return None, f"Found existing compatible format(s): {fmts}; skipping"
    for idx, t in enumerate(audio_tracks):
        if t["format"] == "DTS" and t["language"] == TARGET_DTS_LANGUAGE:
            return idx, "OK"
    has_dts = any(t["format"] == "DTS" for t in audio_tracks)
    if not has_dts:
        return None, "No DTS tracks present"
    has_english_dts = any(
        t["format"] == "DTS" and t["language"] == TARGET_DTS_LANGUAGE for t in audio_tracks
    )
    if not has_english_dts:
        return None, "DTS present but no English DTS track"
    return None, "Unknown filtering condition"


def build_ffmpeg_command(
    input_file: Path, temp_output: Path, target_audio_index: int
) -> List[str]:
    cmd = [
        FFMPEG_PATH,
        "-i",
        str(input_file),
        "-map",
        "0",
        "-c",
        "copy",
        f"-c:a:{target_audio_index}",
        "eac3",
        f"-b:a:{target_audio_index}",
        CONVERT_BITRATE,
        "-n",
        str(temp_output),
    ]
    return cmd


def _write_batch_command(batch_file: Path, cmd: List[str], input_file: Path, target_index: int) -> None:
    try:
        ffmpeg_line = subprocess.list2cmdline(cmd)
        mediainfo_gui_line = subprocess.list2cmdline([MEDIAINFO_GUI_PATH, str(input_file)])
        with batch_file.open("a", encoding="utf-8") as f:
            f.write(f"REM File: {input_file}\n")
            f.write(f"REM Target audio stream index: {target_index}\n")
            f.write(f"REM MediaInfo GUI sample: {mediainfo_gui_line}\n")
            f.write(ffmpeg_line + "\n\n")
    except Exception as e:
        log(f"ERROR: Failed writing to batch file {batch_file}: {e}")


def _validate_converted_file(original_file: Path, temp_file: Path, target_idx: int, original_tracks: List[Dict], skip_size_check: bool) -> bool:
    """Ensure temp_file looks sane before replacing original.

    Checks:
    - temp file exists & non-zero size
    - size within +/- SIZE_TOLERANCE_FRACTION of original (unless skip_size_check True)
    - media info parse succeeds
    - audio track count unchanged
    - target index exists and is E-AC-3 now
    - at least one E-AC-3 track present (fallback check)
    """
    try:
        if not temp_file.exists():
            log(f"[SAFEGUARD] Temp output missing: {temp_file}")
            return False
        orig_size = original_file.stat().st_size
        new_size = temp_file.stat().st_size
        if new_size == 0:
            log(f"[SAFEGUARD] Temp output is zero bytes: {temp_file}")
            return False
        if skip_size_check:
            log("[SAFEGUARD] Skipping size tolerance check (lossless DTS -> E-AC-3 expected shrink)")
        else:
            lower = (1 - SIZE_TOLERANCE_FRACTION) * orig_size
            upper = (1 + SIZE_TOLERANCE_FRACTION) * orig_size
            if not (lower <= new_size <= upper):
                log(f"[SAFEGUARD] Size difference outside tolerance: original={orig_size} new={new_size} (+/-{SIZE_TOLERANCE_FRACTION*100:.0f}% allowed)")
                return False

        root = run_mediainfo(temp_file)
        if root is None:
            log("[SAFEGUARD] MediaInfo failed on temp output")
            return False
        new_tracks = extract_audio_tracks(root)
        if len(new_tracks) != len(original_tracks):
            log(f"[SAFEGUARD] Audio track count changed original={len(original_tracks)} new={len(new_tracks)}")
            return False
        has_eac3 = any(t["format"] == "E-AC-3" for t in new_tracks)
        if not has_eac3:
            log("[SAFEGUARD] No E-AC-3 track found in converted file")
            return False
        if target_idx >= len(new_tracks):
            log(f"[SAFEGUARD] Target index {target_idx} out of range in new file")
            return False
        if new_tracks[target_idx]["format"] != "E-AC-3":
            log(f"[SAFEGUARD] Target track at index {target_idx} is not E-AC-3 (found {new_tracks[target_idx]['format']})")
            return False
        log("[SAFEGUARD] Validation passed for converted file")
        return True
    except Exception as e:
        log(f"[SAFEGUARD] Exception during validation: {e}")
        return False


def _record_dry_run_candidate(file_path: Path, target_idx: int, lossless: bool) -> None:
    """Append conversion candidate metadata for dry-run summary."""
    try:
        size = file_path.stat().st_size
    except OSError:
        size = 0
    DRY_RUN_CANDIDATES.append({
        "path": file_path,
        "size": size,
        "track_index": target_idx,
        "lossless": lossless,
    })


def process_file(file_path: Path, dry_run: bool, batch_file: Optional[Path]) -> None:
    log(f"[SCAN] Evaluating file: {file_path}")
    root = run_mediainfo(file_path)
    if root is None:
        log(f"[SKIP] MediaInfo failure: {file_path}")
        return
    audio_tracks = extract_audio_tracks(root)
    summarize_tracks(file_path, audio_tracks)
    target_idx, reason = find_target_dts_index(audio_tracks)
    if target_idx is None:
        log(f"[SKIP] {file_path} -> {reason}")
        return

    # Temp output uses same extension as original, with .temp inserted before extension
    temp_output = file_path.with_name(file_path.stem + ".temp" + file_path.suffix)
    cmd = build_ffmpeg_command(file_path, temp_output, target_idx)

    if batch_file is not None:
        _write_batch_command(batch_file, cmd, file_path, target_idx)

    if dry_run or batch_file is not None:
        lossless_note = " (lossless DTS-HD)" if audio_tracks[target_idx].get('lossless') else ""
        log(f"[DRY RUN] Would convert (audio stream index {target_idx} DTS->E-AC-3{lossless_note}): {file_path}")
        log(f"[DRY RUN] ffmpeg command: {' '.join(cmd)}")
        _record_dry_run_candidate(file_path, target_idx, audio_tracks[target_idx].get('lossless', False))
        return

    log(f"[CONVERT] DTS -> E-AC-3 (stream {target_idx}): {file_path}")
    # Always show the ffmpeg command used in live mode
    log(f"[FFMPEG] Command: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        log(f"ERROR: Failed to start ffmpeg: {e}")
        return

    if proc.returncode != 0:
        stderr_tail = proc.stderr[-800:] if proc.stderr else "(no stderr)"
        log(f"ERROR: ffmpeg failed for {file_path}\n{stderr_tail}")
        if temp_output.exists():
            try:
                temp_output.unlink()
            except Exception:
                pass
        return

    skip_size = audio_tracks[target_idx].get('lossless', False)

    # Safeguard validation before replacing original
    if not _validate_converted_file(file_path, temp_output, target_idx, audio_tracks, skip_size):
        log(f"[ABORT] Validation failed; original kept: {file_path}")
        # Rename temp file to mark bad conversion instead of deleting
        if temp_output.exists():
            bad_name = file_path.with_name(file_path.stem + ".BAD_CONVERT" + file_path.suffix)
            try:
                # If a previous BAD_CONVERT exists, attempt to remove or create unique name
                if bad_name.exists():
                    # Append numeric suffix
                    counter = 1
                    while True:
                        alt = file_path.with_name(f"{file_path.stem}.BAD_CONVERT_{counter}{file_path.suffix}")
                        if not alt.exists():
                            bad_name = alt
                            break
                        counter += 1
                temp_output.rename(bad_name)
                log(f"[ABORT] Bad temp preserved as: {bad_name}")
            except Exception as e:
                log(f"[ABORT] Failed to rename bad temp file {temp_output}: {e}")
        return

    try:
        file_path.unlink()
        temp_output.rename(file_path)
        log(f"[SUCCESS] Replaced original with converted file: {file_path}")
    except Exception as e:
        log(f"ERROR: Failed to replace original file for {file_path}: {e}")
        if temp_output.exists():
            try:
                temp_output.unlink()
            except Exception:
                pass


def is_supported_video(file_path: Path) -> bool:
    return file_path.suffix.lower() in SUPPORTED_EXTENSIONS


def scan_directory(root_dir: Path, dry_run: bool, batch_file: Optional[Path], name_pattern: str) -> None:
    count = 0
    for path in root_dir.rglob("*"):
        if path.is_file() and is_supported_video(path) and fnmatch.fnmatch(path.name, name_pattern):
            count += 1
            process_file(path, dry_run, batch_file)
    log(f"[INFO] Scan complete. Total candidate video files (matching pattern '{name_pattern}'): {count}")


def _parse_percent(value: str) -> float:
    """Parse a percent string like '20' or '20%' into fraction 0.20. Raises ValueError."""
    v = value.strip()
    if v.endswith('%'):
        v = v[:-1]
    frac = float(v) / 100.0
    if frac <= 0:
        raise ValueError("Percent must be > 0")
    return frac


def reverify_bad_converts(root_dir: Path, variance_fraction: float) -> int:
    """Attempt to re-validate previously failed .BAD_CONVERT files using a user-specified size variance.

    Rules:
    - Locate files whose name contains '.BAD_CONVERT' (with optional numeric suffix) and have a supported extension.
    - Derive original filename by removing the '.BAD_CONVERT[_N]' segment before the extension.
    - Validation passes when:
        * Original file exists
        * BAD file has at least one English (language 'en') E-AC-3 track
        * Audio track count matches original (safety)
        * BAD file size within +/- variance_fraction of original size
    - If valid, original is replaced by BAD file (rename), counted as success.
    """
    pattern = re.compile(r"(.*)\.BAD_CONVERT(?:_\d+)?(\.[^.]+)$", re.IGNORECASE)
    total = 0
    replaced = 0
    skipped = 0
    for path in root_dir.rglob("*"):
        if not path.is_file():
            continue
        if ".BAD_CONVERT" not in path.name:
            continue
        if not is_supported_video(path):
            continue
        m = pattern.match(path.name)
        if not m:
            continue
        total += 1
        original_name = m.group(1) + m.group(2)
        original_path = path.with_name(original_name)
        log(f"[REVERIFY] Found BAD file: {path}")
        if not original_path.exists():
            log(f"[REVERIFY][SKIP] Original missing: {original_path}")
            skipped += 1
            continue
        try:
            bad_size = path.stat().st_size
            orig_size = original_path.stat().st_size
        except OSError as e:
            log(f"[REVERIFY][SKIP] Size stat failed: {e}")
            skipped += 1
            continue

        lower = (1 - variance_fraction) * orig_size
        upper = (1 + variance_fraction) * orig_size
        size_ok = lower <= bad_size <= upper

        root_bad = run_mediainfo(path)
        root_orig = run_mediainfo(original_path)
        if root_bad is None or root_orig is None:
            log("[REVERIFY][SKIP] MediaInfo parse failed")
            skipped += 1
            continue
        bad_tracks = extract_audio_tracks(root_bad)
        orig_tracks = extract_audio_tracks(root_orig)
        if len(bad_tracks) != len(orig_tracks):
            log(f"[REVERIFY][SKIP] Track count mismatch original={len(orig_tracks)} bad={len(bad_tracks)})")
            skipped += 1
            continue
        has_en_eac3 = any(t['format'] == 'E-AC-3' and t['language'] == TARGET_DTS_LANGUAGE for t in bad_tracks)
        if not has_en_eac3:
            log("[REVERIFY][SKIP] No English E-AC-3 track present")
            skipped += 1
            continue
        if not size_ok:
            log(f"[REVERIFY][SKIP] Size variance exceeded: original={orig_size} bad={bad_size} allowed=+/-{variance_fraction*100:.1f}%")
            skipped += 1
            continue
        # Passed all checks: replace original
        try:
            backup = original_path.with_name(original_path.stem + ".ORIG_BACKUP" + original_path.suffix)
            if backup.exists():
                # avoid overwriting previous backup; add numeric suffix
                counter = 1
                while True:
                    alt = original_path.with_name(f"{original_path.stem}.ORIG_BACKUP_{counter}{original_path.suffix}")
                    if not alt.exists():
                        backup = alt
                        break
                    counter += 1
            original_path.rename(backup)
            path.rename(original_path)
            log(f"[REVERIFY][SUCCESS] Replaced original with validated BAD file. Backup: {backup}")
            replaced += 1
        except Exception as e:
            log(f"[REVERIFY][ERROR] Failed to swap files: {e}")
            skipped += 1
            continue
    log(f"[REVERIFY] Complete. Total BAD files examined: {total} | Replaced: {replaced} | Skipped: {skipped}")
    return 0 if replaced > 0 else (1 if total > 0 else 0)


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert English DTS tracks to E-AC-3 (Dolby Digital Plus)"
    )
    p.add_argument("directory", type=Path, help="Root directory to scan")
    p.add_argument(
        "--dry-run", action="store_true", help="Show actions without modifying files"
    )
    p.add_argument(
        "--dry-run-batch",
        type=Path,
        metavar="BATCH_FILE",
        help="Write each ffmpeg command to BATCH_FILE (implies dry-run)",
    )
    p.add_argument(
        "--filter",
        default="*",
        metavar="PATTERN",
        help="Filename wildcard (basename) to limit processed files, e.g. *.mkv (default: *)",
    )
    p.add_argument(
        "--reverify-bad-convert",
        metavar="PERCENT",
        help="Re-verify previously failed .BAD_CONVERT files using given size variance percent (e.g. 20 or 20%)",
    )
    return p.parse_args(argv)


def validate_environment() -> bool:
    ok = True
    if not Path(MEDIAINFO_PATH).is_file():
        log(f"ERROR: MediaInfo not found at {MEDIAINFO_PATH}")
        ok = False
    if not Path(FFMPEG_PATH).is_file():
        log(f"ERROR: ffmpeg not found at {FFMPEG_PATH}")
        ok = False
    return ok


def _format_size(num_bytes: int) -> str:
    # Simple human readable size
    units = ['B','KB','MB','GB','TB']
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def _print_dry_run_summary() -> None:
    if not DRY_RUN_CANDIDATES:
        log("[SUMMARY] No files require conversion.")
        return
    log("\n========== DRY RUN CONVERSION SUMMARY ==========")
    total = len(DRY_RUN_CANDIDATES)
    total_bytes = sum(c['size'] for c in DRY_RUN_CANDIDATES)
    lossless_count = sum(1 for c in DRY_RUN_CANDIDATES if c['lossless'])
    # Sort by path for determinism
    for c in sorted(DRY_RUN_CANDIDATES, key=lambda x: str(x['path']).lower()):
        lossless_flag = 'yes' if c['lossless'] else 'no'
        log(f"[SUMMARY] track={c['track_index']} lossless={lossless_flag} size={_format_size(c['size'])} :: {c['path']}")
    log("------------------------------------------------")
    log(f"[SUMMARY] Files to convert: {total}")
    log(f"[SUMMARY] Total size of candidates: {_format_size(total_bytes)}")
    log(f"[SUMMARY] Lossless DTS candidates: {lossless_count} ({(lossless_count/total*100):.1f}%)")
    avg = total_bytes / total if total else 0
    log(f"[SUMMARY] Average file size: {_format_size(int(avg))}")
    log("================================================\n")


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        # args = argparse.Namespace(directory=Path("X:\\Video\\Movies"), dry_run=True, dry_run_batch=Path("c:\\Temp\\ddpconvert.bat"), filter="*")
        # args = argparse.Namespace(directory=Path("X:\\Video\\Movies"), dry_run=False, dry_run_batch=None, filter="*", reverify_bad_convert=None)
        args = argparse.Namespace(directory=Path("X:\\Video\\Movies"), dry_run=False, dry_run_batch=None, filter="*", reverify_bad_convert="20")
    else:
        args = parse_args(argv or sys.argv[1:])

    # Re-verify mode short-circuits standard processing
    if getattr(args, 'reverify_bad_convert', None):
        try:
            variance_fraction = _parse_percent(args.reverify_bad_convert)
        except ValueError as e:
            log(f"ERROR: Invalid --reverify-bad-convert value: {e}")
            return 4
        if not validate_environment():  # Need at least MediaInfo
            # If ffmpeg missing we can still continue (only warn removed earlier). For simplicity keep existing gating.
            return 2
        directory: Path = args.directory
        if not directory.exists() or not directory.is_dir():
            log(f"ERROR: Directory does not exist: {directory}")
            return 1
        log(f"DTStoDDPlus starting. Mode=REVERIFY BAD_CONVERT. Variance=+/-{variance_fraction*100:.1f}%. Scanning: {directory}")
        code = reverify_bad_converts(directory, variance_fraction)
        log("Done.")
        return code

    batch_file: Optional[Path] = getattr(args, "dry_run_batch", None)
    if batch_file is not None:
        args.dry_run = True
        try:
            batch_file.parent.mkdir(parents=True, exist_ok=True)
            batch_file.write_text("@echo off\nREM Auto-generated ffmpeg commands for DTS->E-AC-3 conversion\n", encoding="utf-8")
            log(f"[INFO] Writing ffmpeg commands to batch file: {batch_file}")
        except Exception as e:
            log(f"ERROR: Cannot initialize batch file {batch_file}: {e}")
            return 3

    if not validate_environment():
        return 2
    directory: Path = args.directory
    if not directory.exists() or not directory.is_dir():
        log(f"ERROR: Directory does not exist: {directory}")
        return 1
    pattern = getattr(args, "filter", "*")
    mode = "DRY RUN BATCH" if batch_file else ("DRY RUN" if args.dry_run else "LIVE")
    log(f"DTStoDDPlus starting. Mode={mode}. Pattern='{pattern}'. Scanning: {directory}")
    scan_directory(directory, args.dry_run, batch_file, pattern)
    if args.dry_run or batch_file is not None:
        _print_dry_run_summary()
    log("Done.")
    if batch_file is not None:
        log(f"[INFO] Batch file ready: {batch_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
