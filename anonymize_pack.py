#!/usr/bin/env python3
"""
CNC Pack Anonymizer — Vision Lab vendor tool.

Makes a CLEANED COPY of a CNC part folder (CAD / CAM / G-code / docs) with
customer identifiers removed, and reports exactly what still needs manual
work. Originals are never modified. Runs fully offline — no file leaves
your machine.

The tool NEVER touches machining data: G-code is edited only inside ( )
comments, STEP models only inside quoted label text, and every edited file
is verified byte-for-byte identical in its machining content afterwards.
If verification ever fails, the original is copied unchanged and flagged.

USAGE
  Double-click (or run with no arguments)  ->  simple window, 3 steps
  Command line:
      python anonymize_pack.py "D:\\jobs\\part folder" --name housing-01
      python anonymize_pack.py "D:\\jobs\\part folder" --name housing-01 --terms D:\\tools\\terms.txt

TERMS FILE (terms.txt, kept NEXT TO this script)
  The list of names to remove. One entry per line:
      # comment lines start with #
      ACME CORP            <- plain text, case-insensitive, whole word
      re:ACM[-_ ]?\\d{3,}   <- lines starting with re: are regular expressions
  You receive this file separately - it is private. Add every customer name,
  project name, person name and part-number format used in your files.

OUTPUT (next to the input folder)
  <name>_CLEAN/            the cleaned pack  ->  the ONLY thing you send
  <name>_report.txt        what changed + [MANUAL] items still to fix
  <name>_rename_map.csv    old->new names  (KEEP PRIVATE, do not send)

Requires Python 3.8+ (python.org, tick "Add to PATH"). Nothing else.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

REPLACEMENT = "XXX"

TEXT_EXT = {".txt", ".rtf", ".csv", ".md"}
GCODE_EXT = {".nc", ".tap", ".mpf", ".min", ".cnc", ".prg", ".eia", ".gcode",
             ".ncf", ".pu", ".pim", ".pit", ".h", ".spf", ".dat", ".iso"}
STEP_EXT = {".step", ".stp"}
PDF_EXT = {".pdf"}
CAM_EXT = {".mcam", ".mcx-5", ".mcx", ".f3d", ".emcam", ".vnc", ".pmlprj",
           ".prt", ".catpart", ".catproduct", ".sldprt", ".sldasm", ".x_t",
           ".dwg"}
DXF_EXT = {".dxf"}

# ---------------------------------------------------------------------------
# terms
# ---------------------------------------------------------------------------


def load_terms(explicit_path=None):
    """Read terms.txt -> (literals, regexes, path-or-None)."""
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    here = Path(__file__).resolve().parent
    candidates += [here / "terms.txt", Path.cwd() / "terms.txt"]
    for cand in candidates:
        if cand.is_file():
            literals, regexes = [], []
            for line in cand.read_text(encoding="utf-8-sig",
                                       errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("re:"):
                    regexes.append(line[3:].strip())
                else:
                    literals.append(line)
            return literals, regexes, cand
    return [], [], None


def build_patterns(literals, regexes):
    pats = []
    for term in literals:
        esc = re.escape(term).replace(r"\ ", r"[\s_-]+")
        if len(term) <= 4:  # short codes need word boundaries ("ACE" != "brace")
            pats.append(re.compile(r"(?<![A-Za-z0-9])" + esc + r"(?![A-Za-z0-9])",
                                   re.IGNORECASE))
        else:
            pats.append(re.compile(esc, re.IGNORECASE))
    for pat in regexes:
        try:
            pats.append(re.compile(pat, re.IGNORECASE))
        except re.error as e:
            print(f"WARNING: bad regex in terms file skipped: {pat} ({e})")
    return pats


GENERIC_PATTERNS = [
    (re.compile(r"[A-Za-z]:\\[^\s()'\"]+"), "path-removed"),   # windows paths
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "email-removed"),
    (re.compile(r"(PROGRAMMED BY\s*[:=]\s*)\S.*", re.IGNORECASE), r"\1-"),
    (re.compile(r"(DRAWN|CHECKED|APPROVED|APPD|CHD|DRN)(\s*[:=]\s*)\S.*",
                re.IGNORECASE), r"\1\2-"),
]

# ---------------------------------------------------------------------------
# scrubbing engine
# ---------------------------------------------------------------------------


def scrub_text(text, patterns):
    hits = 0
    for pat in patterns:
        text, n = pat.subn(REPLACEMENT, text)
        hits += n
    for pat, repl in GENERIC_PATTERNS:
        text, n = pat.subn(repl, text)
        hits += n
    text = re.sub(REPLACEMENT + r"(?:[\s_\-/]*" + REPLACEMENT + r")+",
                  REPLACEMENT, text)
    return text, hits


NC_COMMENT = re.compile(r"\(([^)]*)\)|;(.*)$", re.MULTILINE)
STEP_STRING = re.compile(r"'((?:[^']|'')*)'")


def _scrub_spans(text, spans_regex, patterns):
    """Scrub identifiers ONLY inside the given spans (comments / strings)."""
    hits = 0
    out, last = [], 0
    for m in spans_regex.finditer(text):
        if m.group(1) is not None:
            s, e = m.span(1)
        elif m.lastindex and m.lastindex >= 2 and m.group(2) is not None:
            s, e = m.span(2)
        else:
            continue
        cleaned, n = scrub_text(text[s:e], patterns)
        hits += n
        out.append(text[last:s])
        out.append(cleaned)
        last = e
    out.append(text[last:])
    return "".join(out), hits


def scrub_gcode(text, patterns):
    cleaned, hits = _scrub_spans(text, NC_COMMENT, patterns)
    blank = lambda t: NC_COMMENT.sub("", t)
    if blank(cleaned) != blank(text):
        raise ValueError("machining code changed - file left unmodified")
    return cleaned, hits


def scrub_step(text, patterns):
    cleaned, hits = _scrub_spans(text, STEP_STRING, patterns)
    blank = lambda t: STEP_STRING.sub("''", t)
    if blank(cleaned) != blank(text):
        raise ValueError("geometry changed - file left unmodified")
    return cleaned, hits


def scan_bytes(data, patterns):
    """Scan a binary blob (ascii + utf-16le views) for identifiers."""
    found = set()
    views = [data.decode("latin-1", "ignore")]
    try:
        views.append(data.decode("utf-16-le", "ignore"))
    except Exception:
        pass
    for view in views:
        for pat in patterns:
            for m in pat.finditer(view):
                s = m.group(0).strip()
                if s:
                    found.add(s if len(s) <= 40 else s[:40] + "...")
    return sorted(found)

# ---------------------------------------------------------------------------
# filenames
# ---------------------------------------------------------------------------

SETUP_TOKEN = re.compile(
    r"(1ST|2ND|3RD|4TH|5TH|6TH|SETUP[\s_-]?\d+|OP[\s_-]?\d+|REWORK|DRAWING|"
    r"INFO|FIXTURE|MODEL)", re.IGNORECASE)
ROLE_BY_EXT = {".pdf": "drawing", ".step": "model", ".stp": "model",
               ".dxf": "drawing", ".mcam": "cam", ".mcx-5": "cam",
               ".mcx": "cam", ".f3d": "cam", ".pmlprj": "cam"}


def new_filename(old, name, patterns, counters):
    stem, ext = Path(old).stem, Path(old).suffix
    scrubbed = stem
    for pat in patterns:
        scrubbed = pat.sub(" ", scrubbed)
    for pat, _ in GENERIC_PATTERNS[:2]:
        scrubbed = pat.sub(" ", scrubbed)
    if scrubbed == stem:
        return old, False
    scrubbed = unicodedata.normalize("NFKD", scrubbed)
    tokens = [t.upper().replace(" ", "").replace("-", "_")
              for t in dict.fromkeys(SETUP_TOKEN.findall(stem))]
    pieces = [p for p in re.split(r"[\s_\-]+", scrubbed)
              if len(p) > 2 or SETUP_TOKEN.fullmatch(p)]
    body = "_".join(pieces)
    if tokens:
        tok = "_".join(tokens)
        if not body or set(pieces) <= set(tokens):
            body = tok
        elif tok.lower() not in body.lower():
            body = f"{body}_{tok}"
    if not body:
        role = ROLE_BY_EXT.get(ext.lower(), "file")
        counters[role] = counters.get(role, 0) + 1
        body = f"{role}{counters[role]:02d}" if counters[role] > 1 or \
            role == "file" else role
    return f"{name}_{body}{ext}".replace("__", "_"), True

# ---------------------------------------------------------------------------
# pack processing
# ---------------------------------------------------------------------------


def process_pack(folder, name, terms_path=None):
    """Clean one part folder. Returns a result dict (never raises for
    per-file problems - they land in the report instead)."""
    src = Path(folder).expanduser().resolve()
    if not src.is_dir():
        raise SystemExit(f"ERROR: not a folder: {src}")
    name = re.sub(r"[^\w\-]+", "-", name).strip("-").lower()
    if not name:
        raise SystemExit("ERROR: please give a part name, e.g. housing-01")

    literals, regexes, terms_file = load_terms(terms_path)
    patterns = build_patterns(literals, regexes)

    out = src.parent / f"{name}_CLEAN"
    out.mkdir(exist_ok=True)
    counters = {}
    manual, changed, clean, rename_rows = [], [], [], []

    used = set()
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f.relative_to(src)
        newbase, renamed = new_filename(f.name, name, patterns, counters)
        # two different sources must never collapse onto one cleaned name
        stem, ext_ = os.path.splitext(newbase)
        k = 2
        while (str(rel.parent), newbase.lower()) in used:
            newbase = f"{stem}_{k}{ext_}"
            k += 1
        used.add((str(rel.parent), newbase.lower()))
        dest = out / rel.parent / newbase
        dest.parent.mkdir(parents=True, exist_ok=True)
        ext = f.suffix.lower()
        note = None
        raw = f.read_bytes()

        is_texty = (ext in TEXT_EXT or ext in STEP_EXT or ext in GCODE_EXT
                    or ext in DXF_EXT) and b"\x00" not in raw[:4096]
        if is_texty:
            text = raw.decode("latin-1")   # latin-1 round-trips every byte
            try:
                if ext in GCODE_EXT:
                    cleaned, hits = scrub_gcode(text, patterns)
                    guarantee = "machining code verified unchanged"
                elif ext in STEP_EXT:
                    cleaned, hits = scrub_step(text, patterns)
                    guarantee = "geometry verified unchanged"
                elif ext in DXF_EXT:
                    # DXF text values live on their own lines; whole-text scrub
                    # can touch geometry numbers, so scan + flag only.
                    dest.write_bytes(raw)
                    found = scan_bytes(raw, patterns)
                    if found:
                        manual.append((str(rel), "DXF drawing - identifiers "
                                       f"inside: {', '.join(found[:6])}. Edit "
                                       "the title block in CAD and re-export"))
                    cleaned, hits, guarantee = None, 0, None
                else:
                    cleaned, hits = scrub_text(text, patterns)
                    guarantee = None
            except ValueError as e:
                dest.write_bytes(raw)
                manual.append((str(rel), f"NOT scrubbed ({e}); original "
                               "copied - report this file to Vision Lab"))
                cleaned, hits = None, 0
            if cleaned is not None:
                dest.write_bytes(cleaned.encode("latin-1"))
                if hits:
                    what = f"{hits} identifier(s) scrubbed"
                    if guarantee:
                        what += f"; {guarantee}"
                    changed.append((str(rel), newbase, what))
                    note = "scrubbed"
        elif ext in PDF_EXT:
            dest.write_bytes(raw)
            found = scan_bytes(raw, patterns)
            what = (f"identifiers readable in file: {', '.join(found[:6])}. "
                    if found else "")
            manual.append((str(rel),
                           f"PDF - {what}title block, logo and notes need "
                           "MANUAL redaction (or leave the drawing out)"))
        elif ext in CAM_EXT:
            dest.write_bytes(raw)
            found = scan_bytes(raw, patterns)
            if found:
                manual.append((str(rel), "CAD/CAM file - identifiers inside: "
                               f"{', '.join(found[:6])}. Open it in your "
                               "software, remove them (job name / paths), "
                               "and Save As under the neutral name"))
            else:
                manual.append((str(rel), "CAD/CAM file - nothing readable "
                               "found, but these files store job names and "
                               "paths internally: open and Save As under "
                               "the neutral name to be safe"))
        else:
            dest.write_bytes(raw)
            found = scan_bytes(raw, patterns)
            if found:
                manual.append((str(rel), "unrecognized file type - "
                               f"identifiers found: {', '.join(found[:6])} "
                               "- fix manually"))
        if renamed:
            rename_rows.append((str(rel), str(dest.relative_to(out))))
            if not note:
                changed.append((str(rel), newbase, "renamed only"))
        elif not note and ext not in PDF_EXT and ext not in CAM_EXT:
            clean.append(str(rel))

    # report ----------------------------------------------------------------
    lines = [f"ANONYMIZATION REPORT - pack '{name}'",
             f"source : {src}", f"output : {out}"]
    if terms_file:
        lines.append(f"terms  : {len(literals) + len(regexes)} entries from "
                     f"{terms_file}")
    else:
        lines.append("terms  : !! NO terms.txt FOUND - only generic items "
                     "(paths, emails, programmer names) were removed. Put "
                     "terms.txt next to this script and run again.")
    lines.append("")
    lines.append(f"[CHANGED]  {len(changed)} file(s) renamed and/or scrubbed:")
    for old, new, what in changed:
        lines.append(f"  - {old}  ->  {new}   ({what})")
    lines += ["", f"[MANUAL]   {len(manual)} file(s) STILL NEED MANUAL WORK "
              "before sending:"]
    for relname, what in manual:
        lines.append(f"  ! {relname}: {what}")
    lines += ["", f"[CLEAN]    {len(clean)} file(s) copied unchanged (nothing "
              "found).", ""]
    ready = not manual and terms_file is not None
    lines.append("VERDICT: " + ("READY TO SEND." if ready else
                 "READY TO SEND after the [MANUAL] items above are done."
                 if terms_file else "NOT READY - terms.txt missing (see "
                 "above)."))
    lines += ["", "Reminder: logos and scanned images inside PDFs cannot be "
              "detected automatically - always eyeball every drawing page. "
              "Send ONLY the *_CLEAN folder; the report and rename map stay "
              "private."]
    report_path = src.parent / f"{name}_report.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    map_path = src.parent / f"{name}_rename_map.csv"
    with open(map_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["KEEP PRIVATE - do not send this file"])
        w.writerow(["original", "anonymized"])
        w.writerows(rename_rows)

    return {"report": "\n".join(lines), "out": out, "report_path": report_path,
            "map_path": map_path, "manual": manual, "ready": ready,
            "terms_file": terms_file}

# ---------------------------------------------------------------------------
# simple window (no arguments)
# ---------------------------------------------------------------------------


def open_folder(path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, font, messagebox, scrolledtext

    root = tk.Tk()
    root.title("Vision Lab - CNC Pack Cleaner")
    root.geometry("760x600")
    root.minsize(640, 480)
    base = font.nametofont("TkDefaultFont")
    base.configure(size=11)
    bold = base.copy()
    bold.configure(weight="bold", size=11)

    state = {"folder": None, "out": None}

    frm = tk.Frame(root, padx=16, pady=12)
    frm.pack(fill="both", expand=True)

    # terms status
    literals, regexes, terms_file = load_terms()
    n_terms = len(literals) + len(regexes)
    terms_lbl = tk.Label(frm, anchor="w", justify="left",
                         fg=("#0a7d32" if terms_file else "#b00020"),
                         text=(f"Terms list: {n_terms} entries loaded "
                               f"({terms_file.name})" if terms_file else
                               "Terms list MISSING - put terms.txt next to "
                               "this program, then restart it."))
    terms_lbl.pack(fill="x")

    # step 1
    row1 = tk.Frame(frm)
    row1.pack(fill="x", pady=(12, 4))
    tk.Label(row1, text="Step 1", font=bold, width=7,
             anchor="w").pack(side="left")
    folder_lbl = tk.Label(row1, text="no folder chosen yet", anchor="w",
                          fg="#555555")

    def choose():
        p = filedialog.askdirectory(title="Choose the part folder to clean")
        if p:
            state["folder"] = p
            folder_lbl.config(text=p, fg="#000000")

    tk.Button(row1, text="Choose part folder...",
              command=choose).pack(side="left")
    folder_lbl.pack(side="left", padx=10, fill="x", expand=True)

    # step 2
    row2 = tk.Frame(frm)
    row2.pack(fill="x", pady=4)
    tk.Label(row2, text="Step 2", font=bold, width=7,
             anchor="w").pack(side="left")
    tk.Label(row2, text="Neutral part name:").pack(side="left")
    name_var = tk.StringVar()
    tk.Entry(row2, textvariable=name_var, width=24).pack(side="left", padx=8)
    tk.Label(row2, text="e.g. housing-01, bracket-02",
             fg="#555555").pack(side="left")

    # results box
    box = scrolledtext.ScrolledText(frm, wrap="word", height=18,
                                    state="disabled")
    verdict_lbl = tk.Label(frm, text="", font=bold, anchor="w",
                           justify="left")

    def show(text, verdict, ok):
        box.config(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.config(state="disabled")
        verdict_lbl.config(text=verdict, fg="#0a7d32" if ok else "#b00020")

    # step 3
    row3 = tk.Frame(frm)
    row3.pack(fill="x", pady=(4, 8))
    tk.Label(row3, text="Step 3", font=bold, width=7,
             anchor="w").pack(side="left")

    def clean():
        if not state["folder"]:
            messagebox.showwarning("Choose a folder",
                                   "Step 1 first: choose the part folder.")
            return
        if not name_var.get().strip():
            messagebox.showwarning("Part name",
                                   "Step 2 first: give a neutral part name "
                                   "such as housing-01.")
            return
        root.config(cursor="watch")
        root.update_idletasks()
        try:
            res = process_pack(state["folder"], name_var.get().strip())
        except SystemExit as e:
            root.config(cursor="")
            messagebox.showerror("Problem", str(e))
            return
        except Exception as e:               # never die silently on a shop PC
            root.config(cursor="")
            messagebox.showerror("Unexpected problem",
                                 f"{type(e).__name__}: {e}")
            return
        root.config(cursor="")
        state["out"] = res["out"]
        n_manual = len(res["manual"])
        verdict = ("READY TO SEND - send the _CLEAN folder." if res["ready"]
                   else (f"{n_manual} file(s) need manual work first - see "
                         "the [MANUAL] list above." if res["terms_file"] else
                         "NOT READY - terms.txt is missing."))
        show(res["report"], verdict, res["ready"])
        open_btn.config(state="normal")

    tk.Button(row3, text="Clean the pack", font=bold,
              command=clean).pack(side="left")
    open_btn = tk.Button(row3, text="Open cleaned folder", state="disabled",
                         command=lambda: state["out"] and
                         open_folder(state["out"]))
    open_btn.pack(side="left", padx=10)

    verdict_lbl.pack(fill="x")
    box.pack(fill="both", expand=True, pady=(4, 0))
    root.mainloop()

# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) == 1:
        run_gui()
        return
    ap = argparse.ArgumentParser(description="Anonymize a CNC part folder.")
    ap.add_argument("folder", help="the part folder to clean")
    ap.add_argument("--name", required=True,
                    help="neutral part name, e.g. housing-01")
    ap.add_argument("--terms", help="path to terms.txt (default: next to "
                    "this script)")
    args = ap.parse_args()
    res = process_pack(args.folder, args.name, args.terms)
    print(res["report"])
    print(f"\nreport : {res['report_path']}\nmap    : {res['map_path']}")


if __name__ == "__main__":
    main()
