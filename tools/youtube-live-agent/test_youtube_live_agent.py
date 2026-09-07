import unittest

from youtube_live_agent import analyze_chat, build_report


class TestAnalyzeChat(unittest.TestCase):
    def test_empty_messages(self):
        result = analyze_chat([])
        self.assertEqual(result["message_count"], 0)
        self.assertEqual(result["top_chatters"], [])

    def test_sentiment_and_keywords(self):
        messages = [
            {"author": "alice", "text": "This is awesome and great!", "published_at": ""},
            {"author": "bob", "text": "This is terrible and boring", "published_at": ""},
            {"author": "alice", "text": "just chatting here", "published_at": ""},
        ]
        result = analyze_chat(messages)
        self.assertEqual(result["message_count"], 3)
        self.assertEqual(result["unique_chatters"], 2)
        self.assertEqual(result["sentiment"]["positive"], 1)
        self.assertEqual(result["sentiment"]["negative"], 1)
        self.assertEqual(result["sentiment"]["neutral"], 1)
        self.assertEqual(dict(result["top_chatters"])["alice"], 2)

    def test_spam_detection_url(self):
        messages = [{"author": "spammer", "text": "check this out http://spam.example.com", "published_at": ""}]
        result = analyze_chat(messages)
        self.assertEqual(len(result["spam_messages"]), 1)

    def test_spam_detection_repeated_message(self):
        messages = [
            {"author": "spammer", "text": "buy followers now", "published_at": ""},
            {"author": "spammer", "text": "buy followers now", "published_at": ""},
            {"author": "spammer", "text": "buy followers now", "published_at": ""},
        ]
        result = analyze_chat(messages)
        self.assertTrue(len(result["spam_messages"]) >= 1)

    def test_spam_detection_repeated_chars(self):
        messages = [{"author": "excited", "text": "wooooooow amazing", "published_at": ""}]
        result = analyze_chat(messages)
        self.assertEqual(len(result["spam_messages"]), 1)


class TestBuildReport(unittest.TestCase):
    def test_report_with_snapshots_and_chat(self):
        snapshots = [
            {
                "video_id": "abc123",
                "title": "Test Stream",
                "concurrent_viewers": 10,
                "like_count": 5,
                "timestamp": 1.0,
            },
            {
                "video_id": "abc123",
                "title": "Test Stream",
                "concurrent_viewers": 20,
                "like_count": 8,
                "timestamp": 2.0,
            },
        ]
        chat_analysis = analyze_chat([
            {"author": "alice", "text": "great stream", "published_at": ""},
        ])
        report = build_report(snapshots, chat_analysis)
        self.assertIn("Test Stream", report)
        self.assertIn("Peak concurrent viewers:** 20", report)
        self.assertIn("Live Chat Analysis", report)

    def test_report_without_chat(self):
        snapshots = [{
            "video_id": "abc123",
            "title": "Test Stream",
            "concurrent_viewers": 10,
            "like_count": 5,
            "timestamp": 1.0,
        }]
        report = build_report(snapshots, None)
        self.assertIn("Test Stream", report)
        self.assertNotIn("Live Chat Analysis", report)


if __name__ == "__main__":
    unittest.main()
