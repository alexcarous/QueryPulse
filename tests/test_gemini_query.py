import sys
import os
from unittest.mock import MagicMock, patch, mock_open

# Mock dependencies before importing gemini_query
sys.modules['requests'] = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()
sys.modules['tenacity'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

# Set up environment variables required by gemini_query
os.environ["GEMINI_API_KEY"] = "fake_key"
os.environ["TAVILY_API_KEY"] = "fake_key"
os.environ["NTFY_TOPIC"] = "fake_topic"

import gemini_query

def test_read_prompts_success():
    mock_data = "prompt1\n\nprompt2\n"
    with patch("builtins.open", mock_open(read_data=mock_data)):
        prompts = gemini_query.read_prompts("fake_prompts.txt")
        assert prompts == ["prompt1", "prompt2"]

def test_read_prompts_file_not_found():
    with patch("builtins.open", side_effect=FileNotFoundError):
        # gemini_query uses exit(1) which is a builtin if sys.exit is not imported
        with patch("gemini_query.exit") as mock_exit:
            with patch("gemini_query.logging.error") as mock_logging_error:
                gemini_query.read_prompts("non_existent.txt")
                mock_logging_error.assert_called_once_with("non_existent.txt not found.")
                mock_exit.assert_called_once_with(1)

# Create a proper exception class since requests is mocked
class MockRequestException(Exception):
    pass

def test_ping_healthcheck_success():
    with patch("gemini_query.HEALTHCHECK_URL", "http://fake-url"):
        with patch("gemini_query.requests.get") as mock_get:
            gemini_query.ping_healthcheck()
            mock_get.assert_called_once_with("http://fake-url", timeout=10)

def test_ping_healthcheck_no_url():
    with patch("gemini_query.HEALTHCHECK_URL", ""):
        with patch("gemini_query.requests.get") as mock_get:
            gemini_query.ping_healthcheck()
            mock_get.assert_not_called()

def test_ping_healthcheck_exception():
    with patch("gemini_query.HEALTHCHECK_URL", "http://fake-url"):
        with patch("gemini_query.requests.get") as mock_get:
            mock_get.side_effect = MockRequestException("fake error")
            # We need to temporarily assign our mock exception to the module's requests.exceptions.RequestException
            original_exception = gemini_query.requests.exceptions.RequestException
            gemini_query.requests.exceptions.RequestException = MockRequestException

            try:
                with patch("gemini_query.logging.warning") as mock_warning:
                    gemini_query.ping_healthcheck()
                    mock_warning.assert_called_once_with("Failed to ping Healthchecks.io: fake error")
            finally:
                # Restore the original just in case
                gemini_query.requests.exceptions.RequestException = original_exception
