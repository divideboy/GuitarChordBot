"""
Guitar Chord Telegram Bot
Uses cffi to bypass Cloudflare on Ultimate Guitar.
"""

import os
import re
import logging
import asyncio
import tempfile
import json
from pathlib import Path
from html import unescape

from dotenv import load_dotenv
load_dotenv()

from curl_cffi.requests import AsyncSession
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
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
    return (CHROMATIC.index(to_key) - CHROMATIC.index(from_key)) % 12

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
        import math
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

    async with AsyncSession() as session:
        try:
            # ── Step 1: Search page ────
            logger.info(f"Fetching: {ug_search_url}")
            resp = await session.get(ug_search_url, impersonate="chrome124", headers=headers, timeout=30)
            html = resp.text

            # Check for Cloudflare block
            if "Just a moment" in html or "Performing security verification" in html:
                logger.warning("Cloudflare challenge hit on search page")
                return None

            # Extract js-store from raw HTML
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
            resp2 = await session.get(tab_url, impersonate="chrome124", headers=headers, timeout=30)
            html2 = resp2.text

            if "Just a moment" in html2 or "Performing security verification" in html2:
                logger.warning("Cloudflare challenge hit on tab page")
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
            }

        except Exception as e:
            logger.error(f"curl_cffi scrape error: {e}", exc_info=True)
            return None

# ── Chord simplification ────
KEEP_MINOR = re.compile(r"^[A-G][b#]?m$")  # already simple minor e.g. Am, F#m

def simplify_chord(chord: str) -> str:
    """Strip complex suffixes, keeping only root + optional minor."""
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

# ── PDF generation ────
def build_pdf(chord_data: dict, target_key: str | None, output_path: str):
    lines   = chord_data["lines"]
    title   = chord_data["title"]
    artist  = chord_data["artist"]
    tuning  = chord_data["tuning"]
    capo    = chord_data["capo"]

    original_key = detect_key_from_chords(lines)
    semitones = 0
    display_key = original_key or "?"
    if target_key and original_key:
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
    styles = getSampleStyleSheet()
    title_style   = ParagraphStyle("ChordTitle", parent=styles["Title"],
                    fontSize=18, leading=22, textColor=colors.HexColor("#d32f2f"))
    meta_style    = ParagraphStyle("Meta", parent=styles["Normal"],
                    fontSize=9, leading=12, textColor=colors.HexColor("#5555"))
    section_style = ParagraphStyle("Section", parent=styles["Normal"],
                    fontSize=10, leading=13, textColor=colors.HexColor("#1565c0"),
                    fontName="Helvetica-Bold")
    chord_style   = ParagraphStyle("Chord", parent=styles["Normal"],
                    fontSize=9, leading=11, fontName="Courier-Bold",
                    textColor=colors.HexColor("#c62828"))
    lyric_style   = ParagraphStyle("Lyric", parent=styles["Normal"],
                    fontSize=10, leading=13, fontName="Courier")

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(f"by {artist}", meta_style))
    story.append(Spacer(1, 2*mm))

    meta_line = f"Tuning: {tuning}  |  Capo: {capo}  |  Key: {display_key}"
    if semitones:
        meta_line += f"  (transposed {semitones:+d} semitones from {original_key})"
    story.append(Paragraph(meta_line, meta_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey, spaceAfter=3*mm))

    section_re = re.compile(r"^\[(.*?)\]$")
    for raw_line in lines:
        raw_line = raw_line.rstrip()
        sm = section_re.match(raw_line)
        if sm:
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(sm.group(1).upper(), section_style))
            continue
        if not raw_line.strip():
            story.append(Spacer(1, 2*mm))
            continue
        if is_chord_line(raw_line):
            raw_line = transpose_line(raw_line, semitones, simplify=True)
            safe = clean_text(raw_line).replace("&", "&amp;").replace("<​", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, chord_style))
        else:
            safe = clean_text(raw_line).replace("&", "&amp;").replace("<​", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, lyric_style))
    doc.build(story)

# ── Message parsing ────
def parse_request(text: str):
    parts = re.findall(r'"([^"]+)"', text)
    if len(parts) >= 2:
        query, key = parts[0].strip(), parts[1].strip()
    elif len(parts) == 1:
        query, key = parts[0].strip(), None
    else:
        query, key = text.strip(), None
    if key and not re.match(r"^[A-G][b#]?", key):
        key = None
    return query, key

# ── Bot handlers ────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    chat_id = update.effective_chat.id
    logger.info(f"Received: {text!r}")

    if not text:
        return

    query, target_key = parse_request(text)
    logger.info(f"Parsed → query={query!r}, key={target_key!r}")

    if not query:
        await update.message.reply_text('Please send a request like:\n"Wonderwall Oasis"')
        return

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

    key_msg = f" in key of *{target_key}*" if target_key else " (original key)"
    await update.message.reply_text(
        f"🎸 Generating PDF for *{chord_data['title']}* by *{chord_data['artist']}*{key_msg}...",
        parse_mode="Markdown"
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        pdf_path = tmp.name

    try:
        build_pdf(chord_data, target_key, pdf_path)
        filename = re.sub(r'[\\/*?:"<>|]', "_",
                    f"{chord_data['artist']} - {chord_data['title']}.pdf")
        with open(pdf_path, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=filename,
                caption=f"🎵 {chord_data['title']} — {chord_data['artist']}\nSource: {chord_data['source_url']}",
            )
    except Exception as e:
        logger.error(f"PDF error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error generating PDF: {e}")
    finally:
        Path(pdf_path).unlink(missing_ok=True)


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

# ── Entry point ────
if __name__ == "__main__":
    async def main():
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", handle_start))
        app.add_handler(CommandHandler("help", handle_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        logger.info("Bot is running...")
        async with app:
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            await asyncio.Event().wait()

    asyncio.run(main())