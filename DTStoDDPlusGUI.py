"""Simple GUI wrapper for DTStoDDPlus.py

Features (maps to existing CLI modes):
 - Browse for root directory
 - Filename filter pattern (defaults to *)
 - List DTS (EN) without Dolby Digital (AC-3/E-AC-3)  -> --list-dts-no-dd
 - Dry Run conversion                                 -> --dry-run
 - Dry Run + Batch file generation                    -> --dry-run-batch <file>
 - Live Conversion                                    -> (no dry-run flags)
 - Reverify BAD_CONVERT files (user variance %)       -> --reverify-bad-convert <percent>
 - Clean Temp Files                                   -> --clean-temp-files

Implementation notes:
 - Uses subprocess to invoke the existing script, preserving single-file architecture and safety logic.
 - Output (stdout+stderr) is streamed live into a ScrolledText widget.
 - Runs long operations in a background thread so the GUI stays responsive.
 - Provides a Cancel button to terminate an in-flight subprocess.
 - No external dependencies beyond the Python standard library (tkinter, threading, queue, subprocess).

Usage:
  python DTStoDDPlusGUI.py

The GUI deliberately does not alter any logic in DTStoDDPlus.py; it only builds proper CLI argument lists.
"""

from __future__ import annotations

import sys, subprocess, threading, queue, json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

SCRIPT_NAME = "DTStoDDPlus.py"
STATE_FILE_NAME = ".dts_gui_state.json"
DEFAULT_MODIFIED_DAYS = 30


