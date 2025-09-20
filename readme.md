# DTStoDDPlus

Command-line Python tool to locate video files containing a single English DTS audio track (and no other pre-existing compatible lossy tracks) and convert that track to Dolby Digital Plus (E-AC-3) at 640k while copying all other streams untouched.

## Features
- Recursive directory scanning
- MediaInfo XML parsing to detect audio tracks
- Skips files if any AC-3 / E-AC-3 / AAC track already exists
- Converts only the English DTS track to E-AC-3 (640k)
- Preserves video, other audio, subtitle, attachment streams (stream copy)
- Dry-run mode prints intended ffmpeg commands without changing files
- Safe replace using temporary output file
- Re-verify previously failed conversions (`.BAD_CONVERT*` files) with a custom size variance tolerance

## Requirements
- Python 3.8+
- MediaInfo CLI installed at:
  - `C:\\Program Files\\MediaInfo_CLI\\MediaInfo.exe`
- ffmpeg installed at:
  - `C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe`

Adjust the hardcoded paths in `DTStoDDPlus.py` if yours differ:
```python
MEDIAINFO_PATH = r"C:\\Program Files\\MediaInfo_CLI\\MediaInfo.exe"
FFMPEG_PATH = r"C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"
```

## Installation
Clone or copy the single script `DTStoDDPlus.py` into a directory in your PATH or run it in place.

(Optional) Create a virtual environment if you plan to extend functionality:
```
python -m venv .venv
.venv\\Scripts\\activate
```
Currently the script uses only the standard library.

## Usage
Dry run (recommended first):
```
python DTStoDDPlus.py "D:\\Media\\Movies" --dry-run
```
Live conversion:
```
python DTStoDDPlus.py "D:\\Media\\Movies"
```
Filter by filename pattern (wildcards):
```
python DTStoDDPlus.py "D:\\Media\\Movies" --filter "*.mkv"
```
Write ffmpeg commands to a batch file (implies dry-run):
```
python DTStoDDPlus.py "D:\\Media\\Movies" --dry-run-batch C:\\Temp\\ddpconvert.bat
```
Re-verify prior failed conversions (see Re-verify section below):
```
python DTStoDDPlus.py "D:\\Media\\Movies" --reverify-bad-convert 20
```
(Above allows +/-20% size variance. You can also specify `20%`.)

Exit codes:
- 0 success
- 1 invalid directory
- 2 dependency missing (MediaInfo or ffmpeg not found)
- 3 batch file init error
- 4 invalid `--reverify-bad-convert` argument

## What Gets Converted
A file qualifies only if ALL are true:
1. Container extension is one of: .mkv .mp4 .m4v .mov
2. Contains at least one audio track
3. Contains an English (`en`) DTS track
4. Contains NO audio tracks with formats: AC-3, E-AC-3, AAC

If multiple English DTS tracks exist, only the first is converted (current limitation).

## ffmpeg Strategy
Single-pass command:
- Map all streams (`-map 0`)
- Copy all by default (`-c copy`)
- Re-encode target audio index to E-AC-3 640k via override (`-c:a:<index> eac3 -b:a:<index> 640k`)
- Temporary file written next to original, then replaces original on success.

## Safety
- Dry run prints: files that would convert + full ffmpeg command.
- `-n` flag prevents overwriting an existing temp file.
- Failed validations are renamed to `.BAD_CONVERT` (with numeric suffix if needed) instead of being deleted.
- Re-verify mode can later promote a `.BAD_CONVERT` file if conditions become acceptable.

## Re-verifying Failed Conversions (`--reverify-bad-convert`)
If a conversion failed validation (e.g. size outside the default ±10% window) the temp file is kept as:
```
<basename>.BAD_CONVERT<optional_number><ext>
```
Use:
```
python DTStoDDPlus.py "D:\\Media\\Movies" --reverify-bad-convert 25
```
This scans recursively for `.BAD_CONVERT` files, re-parses both the BAD and original files using MediaInfo, and validates:
- Original file still exists
- Track counts match
- BAD file contains at least one English E-AC-3 track
- BAD file size within the specified +/- percent of the original size (e.g. 25% ? between 75% and 125%)

If all checks pass:
- Original file is renamed to `<basename>.ORIG_BACKUP[(_n)].<ext>`
- BAD file is renamed to the original filename

This lets you relax (or tighten) size tolerance later without re-encoding.

You can specify the variance as either `20` or `20%` (both mean 20%). Values must be > 0.

## Limitations / Future Enhancements
- Preserve and propagate default/forced track flags explicitly
- Retain original audio track order if complex scenarios arise
- Option to keep original DTS track instead of replacing (dual-track mode)
- Custom bitrate / channel layout options
- Parallel processing
- Logging to file / structured JSON output
- Configurable paths to dependencies via CLI flags or env vars

## Troubleshooting
"MediaInfo not found" / "ffmpeg not found": Verify the executables exist at the hardcoded paths or edit the constants.

No files processed: Use `--dry-run` and confirm there is an English DTS track and no AC-3/E-AC-3/AAC tracks.

Re-verify not promoting files: Ensure the chosen variance is large enough and that the BAD file truly has an English E-AC-3 track.

Unicode / path issues: Prefer ASCII-only paths for initial testing or run from an elevated shell if permissions are involved.

## Contributing
Open to improvements; script intentionally compact. Follow PEP8 and keep external deps optional.

## License
Add your preferred license statement here.
