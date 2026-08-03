# 🎸 Guitar Chord Telegram Bot

Searches Ultimate Guitar for chord sheets, optionally transposes them, and sends a clean PDF back to the user.

## How users interact with it

```
Wonderwall by Oasis                  → default key, simplified chords
Wonderwall by Oasis "G"              → transposed to G
Hotel California by Eagles "Am"      → transposed to Am
```

---

## How it works

```
User sends: Wonderwall by Oasis "G"
        │
        ▼
Parse request → song="Wonderwall", artist="Oasis", key="G"
        │
        ▼
Search ultimate-guitar.com via their internal JSON API
        │
        ▼
Scrape the chord sheet (handles [ch]...[/ch] UG markup)
        │
        ▼
Detect original key from first chord found
        │
        ▼
Transpose all chord lines by N semitones
        │
        ▼
Render to PDF (ReportLab) with:
  - Title & artist header
  - BPM and strumming pattern of song (if it's available)
  - Tuning / Capo / Key metadata
  - Color-coded chord lines (red) vs lyrics (black)
  - Section headers (Verse, Chorus, etc.)
        │
        ▼
Send PDF file back to user in Telegram
```

---

## Architecture notes

### Why not download UG's PDF directly?
Ultimate Guitar PDFs require a **Pro subscription** and are behind authentication. Instead, the bot scrapes the chord text and generates its own PDF — which also gives full control over transposition and formatting.

### Anti-scraping considerations
UG uses Cloudflare. The bot uses realistic browser headers and reads from UG's embedded JSON data (the `js-store` div), which is more stable than parsing raw HTML. If you hit blocks:
- Add `time.sleep(1-3)` between requests
- Rotate User-Agent strings
- Consider using a residential proxy

### Key transposition
The bot uses a chromatic scale array and detects chord tokens with a regex. It shifts every chord root by the computed semitone difference between the original and target key. Sharps are used (no flats).

---

## File structure

```
guitar_chord_bot/
├── bot.py            ← main bot
├── requirements.txt
└── README.md
```

---

## Deployment (optional)

To run 24/7, deploy on a cheap VPS or use a free tier:

```bash
# As a systemd service (Linux VPS)
sudo nano /etc/systemd/system/guitar-bot.service

[Unit]
Description=Guitar Chord Bot
After=network.target

[Service]
ExecStart=/usr/bin/python3 /path/to/bot.py
Environment="TELEGRAM_BOT_TOKEN=your_token"
Restart=always

[Install]
WantedBy=multi-user.target

sudo systemctl enable guitar-bot
sudo systemctl start guitar-bot
```

Or deploy to **Railway**, **Render**, or **Fly.io** for free cloud hosting.
