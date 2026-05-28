"""
Guitar Chord Telegram Bot
Uses cffi to bypass Cloudflare on Ultimate Guitar.
"""

from curses import raw
import os
import re
import logging
import asyncio
import tempfile
import json
import math
import gc
from pathlib import Path
from html import unescape
import signal

from dotenv import load_dotenv
load_dotenv()

from curl_cffi.requests import AsyncSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, filters,
    ContextTypes, CommandHandler, CallbackQueryHandler,
)
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Preformatted, Table, TableStyle, Flowable,
)
from reportlab.lib import colors

# ── Logging ────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ── Music theory ────
CHROMATIC = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
CHORD_PATTERN = re.compile(
    r"\b([A-G][b#]?)(maj7|maj|min7|min|m7|m|7|sus2|sus4|aug|dim|add9|6|9|11|13)?\b"
)
HARD_CHORD_DIAGRAMS = {
    "F":   {"pos": [ 1,  3,  3,  2,  1,  1], "base": 1},
    "Fm":  {"pos": [ 1,  3,  3,  1,  1,  1], "base": 1},
    "F#":  {"pos": [ 2,  4,  4,  3,  2,  2], "base": 2},
    "F#m": {"pos": [ 2,  4,  4,  2,  2,  2], "base": 2},
    "B":   {"pos": [-1,  2,  4,  4,  4,  2], "base": 2},
    "Bm":  {"pos": [-1,  2,  4,  4,  3,  2], "base": 2},
    "Bb":  {"pos": [-1,  1,  3,  3,  3,  1], "base": 1},
    "Bbm": {"pos": [-1,  1,  3,  3,  2,  1], "base": 1},
    "Cm":  {"pos": [-1,  3,  5,  5,  4,  3], "base": 3},
    "C#":  {"pos": [-1,  4,  6,  6,  6,  4], "base": 4},
    "C#m": {"pos": [-1,  4,  6,  6,  5,  4], "base": 4},
    "Eb":  {"pos": [-1,  6,  8,  8,  8,  6], "base": 6},
    "Ebm": {"pos": [-1,  6,  8,  8,  7,  6], "base": 6},
    "G#m": {"pos": [ 4,  6,  6,  4,  4,  4], "base": 4},
    "Ab":  {"pos": [ 4,  6,  6,  5,  4,  4], "base": 4},
}

class ChordDiagram(Flowable):
    S_GAP = 6; F_GAP = 6.5; FRETS = 4; N_STR = 6; DOT_R = 2.4; PAD = 5

    def __init__(self, name, positions, base_fret=1):
        super().__init__()
        self.chord_name = name
        self.positions  = positions
        self.base_fret  = base_fret
        self.width  = (self.N_STR - 1) * self.S_GAP + self.PAD * 2 + 10
        self.height = self.FRETS * self.F_GAP + self.PAD * 2 + 16

    def draw(self):
        c = self.canv
        sg, fg = self.S_GAP, self.F_GAP
        nf, ns, pad = self.FRETS, self.N_STR, self.PAD
        gw = (ns - 1) * sg
        gh = nf * fg
        ox = pad + 4
        oy = pad + 2

        c.setFont("Helvetica-Bold", 7)
        c.setFillColor(colors.HexColor("#c62828"))
        c.drawCentredString(ox + gw / 2, oy + gh + 8, self.chord_name)
        c.setFillColor(colors.black)

        if self.base_fret == 1:
            c.setLineWidth(2.5)
            c.setStrokeColor(colors.black)
            c.line(ox, oy + gh, ox + gw, oy + gh)
        else:
            c.setLineWidth(0.5)
            c.setStrokeColor(colors.HexColor("#888888"))
            c.line(ox, oy + gh, ox + gw, oy + gh)
            c.setFont("Helvetica", 5.5)
            c.drawString(ox + gw + 2, oy + gh - fg / 2, f"{self.base_fret}fr")

        c.setLineWidth(0.4)
        c.setStrokeColor(colors.HexColor("#aaaaaa"))
        for i in range(nf + 1):
            c.line(ox, oy + gh - i * fg, ox + gw, oy + gh - i * fg)

        c.setLineWidth(0.7)
        c.setStrokeColor(colors.black)
        for i in range(ns):
            c.line(ox + i * sg, oy, ox + i * sg, oy + gh)

        for s_idx, fret in enumerate(self.positions):
            x = ox + s_idx * sg
            top_y = oy + gh
            if fret == -1:
                c.setFont("Helvetica-Bold", 7)
                c.setFillColor(colors.black)
                c.drawCentredString(x, top_y + 2, "×")
            elif fret == 0:
                c.setStrokeColor(colors.black)
                c.setFillColor(colors.white)
                c.setLineWidth(0.8)
                c.circle(x, top_y + 3, 2, stroke=1, fill=1)
                c.setFillColor(colors.black)
            else:
                rel = fret - self.base_fret
                if 0 <= rel < nf:
                    c.setFillColor(colors.HexColor("#c62828"))
                    c.circle(x, top_y - rel * fg - fg / 2, self.DOT_R, stroke=0, fill=1)
                    c.setFillColor(colors.black)

