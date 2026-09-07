# YouTube Live Stream Analysis Agent

A command-line agent that analyzes a YouTube channel's live broadcast: it
tracks concurrent viewers over time and analyzes live chat activity
(message rate, top chatters, keyword frequency, basic sentiment, and
spam/promo-like messages). Useful for reviewing engagement on the clinic's
marketing live streams (Q&A sessions, procedure explainers, etc.).

## Setup

1. Get a YouTube Data API v3 key from the
   [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com).
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Export your API key:
   ```
   export YOUTUBE_API_KEY=your_api_key
   ```

## Usage

### One-shot snapshot + chat sample analysis

```
python youtube_live_agent.py analyze --channel UCxxxxxxxxxxxxxxxxxxxxxx
python youtube_live_agent.py analyze --channel @yourhandle
python youtube_live_agent.py analyze --video-id VIDEO_ID --chat-samples 300 --output report.json
```

### Monitor viewer count over time

```
python youtube_live_agent.py monitor --channel @yourhandle --duration 600 --interval 30
```

- `--duration`: total seconds to monitor (default 300)
- `--interval`: seconds between viewer snapshots (default 30)
- `--chat-samples`: max chat messages to sample for analysis (default 200)
- `--output`: optional path to save raw JSON results (snapshots + chat analysis)

Either command requires exactly one of `--channel` (a channel ID or
`@handle`, used to look up the currently live video) or `--video-id` (a
specific live video to analyze).

## What it reports

- Peak / latest / average concurrent viewers
- Latest like count
- Chat message count and unique chatter count
- Sentiment breakdown (positive / neutral / negative) using a lightweight
  keyword-based classifier
- Top chatters and top keywords
- Messages flagged as possible spam/promo (contains a URL, repeated
  characters, or the same message repeated 3+ times by one author)

## Running tests

The chat-analysis and report logic is unit tested without needing network
access or an API key:

```
python -m unittest discover -s tools/youtube-live-agent
```

---

## Trade signal agent (paper trading only)

`trade_agent.py` extends this toolkit to watch a **live** YouTube stream
for trade calls made in speech or shown on screen, and simulates the
corresponding trade in a paper (fake-money) brokerage account.

> **This tool never places a real-money trade.** `broker.py` hard-codes
> `paper=True` on the Alpaca client and exposes no option to point it at
> a live account. Treat every detected "signal" as an unverified
> heuristic guess, not a confirmed trade call — speech-to-text and OCR on
> a live stream are noisy, and a misheard word or misread overlay can
> easily produce a wrong signal. Review the JSON audit log (`--output`)
> regularly. This is not financial advice, and "live trading signal"
> streams are also a well-known vector for pump-and-dump style scams —
> don't treat detected signals, even in simulation, as validated advice.

### How it works

1. Resolves the live stream's direct media URL with `yt-dlp`.
2. Every `--segment-seconds` (default 30s), captures a short audio clip
   and one video frame with `ffmpeg`.
3. Transcribes the audio with `faster-whisper` and OCRs the frame with
   `pytesseract`.
4. Scans both texts for a buy/sell/long/short action word near a
   ticker-like symbol (`signal_extraction.py`). Optionally restrict
   symbol matching to a `--watchlist` file (recommended for spoken audio,
   since whisper transcripts are lowercase and won't naturally produce
   uppercase tickers).
5. De-duplicates repeated mentions of the same symbol/action within a
   `--cooldown` window, then (unless `--dry-run`) places a simulated
   market order for a fixed `--notional` dollar amount via Alpaca's
   **paper** trading API.
6. Writes every detected signal — executed or not, with its source text
   — to the `--output` JSON audit log.

### Additional setup

Beyond `pip install -r requirements.txt`, this agent needs two system
binaries that are not Python packages:

- [`ffmpeg`](https://ffmpeg.org/) — for audio/frame capture
- [`tesseract-ocr`](https://github.com/tesseract-ocr/tesseract) — for OCR
  (e.g. `apt install tesseract-ocr` / `brew install tesseract`)

You'll also need a free
[Alpaca paper trading account](https://alpaca.markets/) for API keys:

```
export ALPACA_API_KEY=your_paper_key
export ALPACA_SECRET_KEY=your_paper_secret
```

### Usage

```
# Dry run: only detect and log signals, place no orders
python trade_agent.py --url https://www.youtube.com/watch?v=VIDEO_ID --dry-run

# Full run with a watchlist (recommended) and an audit log
python trade_agent.py --url https://www.youtube.com/watch?v=VIDEO_ID \
    --duration 1800 --segment-seconds 30 --notional 100 \
    --watchlist watchlist.example.txt --output audit_log.json
```

Key options: `--duration` (total seconds to watch), `--segment-seconds`
(capture cadence), `--cooldown` (seconds before the same symbol/action can
re-fire), `--notional` (simulated dollars per trade), `--whisper-model`
(tiny/base/small/medium — bigger is more accurate but slower on CPU).

### Running tests

The signal-extraction and cooldown/watchlist logic is unit tested without
needing `ffmpeg`, `tesseract`, network access, or API keys:

```
python -m unittest test_signal_extraction test_trade_agent
```
