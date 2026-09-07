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
