import concurrent.futures
import os
import json
import requests
import time
import re
import logging
import sys
import gspread
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Tuple, Any
from dotenv import load_dotenv
from google import genai
from google.genai import types
import tenacity
from tenacity import retry, wait_exponential, stop_after_attempt

# Configure logging
log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "querypulse.log")

# Use RotatingFileHandler to prevent logs from growing indefinitely
handler = RotatingFileHandler(log_file_path, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        handler,
        logging.StreamHandler()
    ]
)
# Use a specific logger to avoid capturing noisy third-party debug logs (e.g., urllib3)
logger = logging.getLogger("querypulse")

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL")
GOOGLE_CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE")
GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME")

# Model configuration with defaults
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GROQ_MODEL_SETTING = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

def resolve_groq_model(requested_model: str) -> str:
    """
    Resolves a requested Groq model name. 
    If 'latest' or 'flash' is requested, it tries to pick the best current performance model.
    """
    if requested_model.lower() in ["latest", "flash", "groq-flash-latest"]:
        # In a real scenario, we could query /v1/models here.
        # For robustness, we'll return a prioritized stable ID known to be high-performance.
        return "llama-3.3-70b-versatile" 
    return requested_model

GROQ_MODEL = resolve_groq_model(GROQ_MODEL_SETTING)

# Constants
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Pre-compile regex for performance
URL_PATTERN = re.compile(r'https?://[^\s]+')

# Use a global session to enable HTTP Keep-Alive (connection pooling)
# This significantly reduces latency by skipping TLS handshakes on repeat requests
http_session = requests.Session()
http_session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

if not GEMINI_API_KEY or not TAVILY_API_KEY:
    logger.error("GEMINI_API_KEY and TAVILY_API_KEY must be set in the .env file.")
    sys.exit(1)

if not NTFY_TOPIC and not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
    logger.error("You must configure at least one notification channel (NTFY_TOPIC or Telegram credentials).")
    sys.exit(1)

if GEMINI_API_KEY == "your_gemini_api_key_here" or TAVILY_API_KEY == "your_tavily_api_key_here":
    logger.error("You are still using default placeholders for your API keys. Please edit the .env file and add your real keys.")
    sys.exit(1)

# Initialize Gemini Client
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Error initializing Gemini client: {e}")
    sys.exit(1)

def extract_urls(text: str) -> List[str]:
    """Extracts URLs from a string using regex."""
    return URL_PATTERN.findall(text)

def jina_fallback(retry_state) -> None:
    logger.warning(f"Jina Reader failed after {retry_state.attempt_number} attempts: {retry_state.outcome.exception()}")
    return None

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException),
    retry_error_callback=jina_fallback
)
def fetch_jina_reader(url: str) -> Optional[str]:
    """Uses Jina Reader API to fetch raw text from a specific URL."""
    logger.info(f"Fetching Jina Reader for URL: {url}")
    jina_url = f"https://r.jina.ai/{url}"
    
    response = http_session.get(jina_url, timeout=15)
    response.raise_for_status()
    text = response.text[:3000] # Cap context size to prevent massive prompts
    logger.info(f"Jina Reader success: fetched {len(text)} characters.")
    return text

