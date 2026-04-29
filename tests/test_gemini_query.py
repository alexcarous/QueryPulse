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

def test_extract_urls():
    text = "Check out https://google.com and http://example.com/path. Also https://github.com."
    urls = gemini_query.extract_urls(text)
    assert urls == ["https://google.com", "http://example.com/path.", "https://github.com."]
