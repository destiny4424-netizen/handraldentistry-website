#!/usr/bin/env python3
"""YouTube Live Stream Analysis Agent.

A CLI tool that watches a YouTube channel's live broadcast and reports on
viewer trends and live chat activity (message rate, top chatters, keyword
frequency, basic sentiment, and spam-like messages).

Usage:
    export YOUTUBE_API_KEY=your_api_key
    python youtube_live_agent.py analyze --channel UCxxxxxxxxxxxxxxxxxxxxxx
    python youtube_live_agent.py analyze --video-id VIDEO_ID --chat-samples 200
    python youtube_live_agent.py monitor --channel @yourhandle --duration 300 --interval 30
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:  # pragma: no cover
    build = None
    HttpError = Exception


STOPWORDS = {
    "the", "a", "an", "is", "it", "to", "and", "of", "in", "on", "for",
    "this", "that", "i", "you", "he", "she", "we", "they", "is", "are",
    "was", "were", "be", "been", "with", "at", "by", "as", "or", "but",
    "so", "if", "not", "no", "do", "does", "did", "have", "has", "had",
    "my", "your", "his", "her", "its", "our", "their", "me", "him",
    "us", "them", "what", "which", "who", "whom", "will", "would",
    "can", "could", "just", "im", "u", "ur", "yeah", "yes", "ok", "okay",
}

POSITIVE_WORDS = {
    "good", "great", "awesome", "amazing", "love", "loved", "loving",
    "nice", "cool", "best", "excellent", "fantastic", "wonderful",
    "happy", "thanks", "thank", "congrats", "congratulations", "well",
    "beautiful", "perfect", "haha", "lol", "fire", "goat", "win",
    "winning", "yay", "wow", "brilliant", "super",
}

NEGATIVE_WORDS = {
    "bad", "worst", "hate", "hated", "awful", "terrible", "sucks",
    "sucked", "boring", "lame", "trash", "garbage", "annoying", "sad",
    "angry", "fail", "failed", "losing", "lost", "ugh", "meh", "no",
    "scam", "fake", "cringe",
}

URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
REPEATED_CHAR_RE = re.compile(r"(.)\1{4,}")
WORD_RE = re.compile(r"[a-zA-Z']+")


def get_api_key(cli_key: Optional[str]) -> str:
    key = cli_key or os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise SystemExit(
            "Missing YouTube API key. Pass --api-key or set YOUTUBE_API_KEY."
        )
    return key


class YouTubeLiveAgent:
    def __init__(self, api_key: str):
        if build is None:
            raise SystemExit(
                "google-api-python-client is not installed. "
                "Run: pip install -r requirements.txt"
            )
        self.youtube = build("youtube", "v3", developerKey=api_key)

    def resolve_live_video_id(self, channel_ref: str) -> str:
        """Find the currently live video id for a channel ID or @handle."""
        channel_id = channel_ref
        if channel_ref.startswith("@"):
            resp = self.youtube.channels().list(
                part="id", forHandle=channel_ref.lstrip("@")
            ).execute()
            items = resp.get("items", [])
            if not items:
                raise SystemExit(f"No channel found for handle {channel_ref}")
            channel_id = items[0]["id"]

        resp = self.youtube.search().list(
            part="id",
            channelId=channel_id,
            eventType="live",
            type="video",
            maxResults=1,
        ).execute()
        items = resp.get("items", [])
        if not items:
            raise SystemExit(f"Channel {channel_ref} is not currently live.")
        return items[0]["id"]["videoId"]

    def get_stream_snapshot(self, video_id: str) -> dict:
        resp = self.youtube.videos().list(
            part="snippet,liveStreamingDetails,statistics", id=video_id
        ).execute()
        items = resp.get("items", [])
        if not items:
            raise SystemExit(f"Video {video_id} not found.")
        video = items[0]
        live_details = video.get("liveStreamingDetails", {})
        stats = video.get("statistics", {})
        return {
            "video_id": video_id,
            "title": video["snippet"].get("title"),
            "concurrent_viewers": int(
                live_details.get("concurrentViewers", 0)
            ) if "concurrentViewers" in live_details else None,
            "active_live_chat_id": live_details.get("activeLiveChatId"),
            "actual_start_time": live_details.get("actualStartTime"),
            "like_count": int(stats.get("likeCount", 0)) if "likeCount" in stats else None,
            "timestamp": time.time(),
        }

    def fetch_live_chat_messages(
        self, live_chat_id: str, max_messages: int = 200
    ) -> list[dict]:
        messages: list[dict] = []
        page_token = None
        while len(messages) < max_messages:
            resp = self.youtube.liveChatMessages().list(
                liveChatId=live_chat_id,
                part="snippet,authorDetails",
                pageToken=page_token,
            ).execute()
            for item in resp.get("items", []):
                messages.append({
                    "author": item["authorDetails"].get("displayName", "unknown"),
                    "text": item["snippet"].get("displayMessage", ""),
                    "published_at": item["snippet"].get("publishedAt"),
                })
                if len(messages) >= max_messages:
                    break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
            time.sleep(min(resp.get("pollingIntervalMillis", 2000) / 1000, 5))
        return messages

    def monitor_viewers(
        self, video_id: str, duration_seconds: int, interval_seconds: int
    ) -> list[dict]:
        snapshots = []
        elapsed = 0
        while elapsed <= duration_seconds:
            snapshots.append(self.get_stream_snapshot(video_id))
            if elapsed >= duration_seconds:
                break
            time.sleep(interval_seconds)
            elapsed += interval_seconds
        return snapshots


def analyze_chat(messages: list[dict]) -> dict:
    """Analyze chat messages: activity, top chatters, keywords, sentiment, spam."""
    if not messages:
        return {
            "message_count": 0,
            "top_chatters": [],
            "top_keywords": [],
            "sentiment": {"positive": 0, "neutral": 0, "negative": 0},
            "spam_messages": [],
        }

    author_counter: Counter = Counter()
    word_counter: Counter = Counter()
    sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
    spam_messages = []
    seen_by_author: dict[str, Counter] = {}

    for msg in messages:
        author = msg["author"]
        text = msg["text"] or ""
        author_counter[author] += 1

        words = [w.lower() for w in WORD_RE.findall(text)]
        for w in words:
            if w not in STOPWORDS and len(w) > 1:
                word_counter[w] += 1

        pos_hits = sum(1 for w in words if w in POSITIVE_WORDS)
        neg_hits = sum(1 for w in words if w in NEGATIVE_WORDS)
        if pos_hits > neg_hits:
            sentiment_counts["positive"] += 1
        elif neg_hits > pos_hits:
            sentiment_counts["negative"] += 1
        else:
            sentiment_counts["neutral"] += 1

        is_spam = False
        if URL_RE.search(text):
            is_spam = True
        if REPEATED_CHAR_RE.search(text):
            is_spam = True
        author_msgs = seen_by_author.setdefault(author, Counter())
        author_msgs[text] += 1
        if text and author_msgs[text] >= 3:
            is_spam = True
        if is_spam:
            spam_messages.append(msg)

    return {
        "message_count": len(messages),
        "unique_chatters": len(author_counter),
        "top_chatters": author_counter.most_common(10),
        "top_keywords": word_counter.most_common(15),
        "sentiment": sentiment_counts,
        "spam_messages": spam_messages[:20],
    }


def build_report(snapshots: list[dict], chat_analysis: Optional[dict]) -> str:
    lines = ["# YouTube Live Stream Analysis Report", ""]
    if snapshots:
        first, last = snapshots[0], snapshots[-1]
        lines.append(f"**Video:** {first.get('title')} ({first.get('video_id')})")
        lines.append(f"**Snapshots taken:** {len(snapshots)}")
        viewer_counts = [
            s["concurrent_viewers"] for s in snapshots
            if s.get("concurrent_viewers") is not None
        ]
        if viewer_counts:
            lines.append(f"**Peak concurrent viewers:** {max(viewer_counts)}")
            lines.append(f"**Latest concurrent viewers:** {viewer_counts[-1]}")
            lines.append(
                f"**Average concurrent viewers:** "
                f"{sum(viewer_counts) / len(viewer_counts):.1f}"
            )
        if last.get("like_count") is not None:
            lines.append(f"**Latest like count:** {last['like_count']}")
        lines.append("")

    if chat_analysis is not None:
        lines.append("## Live Chat Analysis")
        lines.append(f"- Messages analyzed: {chat_analysis['message_count']}")
        lines.append(f"- Unique chatters: {chat_analysis.get('unique_chatters', 0)}")
        sentiment = chat_analysis["sentiment"]
        total = sum(sentiment.values()) or 1
        lines.append(
            "- Sentiment: "
            f"{sentiment['positive']} positive "
            f"({sentiment['positive'] / total:.0%}), "
            f"{sentiment['neutral']} neutral "
            f"({sentiment['neutral'] / total:.0%}), "
            f"{sentiment['negative']} negative "
            f"({sentiment['negative'] / total:.0%})"
        )
        if chat_analysis["top_chatters"]:
            top = ", ".join(f"{a} ({c})" for a, c in chat_analysis["top_chatters"][:5])
            lines.append(f"- Top chatters: {top}")
        if chat_analysis["top_keywords"]:
            kw = ", ".join(f"{w} ({c})" for w, c in chat_analysis["top_keywords"][:10])
            lines.append(f"- Top keywords: {kw}")
        lines.append(f"- Possible spam/promo messages flagged: {len(chat_analysis['spam_messages'])}")

    return "\n".join(lines)


def cmd_analyze(args: argparse.Namespace) -> None:
    agent = YouTubeLiveAgent(get_api_key(args.api_key))
    video_id = args.video_id or agent.resolve_live_video_id(args.channel)
    snapshot = agent.get_stream_snapshot(video_id)

    chat_analysis = None
    if snapshot.get("active_live_chat_id"):
        messages = agent.fetch_live_chat_messages(
            snapshot["active_live_chat_id"], max_messages=args.chat_samples
        )
        chat_analysis = analyze_chat(messages)
    else:
        print("No active live chat found for this stream.", file=sys.stderr)

    report = build_report([snapshot], chat_analysis)
    print(report)
    if args.output:
        with open(args.output, "w") as f:
            json.dump({"snapshots": [snapshot], "chat_analysis": chat_analysis}, f, indent=2)
        print(f"\nSaved raw data to {args.output}")


def cmd_monitor(args: argparse.Namespace) -> None:
    agent = YouTubeLiveAgent(get_api_key(args.api_key))
    video_id = args.video_id or agent.resolve_live_video_id(args.channel)
    snapshots = agent.monitor_viewers(video_id, args.duration, args.interval)

    chat_analysis = None
    if snapshots and snapshots[-1].get("active_live_chat_id"):
        messages = agent.fetch_live_chat_messages(
            snapshots[-1]["active_live_chat_id"], max_messages=args.chat_samples
        )
        chat_analysis = analyze_chat(messages)

    report = build_report(snapshots, chat_analysis)
    print(report)
    if args.output:
        with open(args.output, "w") as f:
            json.dump({"snapshots": snapshots, "chat_analysis": chat_analysis}, f, indent=2)
        print(f"\nSaved raw data to {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YouTube Live Stream Analysis Agent")
    parser.add_argument("--api-key", help="YouTube Data API key (or set YOUTUBE_API_KEY)")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    group = common.add_mutually_exclusive_group(required=True)
    group.add_argument("--channel", help="Channel ID or @handle to look up the current live video")
    group.add_argument("--video-id", help="Specific live video ID to analyze")
    common.add_argument("--chat-samples", type=int, default=200, help="Max chat messages to sample")
    common.add_argument("--output", help="Optional path to save raw JSON results")

    analyze_p = sub.add_parser("analyze", parents=[common], help="One-shot snapshot + chat analysis")
    analyze_p.set_defaults(func=cmd_analyze)

    monitor_p = sub.add_parser("monitor", parents=[common], help="Poll viewer count over time")
    monitor_p.add_argument("--duration", type=int, default=300, help="Total seconds to monitor")
    monitor_p.add_argument("--interval", type=int, default=30, help="Seconds between snapshots")
    monitor_p.set_defaults(func=cmd_monitor)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except HttpError as e:
        raise SystemExit(f"YouTube API error: {e}")


if __name__ == "__main__":
    main()