# ── Global HTTP session ────
_session: AsyncSession | None = None

def get_session() -> AsyncSession:
    global _session
    if _session is None:
        _session = AsyncSession()
    return _session

def normalize_root(root):
    return FLAT_TO_SHARP.get(root, root)

def transpose_chord(chord, semitones):
    match = re.match(r"^([A-G][b#]?)(.*)", chord)
    if not match:
        return chord
    root, suffix = match.group(1), match.group(2)
    root = normalize_root(root)
    if root not in CHROMATIC:
        return chord
    new_root = CHROMATIC[(CHROMATIC.index(root) + semitones) % 12]
    return new_root + suffix

def semitones_between(from_key, to_key):
    from_key = normalize_root(from_key)
    to_key   = normalize_root(to_key)
    diff = (CHROMATIC.index(to_key) - CHROMATIC.index(from_key)) % 12
    # If more than 6 semitones up, go down instead (shorter path)
    if diff > 6:
        diff -= 12
    return diff

def transpose_line(line: str, semitones: int, simplify: bool = True) -> str:
    def replace_chord(m):
        chord = m.group(0)
        if semitones:
            chord = transpose_chord(chord, semitones)
        if simplify:
            chord = simplify_chord(chord)
        return chord
    return CHORD_PATTERN.sub(replace_chord, line)

def is_chord_line(line):
    tokens = line.split()
    if not tokens:
        return False
    chord_hits = sum(1 for t in tokens if CHORD_PATTERN.fullmatch(t))
    return chord_hits / len(tokens) >= 0.5

def detect_key_from_chords(lines):
    for line in lines:
        if is_chord_line(line):
            m = CHORD_PATTERN.search(line)
            if m:
                return normalize_root(m.group(1))
    return None

def get_hard_chords_in_song(lines: list, semitones: int = 0) -> list:
    found = set()
    for line in lines:
        if is_chord_line(line):
            for m in CHORD_PATTERN.finditer(line):
                chord = m.group(0)
                if semitones:
                    chord = transpose_chord(chord, semitones)
                chord = simplify_chord(chord)
                if chord in HARD_CHORD_DIAGRAMS:
                    found.add(chord)
    return [k for k in HARD_CHORD_DIAGRAMS if k in found]

