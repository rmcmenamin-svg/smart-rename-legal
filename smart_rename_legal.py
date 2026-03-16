#!/usr/bin/env python3
"""
Smart Legal PDF Renamer
Hazel-compatible CLI: OCR → Claude Classify → CourtListener Verify → Claude Refine → Rename

Usage: smart_rename_legal.py <filepath>
  - Renames the file in place with structured legal filename
  - Exit 0 = success, Exit 1 = error

Environment variables:
  ANTHROPIC_API_KEY   - Required. Claude API key.
  CL_TOKEN            - Optional. CourtListener API token for case verification.
"""

import sys
import os
import re
import json
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime

# --- CONFIG ---
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

BLUEBOOK_MAP = {
    "nysd": "S.D.N.Y.", "nyed": "E.D.N.Y.",
    "casd": "S.D. Cal.", "cacd": "C.D. Cal.", "cand": "N.D. Cal.",
    "dcd": "D.D.C.", "dc": "D.D.C.",
    "mad": "D. Mass.", "ilnd": "N.D. Ill.",
    "cod": "D. Colo.", "co": "D. Colo.",
    "pawd": "W.D. Pa.", "txsd": "S.D. Tex.",
    "vawd": "W.D. Va.", "vaed": "E.D. Va.",
    "njd": "D.N.J.", "paed": "E.D. Pa.",
    "flsd": "S.D. Fla.", "txnd": "N.D. Tex.",
    "ohnd": "N.D. Ohio", "gand": "N.D. Ga.",
}


def log(msg, file=None):
    prefix = f"[{os.path.basename(file)}] " if file else ""
    print(f"[RENAME] {prefix}{msg}", file=sys.stderr)


def get_api_key(name, required=True):
    """Read an API key from environment."""
    val = os.environ.get(name, "").strip()
    if not val and required:
        log(f"Missing required environment variable: {name}")
        sys.exit(1)
    return val


# ── 1. OCR ──────────────────────────────────────────────────────────────

def extract_text_from_docx(filepath, max_chars=5000):
    """Extract text from a .docx file."""
    try:
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(filepath) as z:
            with z.open('word/document.xml') as f:
                tree = ET.parse(f)
        texts = []
        for t in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
            if t.text:
                texts.append(t.text)
        return ' '.join(texts)[:max_chars]
    except Exception as e:
        log(f"DOCX extraction failed: {e}")
        return ""


def extract_text_from_pdf(filepath, max_pages=3):
    """Extract text using pdftotext (poppler), fallback to PyPDF2."""
    # Try pdftotext first (fast, good quality)
    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(max_pages), filepath, "-"],
            capture_output=True, text=True, timeout=30
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to PyPDF2
    try:
        import PyPDF2
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for i in range(min(max_pages, len(reader.pages))):
                page_text = reader.pages[i].extract_text()
                if page_text:
                    text += page_text + "\n"
            return text.strip()
    except Exception as e:
        log(f"PyPDF2 fallback failed: {e}")

    return ""


# ── 2. DATE EXTRACTION ──────────────────────────────────────────────────

