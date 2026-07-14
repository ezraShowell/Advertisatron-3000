# Advertisatron-3000 (v1.2)

Windows desktop app that takes a newspaper PDF and reports the
percentage of the paper taken up by advertisements. Renders each page to an
image, asks the Claude Sonnet vision model to estimate that page's ad coverage,
and combines the results into an overall percentage.
It can also include inserts of different sizes, priced
from the publication data in `inserts_data.py`, and special sections
which are separate PDFs that are analyzed for ad coverage like the paper itself.

## For office use

-Download Advertisatron-3000.zip
-Extract Advertisatron-3000 folder to desktop
-Enter api key in config.ini
-Run Advertisatron-300.exe

## Setup

1. Install Python 3.11+ (make sure "Add Python to PATH" is checked).
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `config.example.ini` to `config.ini` and paste your Anthropic API key:
   ```ini
   [anthropic]
   api_key = sk-ant-...
   ```

## Run from source

```
python main.py
```

Workflow:
1. **Choose PDF…** — pick the newspaper PDF (this no longer auto-runs).
2. **Publication** — pick which paper it is (sets the real page size and the
   available inserts).
3. **Inserts** — click **Add Insert +** for each insert; choose the advertiser
   and the number of pages. Inserts are counted as 100% advertising.
4. **Special Sections** — click **Add Special Section +** and pick the section's
   PDF. Unlike inserts, a special section is **not** 100% ad: each of its pages
   is analyzed for ad coverage the same way the paper is. Its size is derived
   automatically from the PDF in half-page multiples (the largest page in the
   section is taken as one full paper page), so nothing needs to be typed in.
5. **Calculate** — analyzes the paper and every special section, then shows the
   combined advertising % with a breakdown of where it comes from.

You can skip steps 2–4 for a quick paper-only estimate.

## Insert data (`inserts_data.py`)

The list of publications and inserts is a snapshot generated from the Excel
report. To refresh it after editing the Excel:

```
python extract_inserts.py ["path\to\Advertising_Percentage_Report.xlsx"]
```

This regenerates `inserts_data.py` (defaults to the report on your Desktop).
Rebuild the .exe afterward so the snapshot ships with it.

## Build the .exe

```
pyinstaller --onefile --windowed --name Advertisatron-3000 main.py
```

The executable is written to `dist\Advertisatron-3000.exe`. Place your
`config.ini` (with your API key) in the **same folder** as the .exe, then
double-click to run — no Python installation required on the target machine.

## Notes

- One API call is made per page, so a 20-page paper makes 20 calls.
- The percentage is Claude's visual *estimate*, not a pixel-exact measurement.
- Insert areas come from the Excel (real square inches); combining in inches
  keeps the paper-vs-insert ratio independent of PDF scaling.
- Do not commit `config.ini` — it contains your API key.