def build_chord_ref_section(hard_chords: list) -> list:
    if not hard_chords:
        return []
    s = get_pdf_styles()
    diagrams = [ChordDiagram(n, HARD_CHORD_DIAGRAMS[n]["pos"], HARD_CHORD_DIAGRAMS[n]["base"]) for n in hard_chords]
    col_w = diagrams[0].width
    tbl = Table([diagrams], colWidths=[col_w] * len(diagrams))
    tbl.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",  (0,0), (-1,-1), "CENTER"),
        ("LEFTPADDING",   (0,0), (-1,-1), 3),
        ("RIGHTPADDING",  (0,0), (-1,-1), 3),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))
    return [
        Paragraph("Chord Reference", s["ref_hdr"]),
        Spacer(1, 2*mm),
        tbl,
        HRFlowable(width="100%", thickness=0.5, color=colors.grey, spaceAfter=4*mm),
    ]

async def scheduled_restart(app):
    """Restart the bot every 6 hours to free memory."""
    while True:
        await asyncio.sleep(6 * 60 * 60)  # 6 hours
        logger.info("Scheduled restart triggered")
        os.kill(os.getpid(), signal.SIGTERM)

# ── Playwright scraper ────
async def get_js_store(page) -> dict | None:
    """Try multiple strategies to extract the js-store JSON from a UG page."""

    # Strategy 1: wait for the div to appear (up to 10s)
    try:
        await page.wait_for_selector("div.js-store", timeout=10000)
        store = await page.query_selector("div.js-store")
        if store:
            data_content = await store.get_attribute("data-content")
            if data_content:
                logger.info("js-store found via selector wait")
                return json.loads(data_content)
    except Exception as e:
        logger.warning(f"Strategy 1 failed: {e}")

    # Strategy 2: extract via JS evaluation
    try:
        data_content = await page.evaluate(
            "() => { const el = document.querySelector('div.js-store'); return el ? el.getAttribute('data-content') : null; }"
        )
        if data_content:
            logger.info("js-store found via JS evaluation")
            return json.loads(data_content)
    except Exception as e:
        logger.warning(f"Strategy 2 failed: {e}")

    # Strategy 3: intercept the store JSON from page source
    try:
        content = await page.content()
        match = re.search(r'data-content="({.*?})"', content)
        if match:
            raw = match.group(1).replace("&quot;", '"').replace("&amp;", "&")
            logger.info("js-store found via regex in page source")
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"Strategy 3 failed: {e}")

    # Debug: log page title and first 300 chars so we can see what we got
    try:
        title = await page.title()
        body  = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : 'no body'")
        logger.warning(f"Page title: {title!r}")
        logger.warning(f"Page body snippet: {body!r}")
    except Exception:
        pass

    return None


def pick_best_tab(results: list) -> str | None:
    """
    From a list of UG search results, pick the Chords tab with the
    highest (vote_count * rating) score — i.e. highest rated with
    the most votes wins.
    """
    chord_tabs = [
        r for r in results
        if r.get("type") == "Chords" and not r.get("marketing_type")
    ]
    if not chord_tabs:
        # fallback: any chords-like type
        chord_tabs = [r for r in results if "chord" in r.get("type", "").lower()]
    if not chord_tabs:
        return None

    def score(r):
        rating     = float(r.get("rating", 0) or 0)
        votes      = int(r.get("votes", 0) or 0)
        # weighted: rating * log(votes+1) so a 5-star with 1000 votes
        # beats a 5-star with 2 votes
        return rating * math.log(votes + 1)

    best = max(chord_tabs, key=score)
    logger.info(
        f"Picked tab: {best.get('song_name')} | "
        f"rating={best.get('rating')} votes={best.get('votes')} "
        f"url={best.get('tab_url')}"
    )
    return best.get("tab_url")


