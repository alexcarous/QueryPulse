import sys
import os
import unittest
from unittest.mock import patch, MagicMock, mock_open

# 1. Set required environment variables BEFORE importing the module
os.environ["GROQ_API_KEY"] = "fake_key_for_testing"
os.environ["TAVILY_API_KEY"] = "fake_key_for_testing"
os.environ["NTFY_TOPIC"] = "fake_topic"

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

    @patch("querypulse.gspread.service_account")
    def test_fetch_google_sheets_prompts_success(self, mock_service_account):
        mock_gc = MagicMock()
        mock_sh = MagicMock()
        mock_worksheet = MagicMock()
        
        mock_service_account.return_value = mock_gc
        mock_gc.open_by_key.return_value = mock_sh
        mock_sh.sheet1 = mock_worksheet
        
        mock_worksheet.get_all_records.return_value = [
            {"Prompt": "Is it raining?", "Status": "Active"},
            {"Prompt": "Always active prompt", "Status": ""}
        ]
        
        prompts = querypulse.fetch_google_sheets_prompts()
        self.assertEqual(prompts, ["Is it raining?", "Always active prompt"])

    @patch("querypulse.query_groq_batch")
    @patch("querypulse._fetch_context_for_single_prompt")
    def test_process_prompts_in_batches_success(self, mock_fetch_context, mock_query_groq):
        # Setup mocks
        prompts = ["prompt1", "prompt2"]
        mock_fetch_context.side_effect = [("prompt1", "context1", None), ("prompt2", "context2", None)]
        
        # Simulate a successful JSON response from Groq
        mock_query_groq.return_value = '["Condition 1 met", "Condition 2 met"]'
        
        alerts = querypulse.process_prompts_in_batches(prompts, batch_size=2)
        
        self.assertEqual(alerts, ["Condition 1 met", "Condition 2 met"])
        self.assertEqual(mock_fetch_context.call_count, 2)
        mock_query_groq.assert_called_once()

if __name__ == '__main__':
    unittest.main()