def sheets_fallback(retry_state) -> List[str]:
    logger.error(f"Failed to fetch prompts from Google Sheets after {retry_state.attempt_number} attempts: {retry_state.outcome.exception()}")
    return []

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    retry=tenacity.retry_if_exception_type(Exception),
    retry_error_callback=sheets_fallback
)
def fetch_google_sheets_prompts() -> List[str]:
    """Authenticates with Google and reads active prompts from a specific sheet."""
    logger.info(f"Connecting to Google Sheets API to read '{GOOGLE_SHEET_NAME}'...")
    
    try:
        gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_FILE)
    except FileNotFoundError:
        logger.error(f"Google credentials file '{GOOGLE_CREDENTIALS_FILE}' not found.")
        return []
        
    try:
        # Try opening by key/ID first (more robust)
        try:
            sh = gc.open_by_key(GOOGLE_SHEET_NAME)
        except (gspread.exceptions.APIError, gspread.exceptions.SpreadsheetNotFound):
            # Fallback to opening by exact title
            sh = gc.open(GOOGLE_SHEET_NAME)
            
        worksheet = sh.sheet1 
    except (gspread.exceptions.SpreadsheetNotFound, gspread.exceptions.APIError) as e:
         logger.error(f"Spreadsheet '{GOOGLE_SHEET_NAME}' not found or inaccessible: {e}")
         return []

    records = worksheet.get_all_records()
    
    active_prompts = []
    for row in records:
        # Create a lowercase mapping of the row keys for case-insensitive lookup
        row_lower = {str(k).lower().strip(): v for k, v in row.items()}
        
        # Look for 'status' or 'statuses'
        status = str(row_lower.get('status', row_lower.get('statuses', ''))).strip().lower()
        
        # Consider it active if it's 'active', 'true', or empty
        if status in ('active', 'true', '') or not status:
            # Look for 'prompt' or 'prompts'
            prompt = str(row_lower.get('prompt', row_lower.get('prompts', ''))).strip()
            if prompt:
                active_prompts.append(prompt)
                
    logger.info(f"Successfully loaded {len(active_prompts)} prompts from Google Sheets.")
    return active_prompts

def read_prompts(filename: str = "prompts.txt") -> List[str]:
    """Reads prompts from Google Sheets if configured, otherwise falls back to local file."""
    if GOOGLE_CREDENTIALS_FILE and GOOGLE_SHEET_NAME:
        prompts = fetch_google_sheets_prompts()
        if prompts:
            return prompts
        logger.warning("Google Sheets fetch failed or returned no prompts. Falling back to local file.")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(script_dir, filename)
    try:
        with open(filepath, "r") as f:
            prompts = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(prompts)} prompts from {filepath}")
        return prompts
    except FileNotFoundError:
        logger.error(f"{filepath} not found.")
        return [] # Return empty instead of exiting immediately to let main handle it