async def search_and_scrape(song: str, artist: str) -> dict | None:
    """Use curl_cffi to bypass Cloudflare and scrape UG."""
    from urllib.parse import quote
    query = quote(f"{artist} {song}")
    ug_search_url = (
        f"https://www.ultimate-guitar.com/search.php"
        f"?search_type=title&value={query}"
    )
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.ultimate-guitar.com/",
    }

    session = get_session()
    try:
        # ── Step 1: Search page ────
        logger.info(f"Fetching: {ug_search_url}")
        html = None
        for impersonate in ["chrome124", "chrome110", "safari17_0"]:
            resp = await session.get(ug_search_url, impersonate=impersonate, headers=headers, timeout=30)
            html = resp.text
            if "Just a moment" not in html and "Performing security verification" not in html:
                break
            logger.warning(f"Cloudflare challenge with {impersonate}, retrying...")
            _session = None
            session = get_session()

        if not html or "Just a moment" in html or "Performing security verification" in html:
            logger.warning("Cloudflare challenge hit on search page — all impersonations failed")
            return None

        await asyncio.sleep(1.5)

        match = re.search(r'data-content="({.*?})"', html)
        if not match:
            logger.warning("js-store not found in search page HTML")
            logger.warning(f"Page snippet: {html[:300]}")
            return None

        raw = match.group(1).replace("&quot;", '"').replace("&amp;", "&")
        data = json.loads(raw)

        try:
            results = data["store"]["page"]["data"]["results"]
        except KeyError:
            logger.warning(f"Unexpected JSON structure: {list(data.keys())}")
            return None

        logger.info(f"Found {len(results)} search results")
        for r in results[:5]:
            logger.info(f"  [{r.get('type')}] {r.get('song_name')} — rating={r.get('rating')} votes={r.get('votes')}")

        tab_url = pick_best_tab(results)
        if not tab_url:
            logger.warning("No chords tab found in results")
            return None

        # ── Step 2: Tab page ────
        logger.info(f"Fetching tab page: {tab_url}")
        html2 = None
        for impersonate in ["chrome124", "chrome110", "safari17_0"]:
            resp2 = await session.get(tab_url, impersonate=impersonate, headers=headers, timeout=30)
            html2 = resp2.text
            if "Just a moment" not in html2 and "Performing security verification" not in html2:
                break
            logger.warning(f"Cloudflare challenge on tab page with {impersonate}, retrying...")
            _session = None
            session = get_session()

        if not html2 or "Just a moment" in html2 or "Performing security verification" in html2:
            logger.warning("Cloudflare challenge hit on tab page — all impersonations failed")
            return None

        match2 = re.search(r'data-content="({.*?})"', html2)
        if not match2:
            logger.warning("js-store not found in tab page HTML")
            return None

        raw2 = match2.group(1).replace("&quot;", '"').replace("&amp;", "&")
        data2 = json.loads(raw2)

        tab_data = data2["store"]["page"]["data"]["tab"]
        tab_view = data2["store"]["page"]["data"]["tab_view"]

        title       = tab_data.get("song_name", "Unknown")
        artist_name = tab_data.get("artist_name", "Unknown")
        tuning      = tab_view.get("meta", {}).get("tuning", {}).get("value", "Standard")
        capo        = str(tab_view.get("meta", {}).get("capo", 0) or "None")
        # BPM
        tempo_raw = tab_view.get("meta", {}).get("tempo", None)
        bpm = str(int(float(tempo_raw))) if tempo_raw else None
        # Strumming pattern
        strum = None
        strum_match = re.search(
            r'(?:strum(?:ming)?(?:\s+pattern)?[:\s]+)([DdUu \-↑↓x]+)',
            content, re.IGNORECASE
            )
        if strum_match:
            strum = strum_match.group(1).strip()

        content = tab_view.get("wiki_tab", {}).get("content", "")
        if not content:
            content = tab_view.get("content", "")
        content = re.sub(r"\[tab\]|\[/tab\]", "", content)
        content = re.sub(r"\[ch\](.*?)\[/ch\]", r"\1", content)
        content = clean_text(content)
        lines = content.split("\n")

        return {
            "title": title,
            "artist": artist_name,
            "lines": lines,
            "tuning": tuning,
            "capo": capo,
            "source_url": tab_url,
            "bpm": bpm,
            "strumming": strum,
}


    except Exception as e:
        logger.error(f"curl_cffi scrape error: {e}", exc_info=True)
        return None

