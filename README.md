# CNC Pack Anonymizer

A small tool for machine shops that share CNC data packs (CAD, CAM, G-code,
drawings): it makes a **cleaned copy** of a part folder with customer names,
part numbers, project names and computer paths removed, and tells you exactly
what still needs a manual fix.

It exists for **your** protection — so you can be confident nothing about
your customers travels with the files you share. The receiving side runs its
own final check as well, so you always have a second safety net.

- **Fully offline.** No internet is used; no file ever leaves your machine.
- **Originals untouched.** Everything is written to a new `_CLEAN` copy.
- **Machining data is never modified.** G-code is edited only inside `( )`
  comments; STEP models only inside label text. Every edited file is
  verified byte-for-byte identical in its machining content — if that check
  fails, the original is copied unchanged and flagged. Cleaned programs run
  exactly like the originals.

## Setup (once, ~5 minutes)

1. **Install Python 3** (free): <https://www.python.org/downloads/> — tick
   **"Add Python to PATH"** during install.
2. **Download this tool**: click the green **Code** button above → *Download
   ZIP*, and unzip it anywhere, e.g. `D:\tools\`.
3. **Add the terms list**: you received a `terms.txt` file separately —
   place it in the same folder as `anonymize_pack.py`. (It's the private
   list of names to remove. `terms.example.txt` shows the format.)

## Cleaning a part (every time)

**Easiest:** double-click **`Clean a Part Folder.bat`** — a simple window
opens with three steps: choose the part folder, type a neutral name
(`housing-01`, `bracket-02`, …), click **Clean the pack**. You can also just
drag a part folder onto the `.bat` file.

You get, next to your part folder:

| output | what it is |
|---|---|
| `housing-01_CLEAN\` | the cleaned pack — **the only thing you send** |
| `housing-01_report.txt` | what changed + a `[MANUAL]` list of what to fix by hand |
| `housing-01_rename_map.csv` | old→new names, for your records — **keep private** |

Command line, if you prefer:

```
py -3 anonymize_pack.py "D:\jobs\my part folder" --name housing-01
```

## The manual part (no tool can do these)

- **Drawing PDFs** — redact the title block, logo and customer notes by hand
  (draw filled boxes in any PDF editor and re-save), or leave the drawing
  out. Logos and scanned images can't be detected automatically — always
  flip through every page.
- **CAD/CAM files** (`.mcam`, `.f3d`, …) — these store the job name and
  folder paths internally. Open the cleaned copy in your CAM software,
  rename the job to the neutral name, and **Save As** under the new name.
  The report tells you when identifiers were found inside.

## Before you hit send — 30 seconds

1. The report says **READY TO SEND** (or every `[MANUAL]` item is done).
2. Open two or three files in `_CLEAN` and glance at the headers.
3. Flip through every drawing page one last time.
4. Send **only** the `_CLEAN` folder — the report, the map and `terms.txt`
   stay with you.
