import sys
import os
import unittest
from unittest.mock import patch, MagicMock, mock_open

# 1. Set required environment variables BEFORE importing the module
os.environ["GEMINI_API_KEY"] = "fake_key_for_testing"
os.environ["TAVILY_API_KEY"] = "fake_key_for_testing"
os.environ["NTFY_TOPIC"] = "fake_topic"

# 2. Mock heavy dependencies that make network calls or have side effects on import
# We do this before importing querypulse to prevent initialization side-effects
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()

import querypulse

class TestQueryPulse(unittest.TestCase):

    def test_extract_urls(self):
        text = "Check out this link: https://example.com and this one http://test.org/path"
        urls = querypulse.extract_urls(text)
        self.assertEqual(urls, ["https://example.com", "http://test.org/path"])

    def test_extract_urls_no_urls(self):
        text = "There are no links here."
        urls = querypulse.extract_urls(text)
        self.assertEqual(urls, [])

    def test_safe_json_parse_clean_array(self):
        text = '["Result 1", "Result 2"]'
        results = querypulse.safe_json_parse(text)
        self.assertEqual(results, ["Result 1", "Result 2"])

    def test_safe_json_parse_markdown(self):
        text = 'Here is the data: ```json\n["Result 1"]\n``` Hope this helps.'
        results = querypulse.safe_json_parse(text)
        self.assertEqual(results, ["Result 1"])

    def test_safe_json_parse_dict_fallback(self):
        text = '{"fulfilled_conditions": ["Result 1"]}'
        results = querypulse.safe_json_parse(text)
        self.assertEqual(results, ["Result 1"])

    def test_safe_json_parse_invalid(self):
        text = "This is not JSON at all."
        results = querypulse.safe_json_parse(text)
        self.assertEqual(results, [])

    @patch("querypulse.GOOGLE_CREDENTIALS_FILE", None)
    @patch("querypulse.GOOGLE_SHEET_NAME", None)
    @patch("builtins.open", new_callable=mock_open, read_data="prompt1\n\nprompt2\n")
    def test_read_prompts_success(self, mock_file):
        prompts = querypulse.read_prompts("fake_prompts.txt")
        self.assertEqual(prompts, ["prompt1", "prompt2"])
        mock_file.assert_called_once()

    @patch("querypulse.GOOGLE_CREDENTIALS_FILE", None)
    @patch("querypulse.GOOGLE_SHEET_NAME", None)
    @patch("builtins.open", side_effect=FileNotFoundError)
    @patch("querypulse.sys.exit")
    @patch("querypulse.logger.error")
    def test_read_prompts_file_not_found(self, mock_logger_error, mock_sys_exit, mock_file):
        prompts = querypulse.read_prompts("non_existent.txt")
        self.assertEqual(prompts, [])
        mock_logger_error.assert_called_once()
        # Should NOT exit anymore, returns empty list
        mock_sys_exit.assert_not_called()

    @patch("querypulse.gspread.service_account")
    def test_fetch_google_sheets_prompts_success(self, mock_service_account):
        mock_gc = MagicMock()
        mock_sh = MagicMock()
        mock_worksheet = MagicMock()
        
        mock_service_account.return_value = mock_gc
        # Success on the first try (open_by_key)
        mock_gc.open_by_key.return_value = mock_sh
        mock_sh.sheet1 = mock_worksheet
        
        # Simulate sheet with one active, one inactive, and one empty status (defaults to active)
        mock_worksheet.get_all_records.return_value = [
            {"Prompt": "Is it raining?", "Status": "Active"},
            {"Prompt": "Is BTC > 100k?", "Status": "inactive"},
            {"Prompt": "Always active prompt", "Status": ""}
        ]
        
        prompts = querypulse.fetch_google_sheets_prompts()
        self.assertEqual(prompts, ["Is it raining?", "Always active prompt"])
        mock_service_account.assert_called_once()

    @patch("querypulse.GOOGLE_CREDENTIALS_FILE", "fake.json")
    @patch("querypulse.GOOGLE_SHEET_NAME", "Fake Sheet")
    @patch("querypulse.fetch_google_sheets_prompts")
    def test_read_prompts_uses_sheets(self, mock_fetch):
        mock_fetch.return_value = ["sheet_prompt"]
        prompts = querypulse.read_prompts()
        self.assertEqual(prompts, ["sheet_prompt"])
        mock_fetch.assert_called_once()

    @patch("querypulse.GOOGLE_CREDENTIALS_FILE", "fake.json")
    @patch("querypulse.GOOGLE_SHEET_NAME", "Fake Sheet")
    @patch("querypulse.fetch_google_sheets_prompts")
    @patch("builtins.open", new_callable=mock_open, read_data="local_prompt")
    def test_read_prompts_fallback_to_local_on_sheets_failure(self, mock_file, mock_fetch):
        # Sheets returns nothing (failure or empty)
        mock_fetch.return_value = []
        prompts = querypulse.read_prompts()
        self.assertEqual(prompts, ["local_prompt"])
        mock_fetch.assert_called_once()
        mock_file.assert_called_once()

    @patch("querypulse.query_gemini_batch")
    @patch("querypulse._fetch_context_for_single_prompt")
    def test_process_prompts_in_batches_success(self, mock_fetch_context, mock_query_gemini):
        # Setup mocks
        prompts = ["prompt1", "prompt2"]
        mock_fetch_context.side_effect = [("prompt1", "context1", None), ("prompt2", "context2", None)]
        
        # Simulate a successful JSON response from Gemini
        mock_query_gemini.return_value = '["Condition 1 met", "Condition 2 met"]'
        
        alerts = querypulse.process_prompts_in_batches(prompts, batch_size=2)
        
        self.assertEqual(alerts, ["Condition 1 met", "Condition 2 met"])
        self.assertEqual(mock_fetch_context.call_count, 2)
        mock_query_gemini.assert_called_once()

    @patch("querypulse.query_gemini_batch")
    @patch("querypulse._fetch_context_for_single_prompt")
    def test_process_prompts_in_batches_markdown_json(self, mock_fetch_context, mock_query_gemini):
        prompts = ["prompt1"]
        mock_fetch_context.side_effect = [("prompt1", "context1", None)]
        
        # Simulate AI returning markdown-wrapped JSON
        mock_query_gemini.return_value = '```json\n["Alert 1"]\n```'
        
        alerts = querypulse.process_prompts_in_batches(prompts, batch_size=1)
        self.assertEqual(alerts, ["Alert 1"])

    @patch("querypulse.query_gemini_batch")
    @patch("querypulse.query_groq_batch")
    @patch("querypulse._fetch_context_for_single_prompt")
    def test_process_prompts_in_batches_gemini_fails_groq_succeeds(self, mock_fetch_context, mock_query_groq, mock_query_gemini):
        prompts = ["prompt1"]
        mock_fetch_context.side_effect = [("prompt1", "context1", None)]
        
        # Setup Gemini to fail
        mock_query_gemini.return_value = None
        # Setup Groq to succeed with a dict format (sometimes returned in JSON mode)
        mock_query_groq.return_value = '{"fulfilled_conditions": ["Groq Alert"]}'
        
        # Need to ensure GROQ_API_KEY is set in the module scope for fallback to trigger
        with patch("querypulse.GROQ_API_KEY", "fake_groq_key"):
            alerts = querypulse.process_prompts_in_batches(prompts, batch_size=1)
        
        self.assertEqual(alerts, ["Groq Alert"])
        mock_query_gemini.assert_called_once()
        mock_query_groq.assert_called_once()

if __name__ == '__main__':
    unittest.main()