# ---------------- Tooltip Implementation (Stdlib Only) ----------------
class Tooltip:
    """Lightweight tooltip for Tkinter widgets.

    Displays a small toplevel window with explanatory text after a short delay
    when the pointer hovers over the widget. Destroys itself on mouse leave,
    button press, or if the widget is disabled/destroyed.
    """

    def __init__(self, widget: tk.Widget, text: str, delay: int = 600, wraplength: int = 420):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._unschedule)
        widget.bind("<ButtonPress>", self._unschedule)
        widget.bind("<KeyPress>", self._unschedule)

    def _schedule(self, _event=None):
        self._unschedule()
        self._id = self.widget.after(self.delay, self._show)

    def _unschedule(self, _event=None):
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None
        self._hide()

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tip,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            wraplength=self.wraplength,
            font=("Segoe UI", 9),
        )
        lbl.pack(ipadx=4, ipady=2)

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class DTSGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DTStoDDPlus GUI")
        self.geometry("980x640")
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._build_ui()
        self._poll_queue()
        # Persist state on close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI Construction ----------------
    def _build_ui(self) -> None:
        # Rebuild entire function with modified days
        pad = 6
        self._build_menubar()
        frm_top = ttk.Frame(self)
        frm_top.pack(side=tk.TOP, fill=tk.X, padx=pad, pady=(pad, 0))

        # Load previous state (if any)
        prev = self._load_state()
        prev_dir = prev.get("directory") or ""
        prev_filter = prev.get("filter") or "*"
        prev_modified = str(prev.get("modified_within_days") or DEFAULT_MODIFIED_DAYS)

        # Directory selection
        ttk.Label(frm_top, text="Root Directory:").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar(value=prev_dir)
        ttk.Entry(frm_top, textvariable=self.dir_var, width=70).grid(row=0, column=1, sticky="we", padx=(0, pad))
        ttk.Button(frm_top, text="Browse...", command=self._choose_dir).grid(
            row=0, column=2, sticky="we"
        )

        # Filter pattern
        ttk.Label(frm_top, text="Filter Pattern:").grid(row=1, column=0, sticky="w")
        self.filter_var = tk.StringVar(value=prev_filter)
        ttk.Entry(frm_top, textvariable=self.filter_var, width=20).grid(
            row=1, column=1, sticky="w", padx=(0, pad)
        )

        # Reverify variance percent
        ttk.Label(frm_top, text="Reverify %:").grid(row=1, column=2, sticky="e")
        self.reverify_var = tk.StringVar(value="25")
        ttk.Entry(frm_top, textvariable=self.reverify_var, width=6).grid(
            row=1, column=3, sticky="w"
        )

        # Modified days
        ttk.Label(frm_top, text="Modified ≤ Days:").grid(row=2, column=0, sticky="w")
        self.modified_days_var = tk.StringVar(value=prev_modified)
        ttk.Entry(frm_top, textvariable=self.modified_days_var, width=10).grid(
            row=2, column=1, sticky="w", padx=(0, pad)
        )

        frm_buttons = ttk.Frame(self)
        frm_buttons.pack(side=tk.TOP, fill=tk.X, padx=pad, pady=(pad, 0))

        # Action buttons
        self.btn_list = ttk.Button(frm_buttons, text="List DTS (no DD)", command=self._do_list)
        self.btn_dry = ttk.Button(frm_buttons, text="Dry Run", command=self._do_dry_run)
        self.btn_batch = ttk.Button(frm_buttons, text="Dry Run + Batch", command=self._do_batch)
        self.btn_live = ttk.Button(frm_buttons, text="Live Convert", command=self._do_live)
        self.btn_reverify = ttk.Button(frm_buttons, text="Reverify BAD", command=self._do_reverify)
        self.btn_clean = ttk.Button(frm_buttons, text="Clean Temps", command=self._do_clean)
        self.btn_cancel = ttk.Button(frm_buttons, text="Cancel", command=self._cancel_process, state=tk.DISABLED)
        self.btn_clear = ttk.Button(frm_buttons, text="Clear Output", command=self._clear_output)

        # Layout buttons
        for i, b in enumerate(
            [
                self.btn_list,
                self.btn_dry,
                self.btn_batch,
                self.btn_live,
                self.btn_reverify,
                self.btn_clean,
                self.btn_cancel,
                self.btn_clear,
            ]
        ):
            b.grid(row=0, column=i, padx=(0 if i == 0 else 4, 0), pady=2, sticky="we")
        for i in range(8):
            frm_buttons.grid_columnconfigure(i, weight=1)

        # Output area
        frm_out = ttk.Frame(self)
        frm_out.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)
        self.txt = ScrolledText(frm_out, wrap="word", font=("Consolas", 9))
        self.txt.pack(fill=tk.BOTH, expand=True)
        self._append_line("DTStoDDPlus GUI ready. Select a directory and choose an action.\n")
        # After widgets exist, attach tooltips
        self._attach_tooltips(frm_top)
        # Trace changes for persistence (lightweight, writes small JSON)
        for var in (self.dir_var, self.filter_var, self.modified_days_var):
            var.trace_add("write", lambda *_: self._save_state())

    # ---------------- Menu / Help ----------------
    def _build_menubar(self) -> None:
        menubar = tk.Menu(self)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Button Help", command=self._show_help)
        helpmenu.add_separator()
        helpmenu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.config(menu=menubar)

    def _show_help(self) -> None:
        # Create and/or raise a richer help window
        if hasattr(self, "_help_window") and self._help_window is not None:  # type: ignore[attr-defined]
            try:
                self._help_window.deiconify()  # type: ignore[attr-defined]
                self._help_window.lift()       # type: ignore[attr-defined]
                return
            except Exception:
                self._help_window = None  # type: ignore[attr-defined]

        win = tk.Toplevel(self)
        win.title("DTStoDDPlus – Help")
        win.geometry("900x560")
        self._help_window = win  # type: ignore[attr-defined]
        win.transient(self)
        win.grab_set()  # Modal-ish but allows main updates

        # Close on Escape
        win.bind("<Escape>", lambda _e: win.destroy())

        # Layout
        top = ttk.Frame(win)
        top.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        txt = ScrolledText(top, wrap="word", font=("Segoe UI", 10))
        txt.pack(fill=tk.BOTH, expand=True)

        # Tag styles
        txt.tag_configure("h1", font=("Segoe UI", 13, "bold"), spacing1=4, spacing3=6)
        txt.tag_configure("h2", font=("Segoe UI", 11, "bold"), spacing1=4, spacing3=4, foreground="#222299")
        txt.tag_configure("bullet", lmargin1=18, lmargin2=36)
        txt.tag_configure("mono", font=("Consolas", 10))
        txt.tag_configure("note", foreground="#444444", lmargin1=18, lmargin2=18, spacing1=2, spacing3=6)

        def add(tag, text=""):
            txt.insert(tk.END, text + "\n", tag)

        add("h1", "DTStoDDPlus GUI – Control Reference")
        add("note", "This GUI is a thin wrapper around DTStoDDPlus.py and never bypasses the script's safeguards.")

        add("h2", "Fields")
        add("bullet", "Root Directory – Base folder scanned recursively for supported containers: .mkv .mp4 .m4v .mov")
        add("bullet", "Filter Pattern – fnmatch pattern applied to each filename (not full path). Examples: *.mkv | Show*S01E* | *.")
        add("bullet", "Reverify % – Size variance window (+/-) used only by Reverify BAD mode to re‑evaluate prior failed conversions.")
        add("bullet", "Modified ≤ Days – Limit results to files modified within the last N days (0 to disable).")

        add("h2", "Primary Buttons")
        add("bullet", "List DTS (no DD) – Report files that have an English DTS track and NO AC-3 / E-AC-3 / AAC track. Discovery only.")
        add("bullet", "Dry Run – Determine which files WOULD be converted (English DTS present; no compatible lossy track). No changes.")
        add("bullet", "Dry Run + Batch – Dry run plus emits a deterministic .bat file containing ffmpeg commands + REM metadata.")
        add("bullet", "Live Convert – Perform safeguarded conversion: temp encode selected DTS -> E-AC-3 640k, validate, then atomic replace.")
        add("bullet", "Reverify BAD – Re-examine .BAD_CONVERT files using the specified variance; promote if now within safeguards.")
        add("bullet", "Clean Temps – Process lingering .temp files: validate & promote or mark as BAD. General housekeeping.")
        add("bullet", "Cancel – Best-effort termination of the active background process.")

        add("h2", "Safeguard Highlights")
        add("bullet", "All conversions go to <name>.temp<ext> first; original only replaced after validation.")
        add("bullet", "Validation checks codec, track count (unless intentionally changed), size window (lossy only), and success exit code.")
        add("bullet", "Lossless DTS-HD variants skip the size variance requirement (heuristic keywords: MA / Master Audio / XLL / DTS:X).")

        add("h2", "Typical Workflows")
        add("bullet", "Audit Library: List DTS (no DD) -> Dry Run -> Review output -> Live Convert.")
        add("bullet", "Scriptable Batch: Dry Run + Batch -> Inspect generated .bat -> Run batch manually in controlled window.")
        add("bullet", "After Fixing Issues: Reverify BAD with an adjusted percent if needed (e.g. 25 or 35).")
        add("bullet", "Periodic Maintenance: Clean Temps to finalize or quarantine leftover temp encodes.")

        add("h2", "CLI Equivalents")
        add("mono", "List DTS (no DD):  python DTStoDDPlus.py <dir> --list-dts-no-dd --filter <pattern>")
        add("mono", "Dry Run:         python DTStoDDPlus.py <dir> --dry-run --filter <pattern>")
        add("mono", "Dry+Batch:       python DTStoDDPlus.py <dir> --dry-run-batch out.bat --filter <pattern>")
        add("mono", "Live Convert:    python DTStoDDPlus.py <dir> --filter <pattern>")
        add("mono", "Reverify BAD:    python DTStoDDPlus.py <dir> --reverify-bad-convert <pct>")
        add("mono", "Clean Temps:     python DTStoDDPlus.py <dir> --clean-temp-files")

        add("h2", "Notes")
        add("note", "Batch file emission implies a dry run; it never mutates media. You must run the batch manually.")
        add("note", "Cancel does not forcibly kill child processes on all platforms; if ffmpeg hangs you may need manual termination.")
        add("note", "Reverify percent may be given with or without % sign (e.g. 25 or 25%).")

        txt.configure(state=tk.DISABLED)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, padx=8, pady=(4, 8))

        def copy_all():
            try:
                self.clipboard_clear()
                self.clipboard_append(txt.get("1.0", tk.END).strip())
            except Exception:
                pass

        ttk.Button(btn_frame, text="Copy Text", command=copy_all).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Close", command=win.destroy).pack(side=tk.RIGHT)

        win.focus_set()

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About DTStoDDPlus GUI",
            "GUI wrapper for DTStoDDPlus.py\n"
            "Adds convenience for browsing, dry running, batch generation, live conversion, and maintenance modes.\n"
            "Hover over controls or open Help > Button Help for details.",
        )

    # ---------------- Tooltip Support ----------------
    def _attach_tooltips(self, frm_top: ttk.Frame) -> None:  # type: ignore[override]
        # Lazy import style (already in stdlib) - define tooltip helper once
        if not hasattr(self, "_tooltip_instances"):
            self._tooltip_instances = []  # type: ignore[attr-defined]

        def tip(w, text):
            self._tooltip_instances.append(Tooltip(w, text=text))  # type: ignore[attr-defined]

        children = frm_top.winfo_children()
        # Assuming stable ordering as created above
        tip(children[1], "Root directory scanned recursively for video files.")
        tip(children[3], "Filename glob filter (fnmatch). Examples: *.mkv | Show*S01* | *.")
        tip(children[5], "Percent window used only by Reverify BAD (e.g. 25 = +/-25%).")
        # Modified days entry is at index after label (label index maybe 8?) find entry with current value
        for c in children:
            if isinstance(c, ttk.Entry) and c.get() == self.modified_days_var.get():
                tip(c, "Only include files modified within last N days (default 30; 0 disables).")
                break
        # Buttons
        tip(self.btn_list, "List English DTS with no Dolby track (read-only).")
        tip(self.btn_dry, "Dry run; show conversions without changes.")
        tip(self.btn_batch, "Dry run + generate batch file.")
        tip(self.btn_live, "Live safeguarded conversion (temp + validation).")
        tip(self.btn_reverify, "Re-check BAD_CONVERT files using size variance.")
        tip(self.btn_clean, "Promote or relabel lingering .temp files.")
        tip(self.btn_cancel, "Terminate running process (best-effort).")
        tip(self.btn_clear, "Clear output log.")

    # ---------------- Existing Helpers ----------------

    # ---------------- Helpers ----------------
    def _append_line(self, line: str) -> None:
        self.txt.configure(state=tk.NORMAL)
        self.txt.insert(tk.END, line)
        self.txt.see(tk.END)
        self.txt.configure(state=tk.DISABLED)

    def _choose_dir(self) -> None:
        sel = filedialog.askdirectory(title="Select root directory")
        if sel:
            self.dir_var.set(sel)

    def _choose_batch_file(self) -> Path | None:
        file = filedialog.asksaveasfilename(
            title="Choose batch file to create",
            defaultextension=".bat",
            filetypes=[("Batch Files", "*.bat"), ("All Files", "*.*")],
            initialfile="ddpconvert.bat",
        )
        return Path(file) if file else None

    def _validate_directory(self) -> Path | None:
        d = Path(self.dir_var.get().strip())
        if not d.exists() or not d.is_dir():
            messagebox.showerror("Invalid Directory", f"Directory does not exist:\n{d}")
            return None
        return d

    def _disable_actions(self, running: bool) -> None:
        widgets = [
            self.btn_list,
            self.btn_dry,
            self.btn_batch,
            self.btn_live,
            self.btn_reverify,
            self.btn_clean,
        ]
        for w in widgets:
            w.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.btn_cancel.configure(state=tk.NORMAL if running else tk.DISABLED)
        # Clear Output remains enabled always

    def _clear_output(self) -> None:
        """Clear the contents of the output text widget."""
        try:
            self.txt.configure(state=tk.NORMAL)
            self.txt.delete("1.0", tk.END)
            self.txt.insert(tk.END, "(Output cleared)\n")
            self.txt.configure(state=tk.DISABLED)
        except Exception:
            pass

    # ---------------- Persistence ----------------
    def _state_file(self) -> Path:
        return Path(__file__).with_name(STATE_FILE_NAME)

    def _load_state(self) -> dict:
        path = self._state_file()
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception:
            pass
        return {}

    def _save_state(self) -> None:  # type: ignore[override]
        path = self._state_file()
        data = {
            "directory": self.dir_var.get().strip(),
            "filter": self.filter_var.get().strip() or "*",
            "modified_within_days": self.modified_days_var.get().strip() or DEFAULT_MODIFIED_DAYS,
        }
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _on_close(self) -> None:
        # Ensure latest state saved then close
        try:
            self._save_state()
        finally:
            self.destroy()

    # ---------------- Command Builders ----------------
    def _base_args(self) -> list[str]:
        script_path = Path(__file__).with_name(SCRIPT_NAME)
        return [sys.executable, "-u", str(script_path)]

    def _common(self) -> tuple[Path, str, str]:
        d = self._validate_directory()
        if d is None:
            raise RuntimeError("Invalid directory")
        pattern = self.filter_var.get().strip() or "*"
        raw = self.modified_days_var.get().strip() or str(DEFAULT_MODIFIED_DAYS)
        try:
            int(raw)
        except ValueError:
            messagebox.showerror("Invalid Value", f"Modified ≤ Days must be integer (got: {raw})")
            raise RuntimeError("Invalid modified days")
        return d, pattern, raw

    # ---------------- Button Actions ----------------
    def _do_list(self) -> None:
        try:
            directory, pattern, modified = self._common()
        except RuntimeError:
            return
        args = self._base_args() + [str(directory), "--list-dts-no-dd", "--filter", pattern, "--modified-within-days", modified]
        self._run(args, f"Listing English DTS without Dolby in: {directory}\n")

    def _do_dry_run(self) -> None:
        try:
            directory, pattern, modified = self._common()
        except RuntimeError:
            return
        args = self._base_args() + [str(directory), "--dry-run", "--filter", pattern, "--modified-within-days", modified]
        self._run(args, f"Dry run starting for: {directory}\n")

    def _do_batch(self) -> None:
        try:
            directory, pattern, modified = self._common()
        except RuntimeError:
            return
        batch_file = self._choose_batch_file()
        if not batch_file:
            return
        args = self._base_args() + [str(directory), "--dry-run-batch", str(batch_file), "--filter", pattern, "--modified-within-days", modified]
        self._run(args, f"Dry run + batch generation to {batch_file}\n")

    def _do_live(self) -> None:
        try:
            directory, pattern, modified = self._common()
        except RuntimeError:
            return
        if not messagebox.askyesno("Confirm Live Conversion", "Proceed with LIVE conversion (files will be modified after safeguards)?"):
            return
        args = self._base_args() + [str(directory), "--filter", pattern, "--modified-within-days", modified]
        self._run(args, f"Live conversion starting for: {directory}\n")

    def _do_reverify(self) -> None:
        try:
            directory, _, modified = self._common()
        except RuntimeError:
            return
        val = self.reverify_var.get().strip()
        if not val:
            messagebox.showerror("Missing Percent", "Enter a reverify percent (e.g. 25)")
            return
        args = self._base_args() + [str(directory), "--reverify-bad-convert", val, "--modified-within-days", modified]
        self._run(args, f"Reverify BAD_CONVERT files at +/-{val}% variance\n")

    def _do_clean(self) -> None:
        try:
            directory, _, modified = self._common()
        except RuntimeError:
            return
        args = self._base_args() + [str(directory), "--clean-temp-files", "--modified-within-days", modified]
        self._run(args, f"Cleaning temp files in: {directory}\n")

    # ---------------- Subprocess Handling ----------------
    def _run(self, args: list[str], header: str) -> None:
        if self._proc is not None:
            messagebox.showwarning("Busy", "A process is already running.")
            return
        self._append_line("\n=== COMMAND START =====================================\n")
        self._append_line(header)
        self._append_line("Command: " + " ".join(args) + "\n\n")
        self._stop_flag.clear()
        self._disable_actions(True)
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            self._append_line(f"Failed to start process: {e}\n")
            self._proc = None
            self._disable_actions(False)
            return

        def reader():
            assert self._proc is not None
            for line in self._proc.stdout:  # type: ignore[attr-defined]
                if self._stop_flag.is_set():
                    break
                self._queue.put(line)
            self._proc.wait()
            self._queue.put(f"\n(Process exited with code {self._proc.returncode})\n")
            self._queue.put("=== COMMAND END =======================================\n")
            # Mark completion
            self._queue.put("__PROCESS_DONE__")

        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()

    def _poll_queue(self):
        try:
            while True:
                line = self._queue.get_nowait()
                if line == "__PROCESS_DONE__":
                    self._proc = None
                    self._disable_actions(False)
                    continue
                self._append_line(line)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _cancel_process(self) -> None:
        if self._proc is None:
            return
        if messagebox.askyesno("Cancel", "Terminate the running process?"):
            self._stop_flag.set()
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._append_line("\nProcess termination requested...\n")


def main() -> int:
    app = DTSGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


