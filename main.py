"""Minimal Tkinter UI for Advertisatron-3000.

Workflow: choose a PDF, pick the publication, add any inserts (advertiser +
number of pages), then Calculate to get the combined advertising percentage.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import analyzer
import inserts_data


class App:
    def __init__(self, root):
        self.root = root
        root.title("Advertisatron-3000")
        root.geometry("520x620")
        root.minsize(460, 560)

        self.pdf_path = None
        self.insert_rows = []  # list of dicts: {frame, name_var, pages_var}
        self.special_rows = []  # list of dicts: {frame, pdf_path, label}

        tk.Label(
            root, text="Advertisatron-3000", font=("Segoe UI", 16, "bold")
        ).pack(pady=(18, 2))
        tk.Label(
            root,
            text="Estimate a newspaper's advertising coverage, including inserts.",
            font=("Segoe UI", 9),
            fg="#555555",
            wraplength=460,
        ).pack(pady=(0, 12))

        # --- Step 1: PDF ------------------------------------------------------
        self.pdf_button = tk.Button(
            root, text="Choose PDF…", font=("Segoe UI", 11), width=16,
            command=self.on_choose_pdf,
        )
        self.pdf_button.pack(pady=(0, 2))
        self.pdf_label = tk.Label(
            root, text="No PDF selected", font=("Segoe UI", 8), fg="#888888"
        )
        self.pdf_label.pack(pady=(0, 12))

        # --- Publication (hidden until the first "Add Insert +" click) -------
        self.pub_frame = tk.Frame(root)
        tk.Label(
            self.pub_frame, text="Publication", font=("Segoe UI", 9, "bold")
        ).pack()
        self.pub_var = tk.StringVar()
        self.pub_combo = ttk.Combobox(
            self.pub_frame, textvariable=self.pub_var, state="readonly", width=38,
            values=list(inserts_data.PUBLICATIONS.keys()),
        )
        self.pub_combo.pack(pady=(2, 0))
        self.pub_combo.bind("<<ComboboxSelected>>", self.on_pub_change)
        self.pub_revealed = False

        # --- Inserts ----------------------------------------------------------
        self.inserts_header = tk.Label(
            root, text="Inserts", font=("Segoe UI", 9, "bold")
        )
        self.inserts_header.pack()
        self.inserts_container = tk.Frame(root)
        self.inserts_container.pack(fill="x", padx=30)
        self.add_insert_btn = tk.Button(
            root, text="Add Insert +", font=("Segoe UI", 9),
            command=self.add_insert_row,
        )
        self.add_insert_btn.pack(pady=(4, 14))

        # --- Special Sections -------------------------------------------------
        # Unlike inserts, a special section is a PDF that is analyzed for ad
        # coverage like the paper (not counted as 100% ad); its size is derived
        # from the PDF.
        self.special_header = tk.Label(
            root, text="Special Sections", font=("Segoe UI", 9, "bold")
        )
        self.special_header.pack()
        self.special_container = tk.Frame(root)
        self.special_container.pack(fill="x", padx=30)
        self.add_special_btn = tk.Button(
            root, text="Add Special Section +", font=("Segoe UI", 9),
            command=self.on_add_special_section,
        )
        self.add_special_btn.pack(pady=(4, 14))

        # --- Weight converter (standalone; oz → % of a pound) ----------------
        self.oz_header = tk.Label(
            root, text="Weight Calculation", font=("Segoe UI", 9, "bold")
        )
        self.oz_header.pack()
        oz_row = tk.Frame(root)
        oz_row.pack(pady=(2, 0))
        self.oz_var = tk.StringVar()
        self.oz_var.trace_add("write", self._on_oz_change)
        tk.Entry(oz_row, textvariable=self.oz_var, width=8).pack(side="left")
        tk.Label(oz_row, text="oz", font=("Segoe UI", 9)).pack(side="left", padx=(4, 0))
        self.oz_result = tk.Label(
            root, text="", font=("Segoe UI", 12, "bold"), fg="#222222"
        )
        self.oz_result.pack(pady=(2, 14))

        # --- Step 4: Calculate + result --------------------------------------
        self.calc_button = tk.Button(
            root, text="Calculate", font=("Segoe UI", 11, "bold"), width=16,
            command=self.on_calculate,
        )
        self.calc_button.pack(pady=(0, 10))

        self.result = tk.Label(
            root, text="", font=("Segoe UI", 20, "bold"), fg="#1a7f37"
        )
        self.result.pack(pady=(4, 2))
        self.status = tk.Label(
            root, text="", font=("Segoe UI", 9), fg="#555555",
            wraplength=460, justify="center",
        )
        self.status.pack(pady=(0, 8))

        # --- Version ----------------------------------------------------------
        tk.Label(
            root, text=f"v{analyzer.__version__}", font=("Segoe UI", 11),
            fg="#555555",
        ).pack(side="bottom", pady=4)

    # --- UI helpers (main thread) --------------------------------------------
    def set_result(self, text):
        self.result.config(text=text)

    def set_status(self, text):
        self.status.config(text=text)

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.pdf_button.config(state=state)
        self.calc_button.config(state=state)
        self.add_insert_btn.config(state=state)
        self.add_special_btn.config(state=state)

    # --- weight converter -----------------------------------------------------
    def _on_oz_change(self, *_):
        text = self.oz_var.get().strip()
        if not text:
            self.oz_result.config(text="")
            return
        try:
            oz = float(text)
        except ValueError:
            self.oz_result.config(text="Enter a number of ounces.")
            return
        fraction = oz / 16.0
        self.oz_result.config(
            text=f"{oz:g} oz = {fraction:.4f}"
        )

    # --- inserts --------------------------------------------------------------
    def _current_insert_options(self):
        """Dropdown labels for the selected publication (size disambiguates dupes)."""
        pub = self.pub_var.get()
        if not pub:
            return [], {}
        labels = []
        label_to_size = {}
        for ins in inserts_data.PUBLICATIONS[pub]["inserts"]:
            label = f"{ins['name']} — {ins['size_in2']:g} in²"
            labels.append(label)
            label_to_size[label] = ins["size_in2"]
        return labels, label_to_size

    def on_pub_change(self, _event=None):
        # Publication changed: repopulate each row's advertiser list and clear the
        # previous selection (sizes differ between publications).
        labels, _ = self._current_insert_options()
        for row in self.insert_rows:
            row["name_var"].set("")
            row["combo"]["values"] = labels

    def _reveal_pub(self):
        # A publication is required to price inserts and size special sections;
        # reveal the picker as soon as either kind of row is added.
        if not self.pub_revealed:
            self.pub_frame.pack(before=self.inserts_header, pady=(0, 12))
            self.pub_revealed = True

    def _maybe_hide_pub(self):
        # Back to the initial state once there are no inserts and no special
        # sections left.
        if not self.insert_rows and not self.special_rows and self.pub_revealed:
            self.pub_frame.pack_forget()
            self.pub_revealed = False

    def add_insert_row(self):
        self._reveal_pub()

        labels, _ = self._current_insert_options()
        frame = tk.Frame(self.inserts_container)
        name_var = tk.StringVar()
        combo = ttk.Combobox(
            frame, textvariable=name_var, state="readonly", width=28, values=labels
        )
        combo.pack(side="left")
        pages_var = tk.IntVar(value=1)
        tk.Label(frame, text="pages:", font=("Segoe UI", 8)).pack(side="left", padx=(6, 2))
        tk.Spinbox(frame, from_=1, to=999, width=4, textvariable=pages_var).pack(side="left")
        row = {"frame": frame, "name_var": name_var, "pages_var": pages_var, "combo": combo}
        tk.Button(
            frame, text="✕", font=("Segoe UI", 8), fg="#a00", width=2,
            command=lambda: self._remove_row(row),
        ).pack(side="left", padx=(6, 0))
        frame.pack(fill="x", pady=2)
        self.insert_rows.append(row)

    def _remove_row(self, row):
        row["frame"].destroy()
        self.insert_rows.remove(row)
        self._maybe_hide_pub()

    # --- special sections -----------------------------------------------------
    def on_add_special_section(self):
        path = filedialog.askopenfilename(
            title="Select a special section PDF", filetypes=[("PDF files", "*.pdf")]
        )
        if not path:
            return
        self._reveal_pub()

        frame = tk.Frame(self.special_container)
        label = tk.Label(
            frame, text=os.path.basename(path), font=("Segoe UI", 8),
            fg="#333333", anchor="w",
        )
        label.pack(side="left")
        row = {"frame": frame, "pdf_path": path, "label": label}
        tk.Button(
            frame, text="✕", font=("Segoe UI", 8), fg="#a00", width=2,
            command=lambda: self._remove_special_row(row),
        ).pack(side="right", padx=(6, 0))
        frame.pack(fill="x", pady=2)
        self.special_rows.append(row)

    def _remove_special_row(self, row):
        row["frame"].destroy()
        self.special_rows.remove(row)
        self._maybe_hide_pub()

    def _collect_special_sections(self):
        """Return a list of {"name", "pdf_path"} for analysis."""
        return [
            {"name": os.path.splitext(row["label"].cget("text"))[0],
             "pdf_path": row["pdf_path"]}
            for row in self.special_rows
        ]

    def _collect_inserts(self):
        """Return (inserts_list, error_message)."""
        _, label_to_size = self._current_insert_options()
        inserts = []
        for row in self.insert_rows:
            label = row["name_var"].get()
            if not label:
                return None, "Please choose an advertiser for every insert row."
            try:
                pages = int(row["pages_var"].get())
            except (tk.TclError, ValueError):
                return None, "Insert page counts must be whole numbers."
            if pages < 1:
                return None, "Insert page counts must be at least 1."
            inserts.append({"size_in2": label_to_size[label], "pages": pages})
        return inserts, None

    # --- actions --------------------------------------------------------------
    def on_choose_pdf(self):
        path = filedialog.askopenfilename(
            title="Select a newspaper PDF", filetypes=[("PDF files", "*.pdf")]
        )
        if not path:
            return
        self.pdf_path = path
        self.pdf_label.config(text=os.path.basename(path), fg="#333333")

    def on_calculate(self):
        if not self.pdf_path:
            self.set_result("")
            self.set_status("Choose a PDF first.")
            return

        pub = self.pub_var.get()
        if (self.insert_rows or self.special_rows) and not pub:
            self.set_result("")
            self.set_status("Select a publication to price the inserts and special sections.")
            return
        inserts, err = self._collect_inserts()
        if err:
            self.set_result("")
            self.set_status(err)
            return
        special_sections = self._collect_special_sections()

        paper_area = (
            inserts_data.PUBLICATIONS[pub]["paper_size_in2"] if pub else None
        )

        self.set_busy(True)
        self.set_result("")
        self.set_status("Loading…")
        thread = threading.Thread(
            target=self._analyze,
            args=(self.pdf_path, paper_area, inserts, special_sections),
            daemon=True,
        )
        thread.start()

    def _analyze(self, path, paper_area, inserts, special_sections):
        def progress(page, total):
            self.root.after(0, self.set_status, f"Analyzing page {page} of {total}…")

        try:
            overall, _per_page, breakdown = analyzer.analyze_pdf(
                path,
                progress_cb=progress,
                paper_page_area_in2=paper_area,
                inserts=inserts,
                special_sections=special_sections,
            )
        except Exception as exc:  # surface any error in the UI, don't crash
            self.root.after(0, self.set_result, "")
            self.root.after(0, self.set_status, f"Error: {exc}")
        else:
            self.root.after(0, self.set_result, f"Advertising: {overall:.1f}%")
            self.root.after(0, self.set_status, self._format_breakdown(breakdown))
        finally:
            self.root.after(0, self.set_busy, False)

    @staticmethod
    def _format_breakdown(breakdown):
        """Human-readable multi-line summary of the combined percentage."""
        lines = []
        paper = breakdown["paper"]
        lines.append(f"Paper: {paper['ad_pct']:.1f}% ({paper['pages']} pp)")

        sections = breakdown.get("sections") or []
        if sections:
            parts = [
                f"{s['name']} {s['ad_pct']:.1f}% ({s['size_pages']:g} pp)"
                for s in sections
            ]
            lines.append("Special: " + " · ".join(parts))

        inserts = breakdown.get("inserts") or {}
        if inserts.get("count"):
            lines.append(
                f"Inserts: {inserts['count']} ({inserts['total_in2']:g} in², 100% ad)"
            )
        return "\n".join(lines)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
