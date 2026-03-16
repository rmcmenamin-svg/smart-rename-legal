# Smart Legal PDF Renamer

Legal documents arrive with useless filenames. PACER gives you `gov.uscourts.nysd.12345.42.0.pdf`. Opposing counsel sends `FINAL_motion_v3 (2).pdf`. Your own downloads folder is a graveyard of `Document(1).pdf`.

This tool reads the document, figures out what it is, and renames it:

```
Before:  gov.uscourts.nysd.604271.48.0.pdf
After:   2024-08-15 - S.D.N.Y. - Smith v. Jones Corp - 1:24-cv-01234 - Motion to Dismiss for Failure to State a Claim.pdf

Before:  FINAL_motion_v3 (2).pdf
After:   2025-01-10 - E.D. Pa. - Acme Inc v. Widget Co - 2:25-cv-00789 - Opposition to Motion for Summary Judgment.pdf

Before:  scan0042.pdf
After:   2025-03-01 - Engagement Letter - Corporate Formation for NewCo LLC.pdf
```

It works by chaining OCR, Claude (for classification and title generation), and [CourtListener](https://www.courtlistener.com/) (for court record verification) into a single pipeline. Point it at a PDF or `.docx` and it does the rest.

## How It Works

```
PDF/DOCX → Extract Text → Extract Date → Claude Classification
                                              ↓
                                     Is it litigation?
                                    ╱                ╲
                                  Yes                 No
                                  ↓                    ↓
                          CourtListener         Build filename from
                          case lookup           doc type + summary
                                ↓
                        Verify case match
                        (docket + name)
                                ↓
                        Claude refines
                        document title
                                ↓
                        Build filename:
                        DATE - COURT - CASE - DOCKET - TITLE.ext
```

The CourtListener step is optional but valuable — it verifies the case name against actual court records and fills in the Bluebook-formatted court abbreviation (e.g., `S.D.N.Y.`, `N.D. Cal.`).

## Setup

```bash
pip install anthropic

# Optional but recommended for better PDF text extraction:
brew install poppler    # provides pdftotext
pip install PyPDF2      # fallback if poppler isn't available
```

Set your API keys:

```bash
cp .env.example .env
# Edit .env with your keys
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | [Claude API key](https://console.anthropic.com/) |
| `CL_TOKEN` | No | [CourtListener API token](https://www.courtlistener.com/help/api/) — enables case verification |

## Usage

```bash
source .env && ./smart_rename_legal.py /path/to/document.pdf
```

The script renames the file in place and prints the new path to stdout. Already-renamed files (starting with `YYYY-MM-DD - `) are skipped automatically.

### Automate with Hazel

[Hazel](https://www.noodlesoft.com/) is a macOS file automation tool. You can set it up to rename documents as they land in a folder:

1. Create a rule matching `*.pdf` in your target folder (e.g., Downloads, a PACER folder)
2. Add action: **Run shell script**
3. Script: `source ~/.env && /path/to/smart_rename_legal.py "$1"`

Every new PDF gets renamed automatically. Files that have already been processed are skipped.

## License

MIT