def tavily_fallback(retry_state) -> str:
    logger.warning(f"Tavily search failed after {retry_state.attempt_number} attempts: {retry_state.outcome.exception()}")
    return "Search failed. Do your best to evaluate based on your internal knowledge."

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException),
    retry_error_callback=tavily_fallback
)
def search_tavily(query: str) -> str:
    """Queries the Tavily API to get search context for a prompt."""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
        "max_results": 3
    }

    response = http_session.post(url, json=payload, timeout=15)
    response.raise_for_status()
    data = response.json()

    # Combine snippets from the results
    snippets = []
    for result in data.get("results", []):
        snippets.append(result.get("content", ""))

    snippet_text = "\n".join(snippets)
    logger.info(f"Tavily search successful. Fetched {len(snippet_text)} characters of context.")
    return snippet_text

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    # Only retry if it is NOT a client error (e.g., don't retry 400 or 403 errors)
    retry=tenacity.retry_if_not_exception_type(genai.errors.ClientError)
)
def query_gemini_batch(combined_prompt: str) -> str:
    """Queries Gemini to evaluate a pre-constructed prompt containing search context."""
    system_instruction = (
        "You are a strict, factual assistant that evaluates a list of conditional queries based on the provided CURRENT, REAL-TIME search snippets, and by utilizing your Google Search tool if necessary. "
        "For each query, read its associated search context carefully and verify the factual reality using your tools. "
        "You must be absolutely certain the condition is TRUE right now. If it is speculative, future-looking, or simply discussing the topic without the condition being met, it is FALSE. "
        "If the condition is explicitly TRUE, create a short, concise, single-sentence string explaining what was fulfilled (e.g., 'BTC is now over $100k'). "
        "If the condition is FALSE, or if you cannot definitively verify it is true right now, DO NOT include it in your output. "
        "Your final output MUST be a valid JSON array of these short strings. "
        "If NONE of the conditions are true, output an empty JSON array `[]`. "
        "Do not include Markdown formatting like ```json ... ```, just the raw JSON array."
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=combined_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1, # Keep it deterministic
            tools=[{"google_search": {}}], # Enable Google Search Grounding
        )
    )
    
    # Handle Safety Filters
    if not response.candidates or not response.candidates[0].content.parts:
        logger.warning("Gemini response was blocked or empty (likely safety filters).")
        return "[]"

    return response.text

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
    retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException)
)
def query_groq_batch(combined_prompt: str) -> Optional[str]:
    """Fallback function: queries Groq if Gemini fails."""
    if not GROQ_API_KEY:
        return None

    logger.info("Initiating Groq fallback...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    system_message = (
        "You are a strict, factual assistant that evaluates a list of conditional queries based on the provided CURRENT, REAL-TIME search snippets. "
        "For each query, read its associated search context carefully. "
        "You must be absolutely certain the condition is TRUE right now. If it is speculative, future-looking, or simply discussing the topic without the condition being met, it is FALSE. "
        "If the condition is explicitly TRUE, create a short, concise, single-sentence string explaining what was fulfilled (e.g., 'BTC is now over $100k'). "
        "If the condition is FALSE, or if you cannot definitively verify it is true right now, DO NOT include it in your output. "
        "Your final output MUST be a valid JSON object with a single key \"fulfilled_conditions\" containing an array of these short strings. "
        "If NONE of the conditions are true, output `{\"fulfilled_conditions\": []}`. "
        "Do not include Markdown formatting like ```json ... ```, just the raw JSON object."
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": combined_prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    response = http_session.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data['choices'][0]['message']['content']

def safe_json_parse(text: str) -> List[str]:
    """Attempts to extract and parse a JSON array from the AI's response text."""
    if not text:
        return []

    text = text.strip()
    
    # 1. Direct parse attempt
    try:
        data = json.loads(text)
        return _extract_list_from_data(data)
    except json.JSONDecodeError:
        pass

    # 2. Try cleaning markdown if present
    cleaned = re.sub(r'```(?:json)?\s*(.*?)\s*```', r'\1', text, flags=re.DOTALL).strip()
    try:
        data = json.loads(cleaned)
        return _extract_list_from_data(data)
    except json.JSONDecodeError:
        pass

    # 3. Regex-based array extraction as a last resort
    # Use non-greedy matching `.*?` to avoid grabbing multiple separate blocks incorrectly
    match = re.search(r'\[.*?\]', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return _extract_list_from_data(data)
        except json.JSONDecodeError:
            pass

    logger.error(f"Failed to parse AI response as JSON: {text[:200]}...")
    return []

def _extract_list_from_data(data: Any) -> List[str]:
    """Helper to extract a list of strings from parsed JSON data (either list or dict)."""
    if isinstance(data, list):
        return [str(item) for item in data]
    
    if isinstance(data, dict):
        # Groq might return {"fulfilled_conditions": [...]}
        for val in data.values():
            if isinstance(val, list):
                return [str(item) for item in val]
    
    return []

def _fetch_context_for_single_prompt(prompt: str) -> Tuple[str, str]:
    """Helper function to fetch context for a single prompt."""
    urls = extract_urls(prompt)
    context = fetch_jina_reader(urls[0]) if urls else ""
    if not context:
        context = search_tavily(prompt)
    return prompt, context

def process_prompts_in_batches(prompts: List[str], batch_size: int = 10) -> List[str]:
    """Processes prompts in batches to avoid giant requests while minimizing API calls."""
    all_alerts = []
    batches = [prompts[i:i + batch_size] for i in range(0, len(prompts), batch_size)]

    for i, batch in enumerate(batches):
        logger.info(f"Preparing context for Batch {i+1}/{len(batches)}...")

        # Concurrency: HTTP requests are I/O bound, we don't need to limit by CPU cores.
        # Cap at 10 to be respectful to third-party APIs.
        max_workers = min(batch_size, 10)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            context_results = list(executor.map(_fetch_context_for_single_prompt, batch))

        prompt_parts = ["Please evaluate the following conditions based on their provided search context:\n\n"]
        for j, (original_prompt, context) in enumerate(context_results):
            prompt_parts.append(f"Condition {j+1}: {original_prompt}\nSearch Context: {context}\n\n")

        combined_prompt = "".join(prompt_parts)
        logger.info(f"Combined prompt for batch {i+1} built. Length: {len(combined_prompt)}")

        logger.info(f"Querying Gemini (Batch {i+1}/{len(batches)})...")
        response_text = None
        try:
            response_text = query_gemini_batch(combined_prompt)
        except tenacity.RetryError as e:
            exception = e.last_attempt.exception()
            logger.error(f"Gemini failed after retries for batch {i+1}: {type(exception).__name__} - {exception}")
        except genai.errors.ClientError as e:
            logger.error(f"Gemini client error for batch {i+1}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error when querying Gemini for batch {i+1}: {e}")

        if not response_text:
            try:
                response_text = query_groq_batch(combined_prompt)
            except Exception as e:
                logger.error(f"Groq fallback failed after retries for batch {i+1}: {e}")
        
        if response_text:
            logger.info(f"Raw response for batch {i+1} received.")
            batch_alerts = safe_json_parse(response_text)
            all_alerts.extend(batch_alerts)
        else:
            logger.error(f"Both Gemini and Groq failed for batch {i+1}. Skipping batch.")

        if i < len(batches) - 1:
            logger.info("Waiting 5 seconds before next batch to prevent rate limits...")
            time.sleep(5)

    return all_alerts

def notification_fallback(retry_state) -> None:
    logger.error(f"Notification failed after {retry_state.attempt_number} attempts: {retry_state.outcome.exception()}")
    return None

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException),
    retry_error_callback=notification_fallback
)
def send_telegram_notification(message: str) -> None:
    """Sends a notification to the configured Telegram Chat ID."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    logger.info("Sending notification via Telegram...")
    response = http_session.post(url, json=payload, timeout=15)
    response.raise_for_status()
    logger.info("Telegram notification sent successfully.")

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException),
    retry_error_callback=notification_fallback
)
def send_ntfy_notification(message: str) -> None:
    """Sends a notification to the configured ntfy topic."""
    if not NTFY_TOPIC:
        return

    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    logger.info(f"Sending notification to {url}...")
    response = http_session.post(url, data=message.encode('utf-8'), timeout=15)
    response.raise_for_status()
    logger.info("Ntfy notification sent successfully.")

def send_all_notifications(message: str) -> None:
    """Sends a notification message to all configured channels."""
    send_ntfy_notification(message)
    send_telegram_notification(message)

@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException)
)
def ping_healthcheck() -> None:
    """Pings Healthchecks.io if configured to signal a successful run."""
    if not HEALTHCHECK_URL:
        return

    logger.info("Pinging Healthchecks.io...")
    response = http_session.get(HEALTHCHECK_URL, timeout=10)
    # This was previously missing: if it returns a 500, we want it to retry
    response.raise_for_status() 
    logger.info("Healthcheck ping successful.")

def main() -> None:
    logger.info("Starting QueryPulse")
    prompts = read_prompts()
    if not prompts:
        logger.warning("No prompts found in prompts.txt. Exiting.")
        return

    try:
        alerts = process_prompts_in_batches(prompts, batch_size=10)

        if alerts:
            notification_message = f"{FREQUENCY.capitalize()} Updates:\n" + "\n".join(f"- {alert}" for alert in alerts)
            logger.info("Conditions met. Sending notifications...")
            send_all_notifications(notification_message)
        else:
            logger.info("No conditions were met. No notification sent.")

        ping_healthcheck()

    except Exception as e:
         logger.exception(f"A fatal error occurred during processing: {e}")

if __name__ == "__main__":
    main()