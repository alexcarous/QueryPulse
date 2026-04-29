import sys
import os
from unittest.mock import MagicMock, patch

class FakeRequestException(Exception): pass

# Provide requests module mocking properly for exception catching
mock_requests = MagicMock()
mock_requests.exceptions.RequestException = FakeRequestException
sys.modules['requests'] = mock_requests

sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()
sys.modules['tenacity'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

# Set up environment variables required by gemini_query
os.environ["GEMINI_API_KEY"] = "fake_key"
os.environ["TAVILY_API_KEY"] = "fake_key"
os.environ["NTFY_TOPIC"] = "fake_topic"
os.environ["TELEGRAM_BOT_TOKEN"] = "fake_bot_token"
os.environ["TELEGRAM_CHAT_ID"] = "fake_chat_id"

import gemini_query

def test_send_ntfy_notification_success():
    with patch("gemini_query.requests.post") as mock_post:
        mock_response = MagicMock()
        mock_post.return_value = mock_response
        with patch("gemini_query.logging.info") as mock_logging_info:
            gemini_query.send_ntfy_notification("Test Message")
            mock_post.assert_called_once_with(
                "https://ntfy.sh/fake_topic",
                data=b"Test Message",
                timeout=15
            )
            mock_response.raise_for_status.assert_called_once()
            mock_logging_info.assert_any_call("Sending notification via ntfy...")
            mock_logging_info.assert_any_call("Ntfy notification sent successfully.")

def test_send_ntfy_notification_failure():
    with patch("gemini_query.requests.post") as mock_post:
        mock_post.side_effect = gemini_query.requests.exceptions.RequestException("fake error details including https://ntfy.sh/fake_topic")
        with patch("gemini_query.logging.error") as mock_logging_error:
            gemini_query.send_ntfy_notification("Test Message")
            mock_logging_error.assert_called_once_with("Failed to send ntfy notification: FakeRequestException")

def test_send_telegram_notification_success():
    with patch("gemini_query.requests.post") as mock_post:
        mock_response = MagicMock()
        mock_post.return_value = mock_response
        with patch("gemini_query.logging.info") as mock_logging_info:
            gemini_query.send_telegram_notification("Test Message")
            mock_post.assert_called_once_with(
                "https://api.telegram.org/botfake_bot_token/sendMessage",
                json={"chat_id": "fake_chat_id", "text": "Test Message"},
                timeout=15
            )
            mock_response.raise_for_status.assert_called_once()
            mock_logging_info.assert_any_call("Sending notification via Telegram...")
            mock_logging_info.assert_any_call("Telegram notification sent successfully.")

def test_send_telegram_notification_failure():
    with patch("gemini_query.requests.post") as mock_post:
        mock_post.side_effect = gemini_query.requests.exceptions.RequestException("fake error details including fake_bot_token")
        with patch("gemini_query.logging.error") as mock_logging_error:
            gemini_query.send_telegram_notification("Test Message")
            mock_logging_error.assert_called_once_with("Failed to send Telegram notification: FakeRequestException")
