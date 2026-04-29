import sys
import os
from unittest.mock import MagicMock, patch

# Define a real exception for the mock to use
class MockRequestException(Exception):
    pass

# Mock dependencies before importing gemini_query
mock_requests = MagicMock()
mock_requests.exceptions.RequestException = MockRequestException
sys.modules['requests'] = mock_requests

sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()
sys.modules['tenacity'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

# Set up environment variables required by gemini_query
os.environ["GEMINI_API_KEY"] = "fake_gemini_key"
os.environ["TAVILY_API_KEY"] = "fake_tavily_key"
os.environ["NTFY_TOPIC"] = "fake_ntfy_topic"
os.environ["TELEGRAM_BOT_TOKEN"] = "fake_telegram_token"
os.environ["TELEGRAM_CHAT_ID"] = "fake_chat_id"
os.environ["HEALTHCHECK_URL"] = "https://hc-ping.com/fake_hc_url"

import gemini_query

def test_telegram_token_redaction():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    exception_msg = f"Error connecting to https://api.telegram.org/bot{token}/sendMessage"

    with patch("gemini_query.logging.error") as mock_log_error:
        gemini_query.requests.post.side_effect = MockRequestException(exception_msg)

        gemini_query.send_telegram_notification("test message")

        mock_log_error.assert_called()
        logged_msg = mock_log_error.call_args[0][0]

        assert token not in logged_msg
        assert "[REDACTED]" in logged_msg

def test_ntfy_topic_redaction():
    topic = os.environ["NTFY_TOPIC"]
    exception_msg = f"Error connecting to https://ntfy.sh/{topic}"

    with patch("gemini_query.logging.error") as mock_log_error:
        gemini_query.requests.post.side_effect = MockRequestException(exception_msg)

        gemini_query.send_ntfy_notification("test message")

        mock_log_error.assert_called()
        logged_msg = mock_log_error.call_args[0][0]

        assert topic not in logged_msg
        assert "[REDACTED]" in logged_msg

def test_healthcheck_url_redaction():
    hc_url = os.environ["HEALTHCHECK_URL"]
    exception_msg = f"Error connecting to {hc_url}"

    with patch("gemini_query.logging.warning") as mock_log_warning:
        gemini_query.requests.get.side_effect = MockRequestException(exception_msg)

        gemini_query.ping_healthcheck()

        mock_log_warning.assert_called()
        logged_msg = mock_log_warning.call_args[0][0]

        assert hc_url not in logged_msg
        assert "[REDACTED]" in logged_msg