# ── Chord simplification ────
KEEP_MINOR = re.compile(r"^[A-G][b#]?m$")  # already simple minor e.g. Am, F#m

def simplify_chord(chord: str) -> str:
    """Strip complex suffixes and slash bass notes, keeping only root + optional minor."""
    # Remove slash bass note first (e.g. G/B → G, Bb/D → Bb)
    chord = chord.split("/")[0]
    # Then strip complex suffixes
    match = re.match(r"^([A-G][b#]?)(m(?!aj))?", chord)
    if not match:
        return chord
    root = match.group(1)
    minor = match.group(2) or ""
    return root + minor

# ── Text cleaning ────
def clean_text(text: str) -> str:
    """Decode HTML entities and normalize special characters."""
    import unicodedata
    text = unescape(text)  # decode &#039; &amp; &quot; etc.
    text = unicodedata.normalize("NFKC", text)  # normalize unicode (curly quotes, etc.)
    # Replace common problematic characters
    text = text.replace("\u2019", "'").replace("\u2018", "'")  # curly apostrophes
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # curly quotes
    text = text.replace("\u2013", "-").replace("\u2014", "-")  # en/em dash
    text = text.replace("\u2026", "...")                        # ellipsis
    return text

# ── Cached PDF styles ────
_pdf_styles = None

def get_pdf_styles():
    global _pdf_styles
    if _pdf_styles is None:
        styles = getSampleStyleSheet()
        _pdf_styles = {
            "ref_hdr": ParagraphStyle("RefHeader", parent=styles["Normal"],
               fontSize=9, leading=11, fontName="Helvetica-Bold",
               textColor=colors.HexColor("#1565c0")),
            "title":   ParagraphStyle("ChordTitle", parent=styles["Title"],
                        fontSize=18, leading=22, textColor=colors.HexColor("#d32f2f")),
            "meta":    ParagraphStyle("Meta", parent=styles["Normal"],
                        fontSize=9, leading=12, textColor=colors.HexColor("#555555")),
            "section": ParagraphStyle("Section", parent=styles["Normal"],
                        fontSize=10, leading=13, textColor=colors.HexColor("#1565c0"),
                        fontName="Helvetica-Bold"),
            "chord":   ParagraphStyle("Chord", parent=styles["Normal"],
                        fontSize=9, leading=11, fontName="Courier-Bold",
                        textColor=colors.HexColor("#c62828")),
            "lyric":   ParagraphStyle("Lyric", parent=styles["Normal"],
                        fontSize=10, leading=13, fontName="Courier"),
        }
    return _pdf_styles

# ── PDF generation ────
def build_pdf(chord_data: dict, target_key: str | None, target_semitones: int | None, output_path: str):
    lines   = chord_data["lines"]
    title   = chord_data["title"]
    artist  = chord_data["artist"]
    tuning  = chord_data["tuning"]
    capo    = chord_data["capo"]

    original_key = detect_key_from_chords(lines)
    semitones = 0
    if target_semitones is not None and original_key:
        semitones = target_semitones
        display_key = CHROMATIC[(CHROMATIC.index(normalize_root(original_key)) + semitones) % 12]

    elif target_key and original_key:
        display_key = original_key or "?"

    if target_semitones is not None and original_key:
        semitones = target_semitones
        display_key = CHROMATIC[(CHROMATIC.index(normalize_root(original_key)) + semitones) % 12]
    elif target_key and original_key:
        try:
            semitones = semitones_between(original_key, target_key)
            display_key = target_key
        except (ValueError, IndexError):
            pass

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )
    s = get_pdf_styles()

    story = []
    story.append(Paragraph(title, s["title"]))
    story.append(Paragraph(f"by {artist}", s["meta"]))
    story.append(Spacer(1, 2*mm))

    meta_line = f"Tuning: {tuning}  |  Capo: {capo}  |  Key: {display_key}"
    if semitones:
        meta_line += f"  (transposed {semitones:+d} semitones from {original_key})"
    story.append(Paragraph(meta_line, s["meta"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey, spaceAfter=3*mm))
    hard_chords = get_hard_chords_in_song(lines, semitones)
    story.extend(build_chord_ref_section(hard_chords))
    bpm_str   = f"BPM: {chord_data.get('bpm')}" if chord_data.get('bpm') else "BPM: N/A"
    strum_str = f"Strumming: {chord_data.get('strumming')}" if chord_data.get('strumming')else "Strumming: Not Available"
    story.append(Paragraph(f"{bpm_str}  |  {strum_str}", s["meta"]))

    section_re = re.compile(r"^\[(.*?)\]$")
    for raw_line in lines:
        raw_line = raw_line.rstrip()
        sm = section_re.match(raw_line)
        if sm:
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(sm.group(1).upper(), s["section"]))
            continue
        if not raw_line.strip():
            story.append(Spacer(1, 2*mm))
            continue
        if is_chord_line(raw_line):
            raw_line = transpose_line(raw_line, semitones, simplify=True)
            story.append(Preformatted(clean_text(raw_line), s["chord"]))
        else:
            safe = clean_text(raw_line).replace("&", "&amp;").replace("<​", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, s["lyric"]))
    doc.build(story)