def extract_best_date(text, filename):
    """Extract the most relevant date from text or filename."""
    # Try filed/entered dates first
    filed_pattern = r'(?i)(?:filed|entered|date)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
    m = re.search(filed_pattern, text)
    if m:
        d = normalize_date(m.group(1))
        if d:
            return d

    # Try ISO dates in text
    iso_pattern = r'(\d{4}-\d{2}-\d{2})'
    m = re.search(iso_pattern, text)
    if m:
        return m.group(1)

    # Try dates in filename
    m = re.search(iso_pattern, filename)
    if m:
        return m.group(1)

    # Try loose dates in text
    loose_pattern = r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b'
    m = re.search(loose_pattern, text)
    if m:
        d = normalize_date(m.group(1))
        if d:
            return d

    # Try month-name dates
    month_pattern = r'(?i)((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})'
    m = re.search(month_pattern, text)
    if m:
        for fmt in ["%B %d, %Y", "%B %d %Y"]:
            try:
                return datetime.strptime(m.group(1).replace(",", ", ").replace("  ", " ").strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

    return None


def normalize_date(date_str):
    """Convert various date formats to YYYY-MM-DD."""
    for fmt in ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y", "%Y-%m-%d"]:
        try:
            d = datetime.strptime(date_str, fmt)
            if d.year < 100:
                d = d.replace(year=d.year + 2000)
            if 1990 <= d.year <= 2030:
                return d.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── 3. CLAUDE API ──────────────────────────────────────────────────────

def call_claude(prompt, parse_json=False):
    """Call Claude API via the Anthropic SDK."""
    try:
        import anthropic
    except ImportError:
        log("anthropic package not installed. Run: pip install anthropic")
        sys.exit(1)

    api_key = get_api_key("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if parse_json:
            return extract_json(text)
        return text
    except Exception as e:
        log(f"Claude API error: {e}")
        return {} if parse_json else ""


def extract_json(text):
    """Extract JSON object from potentially markdown-wrapped text."""
    clean = text.replace("```json", "").replace("```", "")
    start = clean.find("{")
    end = clean.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(clean[start:end + 1])
    except json.JSONDecodeError:
        return {}


# ── 4. CLASSIFICATION ───────────────────────────────────────────────────

def classify_document(text):
    """Ask Claude to classify: litigation vs non-litigation."""
    prompt = f"""Analyze the legal text below. You are a legal intake clerk.
Return strictly valid JSON. No markdown.
Structure:
{{
  "is_litigation": true or false,
  "docket_number": "extracted docket or null",
  "case_name": "e.g. Smith v. Jones Corp (extract from text if possible)",
  "title_hint": "e.g. Motion to Dismiss",
  "doc_type_if_not_case": "e.g. Contract, Letter",
  "summary_if_not_case": "Short description (max 10 words)"
}}

TEXT:
{text[:3000]}"""

    result = call_claude(prompt, parse_json=True)
    if not result:
        return {"is_litigation": True, "docket_number": "", "title_hint": "Unknown Document"}
    return result


# ── 5. COURTLISTENER ────────────────────────────────────────────────────

def search_courtlistener(query):
    """Search CourtListener for case matches."""
    cl_token = get_api_key("CL_TOKEN", required=False)
    if not cl_token:
        log("CL_TOKEN not set — skipping CourtListener lookup")
        return []

    if len(query.strip()) < 3:
        return []

    encoded = urllib.parse.quote(query.strip())
    url = f"https://www.courtlistener.com/api/rest/v4/search/?q={encoded}&type=d"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Token {cl_token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            results = data.get("results", [])
            candidates = []
            for r in results:
                name = r.get("caseName", "")
                court = r.get("court_id", "").lower()
                docket = r.get("docketNumber", "")
                date_filed = r.get("dateFiled", "")
                if name:
                    candidates.append({
                        "name": name,
                        "court": court,
                        "docket": docket,
                        "date_filed": date_filed,
                    })
            return candidates
    except Exception as e:
        log(f"CourtListener error: {e}")
        return []


def find_verified_match(candidates, text, search_docket):
    """Try to verify a match by docket number + name tokens in text."""
    ocr_nums = re.sub(r'[^0-9]', '', search_docket)
    if len(ocr_nums) < 3:
        return None

    for cand in candidates:
        cand_nums = re.sub(r'[^0-9]', '', cand["docket"])
        if ocr_nums in cand_nums or cand_nums in ocr_nums:
            # Verify name tokens appear in text
            if validate_name_in_text(cand["name"], text):
                return cand
    return None


def validate_name_in_text(case_name, text):
    """Check if meaningful tokens from case name appear in document text.

    Requires at least 2 matching tokens (or all tokens if fewer than 2 exist)
    to avoid false positives from a single common surname.
    """
    noise = {
        "v.", "vs.", "inc", "corp", "llc", "ltd", "company", "corporation",
        "et", "al", "defendant", "plaintiff", "united", "states", "district",
        "court", "office", "service", "america", "government", "division",
        "western", "eastern", "central", "southern", "northern", "county",
        "department", "postal", "commissioner", "social", "security",
        "judge", "clerk", "the", "of", "and", "for", "in",
    }
    tokens = [w for w in re.split(r'[^a-zA-Z]+', case_name.lower()) if len(w) > 3 and w not in noise]
    if not tokens:
        return True
    haystack = text.lower()
    matched = sum(1 for t in tokens if t in haystack)
    required = min(2, len(tokens))
    return matched >= required


def find_name_match(candidates, gemini_case_name):
    """Find a CL candidate whose name matches the extracted case name."""
    target_tokens = extract_name_tokens(gemini_case_name)
    if not target_tokens:
        return None

    best = None
    best_score = 0
    for cand in candidates:
        cand_tokens = extract_name_tokens(cand["name"])
        if not cand_tokens:
            continue
        overlap = target_tokens & cand_tokens
        score = len(overlap) / max(len(target_tokens), 1)
        if score > best_score:
            best_score = score
            best = cand

    # Require at least 50% token overlap AND at least 2 matching tokens
    if best and best_score >= 0.5:
        matched_tokens = extract_name_tokens(best["name"]) & target_tokens
        if len(matched_tokens) >= 2 or best_score >= 0.8:
            return best
    return None


def names_overlap(name_a, name_b):
    """Check if two case names share meaningful tokens."""
    tokens_a = extract_name_tokens(name_a)
    tokens_b = extract_name_tokens(name_b)
    if not tokens_a or not tokens_b:
        return False
    overlap = tokens_a & tokens_b
    smaller = min(len(tokens_a), len(tokens_b))
    return len(overlap) >= 2 or (smaller > 0 and len(overlap) / smaller >= 0.6)


def extract_name_tokens(name):
    """Extract meaningful tokens from a case name for comparison."""
    noise = {
        "v", "vs", "inc", "corp", "llc", "ltd", "company", "corporation",
        "et", "al", "the", "of", "and", "for", "in", "a", "an",
        "dba", "aka", "individually", "personal", "capacity",
        "united", "states", "district", "court", "county",
    }
    tokens = set(w.lower() for w in re.split(r'[^a-zA-Z]+', name) if len(w) > 2 and w.lower() not in noise)
    return tokens


def ask_claude_select_best(text, candidates, hint_case_name=""):
    """Ask Claude to pick the best matching case from candidates."""
    if len(candidates) == 1:
        return candidates[0]

    simplified = [{"name": c["name"], "court": c["court"], "docket": c["docket"]} for c in candidates[:8]]
    hint = f'\nHINT: The document likely relates to the case "{hint_case_name}".\n' if hint_case_name else ""
    prompt = f"""I have a legal document and a list of potential court case matches.
Identify which Case Candidate best matches the Document Text.
{hint}
DOCUMENT TEXT (OCR):
\"\"\"{text[:2000]}\"\"\"

CANDIDATES:
{json.dumps(simplified)}

INSTRUCTIONS:
1. Compare Plaintiff, Defendant, and Court names from the document with the candidates.
2. If a candidate's party names match the document, prefer it even if docket format differs.
3. Return strictly JSON: {{ "best_index": 0 }}
(0-indexed number of the best matching candidate)"""

    result = call_claude(prompt, parse_json=True)
    idx = result.get("best_index", 0)
    if isinstance(idx, int) and 0 <= idx < len(candidates):
        return candidates[idx]
    return candidates[0]


# ── 6. TITLE REFINEMENT ────────────────────────────────────────────────

def refine_title(text, case_name):
    """Ask Claude to generate a clean document title."""
    prompt = f"""Refine the title of this legal document.
Context: The case is "{case_name}".
Return ONLY the title string (Max 15 words). No quotes, no explanation.

Examples of good titles:
- Motion to Dismiss for Failure to State a Claim
- Opposition to Motion for Summary Judgment
- Declaration of John Smith in Support of Motion
- Order Granting Preliminary Injunction
- Exhibit A to Declaration of Jane Doe

TEXT:
{text[:2500]}"""

    raw = call_claude(prompt)
    return raw.strip().strip('"').strip("'")


# ── 7. FILENAME CONSTRUCTION ───────────────────────────────────────────

def build_filename(date, court, case_name, docket, title, ext=".pdf"):
    """Build structured filename: DATE - COURT - CASE - DOCKET - TITLE.ext"""
    parts = []
    parts.append(date or "UNDATED")
    if court:
        parts.append(court)
    if case_name:
        parts.append(case_name)
    if docket:
        parts.append(docket)
    if title:
        parts.append(title)

    filename = " - ".join(parts) + ext
    # Sanitize
    filename = re.sub(r'[/\\?%*|"<>:]', ' ', filename)
    filename = re.sub(r'\s+', ' ', filename).strip()
    return filename[:200]


# ── MAIN PIPELINE ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: smart_rename_legal.py <filepath>", file=sys.stderr)
        sys.exit(2)

    filepath = sys.argv[1]
    filename = os.path.basename(filepath)

    if not os.path.exists(filepath):
        log(f"File not found: {filepath}")
        sys.exit(1)

    # Skip already-renamed files (pattern: YYYY-MM-DD - ...)
    if re.match(r'^\d{4}-\d{2}-\d{2} - ', filename):
        log(f"Already renamed, skipping: {filename}", file=filepath)
        print(filepath)
        sys.exit(0)

    log(f"Processing: {filename}", file=filepath)

    # Step 1: Extract text
    log("Step 1: Extracting text...", file=filepath)
    file_ext = os.path.splitext(filepath)[1].lower()
    if file_ext == '.docx':
        text = extract_text_from_docx(filepath)
    else:
        text = extract_text_from_pdf(filepath)
    if not text:
        log("No text extracted. Skipping rename.", file=filepath)
        sys.exit(1)
    log(f"Extracted {len(text.split())} words", file=filepath)

    # Step 2: Extract date
    date = extract_best_date(text, filename)
    log(f"Date: {date or 'UNDATED'}", file=filepath)

    # Step 3: Classify with Claude
    log("Step 3: Classifying...", file=filepath)
    classification = classify_document(text)
    is_litigation = classification.get("is_litigation", True)

    if not is_litigation:
        doc_type = classification.get("doc_type_if_not_case", "Document")
        summary = classification.get("summary_if_not_case", "")
        new_name = build_filename(date, "", "", "", doc_type + (" - " + summary if summary else ""), ext=file_ext)
        log(f"Non-litigation: {doc_type}", file=filepath)
    else:
        docket = classification.get("docket_number", "") or ""
        extracted_case_name = classification.get("case_name", "") or ""
        title_hint = classification.get("title_hint", "Document")
        log(f"Litigation. Docket: {docket}, Case: {extracted_case_name}, Hint: {title_hint}", file=filepath)

        court = ""
        case_name = ""
        doc_title = title_hint

        # Step 4: CourtListener verification
        log("Step 4: Searching CourtListener...", file=filepath)
        candidates = []

        if docket and extracted_case_name:
            combined = f"{docket} {extracted_case_name}"
            log(f"Searching: {combined}", file=filepath)
            candidates = search_courtlistener(combined)

        if not candidates and extracted_case_name:
            log(f"Fallback search by case name: {extracted_case_name}", file=filepath)
            candidates = search_courtlistener(extracted_case_name)

        if not candidates and docket:
            log(f"Fallback search by docket: {docket}", file=filepath)
            candidates = search_courtlistener(docket)

        if candidates:
            log(f"Found {len(candidates)} candidates", file=filepath)

            verified = find_verified_match(candidates, text, docket)
            if verified:
                log(f"Verified match: {verified['name']}", file=filepath)
                match = verified
            else:
                name_match = None
                if extracted_case_name:
                    name_match = find_name_match(candidates, extracted_case_name)

                if name_match:
                    log(f"Name match: {name_match['name']} (matched: {extracted_case_name})", file=filepath)
                    match = name_match
                else:
                    log("No verified/name match. Asking Claude to select...", file=filepath)
                    match = ask_claude_select_best(text, candidates, hint_case_name=extracted_case_name)
                    log(f"AI selected: {match['name']}", file=filepath)

                    if extracted_case_name and not names_overlap(match["name"], extracted_case_name):
                        log(f"AI pick '{match['name']}' doesn't match '{extracted_case_name}'. Trusting extraction.", file=filepath)
                        case_name = extracted_case_name
                        if not docket:
                            docket = match["docket"]
                        match = None

            if match:
                case_name = match["name"]
                court_raw = match["court"].lower()
                court = BLUEBOOK_MAP.get(court_raw, court_raw.upper())
                if not docket:
                    docket = match["docket"]
        else:
            log("No CourtListener matches", file=filepath)
            if extracted_case_name:
                case_name = extracted_case_name
                log(f"Using extracted case name: {case_name}", file=filepath)

        # Step 5: Refine title
        if case_name:
            log("Step 5: Refining title...", file=filepath)
            doc_title = refine_title(text, case_name)
            log(f"Title: {doc_title}", file=filepath)

        new_name = build_filename(date, court, case_name, docket, doc_title, ext=file_ext)

    # Step 6: Rename file
    log(f"New filename: {new_name}", file=filepath)

    new_path = os.path.join(os.path.dirname(filepath), new_name)

    # Handle collisions
    if os.path.exists(new_path) and new_path != filepath:
        base, collision_ext = os.path.splitext(new_name)
        i = 1
        while os.path.exists(new_path):
            new_path = os.path.join(os.path.dirname(filepath), f"{base} ({i}){collision_ext}")
            i += 1

    try:
        os.rename(filepath, new_path)
        log(f"Renamed: {new_name}", file=filepath)
        print(new_path)
        sys.exit(0)
    except OSError as e:
        log(f"Rename failed: {e}", file=filepath)
        sys.exit(1)


if __name__ == "__main__":
    main()
