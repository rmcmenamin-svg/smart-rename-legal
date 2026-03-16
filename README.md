# Smart Legal PDF Renamer

Hazel-compatible CLI that automatically renames legal documents with structured filenames using AI classification and court record verification.

**Pipeline:** OCR → Claude Classify → CourtListener Verify → Claude Refine → Rename

## Output Format

```
YYYY-MM-DD - S.D.N.Y. - Smith v. Jones Corp - 1:24-cv-01234 - Motion to Dismiss.pdf
```

## Setup

```bash
pip install anthropic
# Optional: brew install poppler (for pdftotext)
# Optional: pip install PyPDF2 (fallback PDF reader)

cp .env.example .env
# Edit .env with your API keys
```

## Usage

```bash
# Direct
export ANTHROPIC_API_KEY=sk-ant-...
./smart_rename_legal.py /path/to/document.pdf

# With .env file
source .env && ./smart_rename_legal.py /path/to/document.pdf
```

### Hazel Integration

1. Create a Hazel rule matching `*.pdf` in your target folder
2. Add action: **Run shell script**
3. Script: `source ~/.env && /path/to/smart_rename_legal.py "$1"`
4. Files matching `YYYY-MM-DD - *` are automatically skipped (already renamed)

## How It Works

1. **OCR** — Extracts text via `pdftotext` (poppler) or PyPDF2 fallback. Supports `.docx` too.
2. **Date Extraction** — Finds filed/entered dates, ISO dates, or month-name dates. Falls back to `UNDATED`.
3. **Classification** — Claude identifies litigation vs. non-litigation, extracts docket number, case name, and document type.
4. **CourtListener Verification** — Searches CourtListener to verify and enrich case metadata (court, canonical case name, docket).
5. **Title Refinement** — Claude generates a clean document title (e.g., "Motion to Dismiss for Failure to State a Claim").
6. **Rename** — Builds structured filename with collision handling.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `CL_TOKEN` | No | CourtListener API token — enables case verification |

## License

MIT