# ── Message parsing ────
def parse_request(text: str):
    # Normalize smart/curly quotes to straight quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # " "
    text = text.replace("\u2018", "'").replace("\u2019", "'")  # ' '

    parts = re.findall(r'"([^"]+)"', text)
    if len(parts) >= 2:
        query, key = parts[0].strip(), parts[1].strip()
    elif len(parts) == 1:
        query, key = parts[0].strip(), None
    else:
        query, key = text.strip(), None

    # Also support unquoted key at the end e.g. "Hosanna Hillsong" C
    if key is None:
        m = re.search(r'\b([A-G][b#]?m?)\s*$', text)
        if m:
            key = m.group(1)
            query = text[:m.start()].strip().strip('"').strip()

    target_key = None
    target_semitones = None
    if key:
        if re.match(r'^[+-]\d+$', key):
            target_semitones = int(key)
        elif re.match(r'^[A-G][b#]?$', key):
            target_key = key

    if key:
        key = key.capitalize()

    return query, target_key, target_semitones

# ── Bot handlers ────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    chat_id = update.effective_chat.id
    logger.info(f"Received: {text!r}")

    if not text:
        return

    query, target_key, target_semitones = parse_request(text)
    logger.info(f"Parsed → query={query!r}, key={target_key!r}, semitones={target_semitones!r}")

    if not query:
        await update.message.reply_text('Please send a request like:\n"Wonderwall Oasis"')
        return

    if " by " in query.lower():
        idx = query.lower().index(" by ")
        song_guess   = query[:idx].strip()
        artist_guess = query[idx + 4:].strip()

    else:
        words = query.split()
        artist_guess = words[-1] if len(words) >= 2 else query
        song_guess   = " ".join(words[:-1]) if len(words) >= 2 else query

    await update.message.reply_text(f'🔍 Searching for: *{query}*...', parse_mode="Markdown")

    chord_data = await search_and_scrape(song_guess, artist_guess)

    if not chord_data:
        await update.message.reply_text(
            "❌ Couldn't find chords. Try being more specific, e.g.:\n"
            '"Wonderwall Oasis"'
        )
        return

    if target_semitones is not None:
        key_msg = f" (transposed by {target_semitones} semitones)"
    else:
        key_msg = f" in key of *{target_key}*" if target_key else " (original key)"
    await update.message.reply_text(
        f"🎸 Generating PDF for *{chord_data['title']}* by *{chord_data['artist']}*{key_msg}...",
        parse_mode="Markdown"
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        pdf_path = tmp.name

    try:
        build_pdf(chord_data, target_key, target_semitones, pdf_path)
        filename = re.sub(r'[\\/*?:"<>|]', "_",
                    f"{chord_data['artist']} - {chord_data['title']}.pdf")
        with open(pdf_path, "rb") as f:
            await context.bot.send_document(
        chat_id=chat_id,
        document=f,
        filename=filename,
        caption=f"🎵 {chord_data['title']} — {chord_data['artist']}\nSource: {chord_data['source_url']}",
        read_timeout=60,
        write_timeout=60,
        connect_timeout=30,
    )
    except Exception as e:
        logger.error(f"PDF error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error generating PDF: {e}")
    finally:
        Path(pdf_path).unlink(missing_ok=True)
        gc.collect()
    context.chat_data["last_chord_data"] = chord_data
    await send_transpose_keyboard(context.bot, chat_id, chord_data)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎸 *Guitar Chord Bot*\n\n"
        "Send me a song request like this:\n\n"
        '`"Song Name Artist Name"`\n'
        '`"Song Name Artist Name" "Key"`\n\n'
        "Examples:\n"
        '`"Wonderwall Oasis"`\n'
        '`"Hotel California Eagles" "Am"`\n'
        '`"Let Her Go Passenger" "G"`\n\n'
        "I'll search Ultimate Guitar and send you a clean PDF!",
        parse_mode="Markdown"
    )

