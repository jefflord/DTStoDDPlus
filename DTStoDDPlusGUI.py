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

import sys
import subprocess
import threading
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText


SCRIPT_NAME = "DTStoDDPlus.py"  # Assumed to be in same directory


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

    # ---------------- UI Construction ----------------
    def _build_ui(self) -> None:
        pad = 6
        frm_top = ttk.Frame(self)
        frm_top.pack(side=tk.TOP, fill=tk.X, padx=pad, pady=(pad, 0))

        # Directory selection
        ttk.Label(frm_top, text="Root Directory:").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar()
        ent_dir = ttk.Entry(frm_top, textvariable=self.dir_var, width=70)
        ent_dir.grid(row=0, column=1, sticky="we", padx=(0, pad))
        ttk.Button(frm_top, text="Browse...", command=self._choose_dir).grid(
            row=0, column=2, sticky="we"
        )

        # Filter pattern
        ttk.Label(frm_top, text="Filter Pattern:").grid(row=1, column=0, sticky="w")
        self.filter_var = tk.StringVar(value="*")
        ttk.Entry(frm_top, textvariable=self.filter_var, width=20).grid(
            row=1, column=1, sticky="w", padx=(0, pad)
        )

        # Reverify variance percent
        ttk.Label(frm_top, text="Reverify %:").grid(row=1, column=2, sticky="e")
        self.reverify_var = tk.StringVar(value="25")
        ttk.Entry(frm_top, textvariable=self.reverify_var, width=6).grid(
            row=1, column=3, sticky="w"
        )

        frm_buttons = ttk.Frame(self)
        frm_buttons.pack(side=tk.TOP, fill=tk.X, padx=pad, pady=(pad, 0))

        # Action buttons
        self.btn_list = ttk.Button(
            frm_buttons, text="List DTS (no DD)", command=self._do_list
        )
        self.btn_dry = ttk.Button(
            frm_buttons, text="Dry Run", command=self._do_dry_run
        )
        self.btn_batch = ttk.Button(
            frm_buttons, text="Dry Run + Batch", command=self._do_batch
        )
        self.btn_live = ttk.Button(
            frm_buttons, text="Live Convert", command=self._do_live
        )
        self.btn_reverify = ttk.Button(
            frm_buttons, text="Reverify BAD", command=self._do_reverify
        )
        self.btn_clean = ttk.Button(
            frm_buttons, text="Clean Temps", command=self._do_clean
        )
        self.btn_cancel = ttk.Button(
            frm_buttons, text="Cancel", command=self._cancel_process, state=tk.DISABLED
        )

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
            ]
        ):
            b.grid(row=0, column=i, padx=(0 if i == 0 else 4, 0), pady=2, sticky="we")
        for i in range(7):
            frm_buttons.grid_columnconfigure(i, weight=1)

        # Output area
        frm_out = ttk.Frame(self)
        frm_out.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)
        self.txt = ScrolledText(frm_out, wrap="word", font=("Consolas", 9))
        self.txt.pack(fill=tk.BOTH, expand=True)
        self._append_line("DTStoDDPlus GUI ready. Select a directory and choose an action.\n")

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

    # ---------------- Command Builders ----------------
    def _base_args(self) -> list[str]:
        script_path = Path(__file__).with_name(SCRIPT_NAME)
        return [sys.executable, "-u", str(script_path)]

    def _common(self) -> tuple[Path, str]:
        d = self._validate_directory()
        if d is None:
            raise RuntimeError("Invalid directory")
        pattern = self.filter_var.get().strip() or "*"
        return d, pattern

    # ---------------- Button Actions ----------------
    def _do_list(self) -> None:
        try:
            directory, pattern = self._common()
        except RuntimeError:
            return
        args = self._base_args() + [str(directory), "--list-dts-no-dd", "--filter", pattern]
        self._run(args, f"Listing English DTS without Dolby in: {directory}\n")

    def _do_dry_run(self) -> None:
        try:
            directory, pattern = self._common()
        except RuntimeError:
            return
        args = self._base_args() + [str(directory), "--dry-run", "--filter", pattern]
        self._run(args, f"Dry run starting for: {directory}\n")

    def _do_batch(self) -> None:
        try:
            directory, pattern = self._common()
        except RuntimeError:
            return
        batch_file = self._choose_batch_file()
        if not batch_file:
            return
        args = self._base_args() + [
            str(directory),
            "--dry-run-batch",
            str(batch_file),
            "--filter",
            pattern,
        ]
        self._run(args, f"Dry run + batch generation to {batch_file}\n")

    def _do_live(self) -> None:
        try:
            directory, pattern = self._common()
        except RuntimeError:
            return
        if not messagebox.askyesno(
            "Confirm Live Conversion",
            "Proceed with LIVE conversion (files will be modified after safeguards)?",
        ):
            return
        args = self._base_args() + [str(directory), "--filter", pattern]
        self._run(args, f"Live conversion starting for: {directory}\n")

    def _do_reverify(self) -> None:
        try:
            directory, _ = self._common()
        except RuntimeError:
            return
        val = self.reverify_var.get().strip()
        if not val:
            messagebox.showerror("Missing Percent", "Enter a reverify percent (e.g. 25)")
            return
        args = self._base_args() + [str(directory), "--reverify-bad-convert", val]
        self._run(args, f"Reverify BAD_CONVERT files at +/-{val}% variance\n")

    def _do_clean(self) -> None:
        try:
            directory, _ = self._common()
        except RuntimeError:
            return
        args = self._base_args() + [str(directory), "--clean-temp-files"]
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