async def send_transpose_keyboard(bot, chat_id: int, chord_data: dict):
    original_key = detect_key_from_chords(chord_data["lines"]) or "?"
    keyboard = [[
        InlineKeyboardButton("-3", callback_data="tp:-3"),
        InlineKeyboardButton("-2", callback_data="tp:-2"),
        InlineKeyboardButton("-1", callback_data="tp:-1"),
        InlineKeyboardButton("🔄 Original", callback_data="tp:0"),
        InlineKeyboardButton("+1", callback_data="tp:+1"),
        InlineKeyboardButton("+2", callback_data="tp:+2"),
        InlineKeyboardButton("+3", callback_data="tp:+3"),
    ]]
    await bot.send_message(
        chat_id=chat_id,
        text=f"🎹 Transpose (original key: *{original_key}*)?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_transpose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id   = query.message.chat_id
    semitones = int(query.data.split(":")[1])
    chord_data = context.chat_data.get("last_chord_data")
    if not chord_data:
        await query.message.reply_text("❌ Session expired. Please search again.")
        return
    original_key = detect_key_from_chords(chord_data["lines"]) or "?"
    if semitones == 0:
        display_key = original_key
        key_label   = f"original key ({original_key})"
    else:
        display_key = CHROMATIC[(CHROMATIC.index(normalize_root(original_key)) + semitones) % 12]
        key_label   = f"key of *{display_key}* ({semitones:+d} semitones)"
    await query.message.reply_text(f"🎸 Generating PDF in {key_label}...", parse_mode="Markdown")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        pdf_path = tmp.name
    try:
        build_pdf(chord_data, None, semitones if semitones != 0 else None, pdf_path)
        filename = re.sub(r'[\\/*?:"<>|]', "_",
                    f"{chord_data['artist']} - {chord_data['title']} ({display_key}).pdf")
        with open(pdf_path, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id, document=f, filename=filename,
                caption=f"🎵 {chord_data['title']} — {chord_data['artist']}\nKey: {display_key}\nSource: {chord_data['source_url']}",
            )
        await send_transpose_keyboard(context.bot, chat_id, chord_data)
    except Exception as e:
        await query.message.reply_text(f"❌ Error: {e}")
    finally:
        Path(pdf_path).unlink(missing_ok=True)
        gc.collect()

# ── Entry point ────
if __name__ == "__main__":
    async def main():
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", handle_start))
        app.add_handler(CommandHandler("help", handle_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(CallbackQueryHandler(handle_transpose_callback, pattern=r"^tp:"))

        asyncio.create_task(scheduled_restart(app))

        logger.info("Bot is running...")
        async with app:
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            await asyncio.Event().wait()

    asyncio.run(main())