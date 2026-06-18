import concurrent.futures
import os
import json
import requests
import time
import re
import logging
import sys
from datetime import datetime
from threading import Lock
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Tuple, Any
from dotenv import load_dotenv
import tenacity
from tenacity import retry, wait_exponential, stop_after_attempt

# Configure logging
log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "querypulse.log")

handler = RotatingFileHandler(log_file_path, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[handler, logging.StreamHandler()]
)
logger = logging.getLogger("querypulse")

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".tavily_cache.json")
cache_lock = Lock()

# Load environment variables
load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL")
FREQUENCY = os.environ.get("FREQUENCY", "weekly")
SCHEDULE = os.environ.get("SCHEDULE", FREQUENCY).lower()

# Constants & Networking
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
URL_PATTERN = re.compile(r'https?://[^\s]+')
http_session = requests.Session()
http_session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

# Model configuration
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

if not GROQ_API_KEY or not TAVILY_API_KEY:
    logger.error("GROQ_API_KEY and TAVILY_API_KEY must be set in the .env file.")
    sys.exit(1)

if not NTFY_TOPIC and not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
    logger.error("You must configure at least one notification channel.")
    sys.exit(1)

if GROQ_API_KEY == "your_groq_api_key_here" or TAVILY_API_KEY == "your_tavily_api_key_here":
    logger.error("You are still using default placeholders for your API keys.")
    sys.exit(1)

def extract_urls(text: str) -> List[str]:
    """Extracts URLs from a string using regex."""
    return URL_PATTERN.findall(text)

def should_run() -> bool:
    """Checks if the script should run based on the SCHEDULE."""
    if SCHEDULE == "always":
        return True

    last_run_file = os.path.join(os.path.dirname(__file__), ".last_run")
    if not os.path.exists(last_run_file):
        return True

    try:
        with open(last_run_file, "r") as f:
            last_run_time = float(f.read().strip())
    except (ValueError, OSError):
        return True

    schedule_map = {"hourly": 3600, "daily": 86400, "weekly": 604800, "annually": 31536000}
    required_seconds = schedule_map.get(SCHEDULE, 0)
    return (time.time() - last_run_time) >= (required_seconds - 60)

def update_last_run() -> None:
    """Updates the .last_run file with the current timestamp."""
    last_run_file = os.path.join(os.path.dirname(__file__), ".last_run")
    try:
        with open(last_run_file, "w") as f:
            f.write(str(time.time()))
    except OSError as e:
        logger.error(f"Could not update last run record: {e}")

def load_cache() -> dict:
    """Loads the search cache from disk."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Could not load cache: {e}")
        return {}

def save_cache(cache_data: dict) -> None:
    """Saves the search cache to disk atomically."""
    with cache_lock:
        temp_file = CACHE_FILE + ".tmp"
        try:
            with open(temp_file, "w") as f:
                json.dump(cache_data, f, indent=2)
            os.replace(temp_file, CACHE_FILE)
        except OSError as e:
            logger.error(f"Could not save cache: {e}")
            if os.path.exists(temp_file):
                os.remove(temp_file)

def jina_fallback(retry_state) -> None:
    logger.warning(f"Jina Reader failed: {retry_state.outcome.exception()}")

@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5), retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException), retry_error_callback=jina_fallback)
def fetch_jina_reader(url: str) -> Optional[str]:
    """Uses Jina Reader API to fetch raw text from a specific URL."""
    jina_url = f"https://r.jina.ai/{url}"
    response = http_session.get(jina_url, timeout=15)
    response.raise_for_status()
    return response.text[:3000]

def read_prompts(filename: str = "prompts.txt") -> List[str]:
    """Reads prompts from a local file."""
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(filepath, "r") as f:
            prompts = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(prompts)} prompts from {filepath}")
        return prompts
    except FileNotFoundError:
        logger.error(f"{filepath} not found.")
        return []

def tavily_fallback(retry_state) -> str:
    logger.warning(f"Tavily failed: {retry_state.outcome.exception()}")
    return "Search failed."

@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5), retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException), retry_error_callback=tavily_fallback)
def search_tavily(query: str) -> str:
    """Queries the Tavily API for context."""
    url = "https://api.tavily.com/search"
    payload = {"api_key": TAVILY_API_KEY, "query": query, "search_depth": "basic", "max_results": 3}
    response = http_session.post(url, json=payload, timeout=15)
    response.raise_for_status()
    snippets = [result.get("content", "") for result in response.json().get("results", [])]
    return "\n".join(snippets)

def check_if_stale_via_groq(prompt: str, last_timestamp: float) -> bool:
    """Uses Groq to decide if a cached result is stale."""
    if not GROQ_API_KEY: return True
    human_time = datetime.fromtimestamp(last_timestamp).strftime("%A, %B %d, %Y at %I:%M %p UTC")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "You are a factual volatility analyst. Answer ONLY 'YES' or 'NO'."},
            {"role": "user", "content": f"Is it probable that the factual answer to '{prompt}' has changed since {human_time}? Answer ONLY 'YES' or 'NO'."}
        ],
        "temperature": 0.0,
        "max_tokens": 5
    }
    try:
        response = http_session.post(url, headers=headers, json=payload, timeout=10)
        answer = response.json()['choices'][0]['message']['content'].strip().upper()
        return "NO" not in answer
    except Exception:
        return True

@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(3), retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException))
def query_groq_batch(combined_prompt: str) -> Optional[str]:
    """Evaluates prompts using Groq."""
    if not GROQ_API_KEY: return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    system_message = (
        "You are a strict, factual assistant. Evaluate the conditions based on provided search snippets. "
        "Return a JSON object: {\"fulfilled_conditions\": [\"short description of true conditions\"]}. "
        "If none are true, return {\"fulfilled_conditions\": []}."
    )
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "system", "content": system_message}, {"role": "user", "content": combined_prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }
    response = http_session.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']

def safe_json_parse(text: str) -> List[str]:
    """Attempts to extract and parse a JSON array from the AI's response text."""
    if not text: return []
    text = text.strip()
    try:
        data = json.loads(text)
        return _extract_list_from_data(data)
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r'```(?:json)?\s*(.*?)\s*```', r'\1', text, flags=re.DOTALL).strip()
    try:
        data = json.loads(cleaned)
        return _extract_list_from_data(data)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\[.*?\]', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return _extract_list_from_data(data)
        except json.JSONDecodeError:
            pass
    return []

def _extract_list_from_data(data: Any) -> List[str]:
    """Helper to extract a list of strings from parsed JSON data."""
    if isinstance(data, list): return [str(item) for item in data]
    if isinstance(data, dict):
        for val in data.values():
            if isinstance(val, list): return [str(item) for item in val]
    return []

def _fetch_context_for_single_prompt(prompt: str, cache: dict) -> Tuple[str, str, Optional[dict]]:
    """Fetches context using Jina, Cache, or Tavily."""
    urls = extract_urls(prompt)
    if urls:
        context = fetch_jina_reader(urls[0])
        if context: return prompt, context, None

    cached_entry = cache.get(prompt)
    if cached_entry and not check_if_stale_via_groq(prompt, cached_entry['timestamp']):
        return prompt, cached_entry['context'], None

    context = search_tavily(prompt)
    return prompt, context, {"context": context, "timestamp": time.time(), "human_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}

def process_prompts_in_batches(prompts: List[str], batch_size: int = 10) -> List[str]:
    """Processes prompts in batches."""
    all_alerts, cache = [], load_cache()
    unique_prompts = list(dict.fromkeys(prompts))
    batches = [unique_prompts[i:i + batch_size] for i in range(0, len(unique_prompts), batch_size)]

    for i, batch in enumerate(batches):
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(batch), 10)) as executor:
            results = list(executor.map(lambda p: _fetch_context_for_single_prompt(p, cache), batch))

        prompt_parts = ["Evaluate these conditions based on context:\n\n"]
        new_entries = {}
        for original_prompt, context, new_entry in results:
            prompt_parts.append(f"Prompt: {original_prompt}\nContext: {context}\n\n")
            if new_entry: new_entries[original_prompt] = new_entry

        if new_entries:
            cache.update(new_entries)
            save_cache(cache)

        response_text = query_groq_batch("".join(prompt_parts))
        if response_text:
            all_alerts.extend(safe_json_parse(response_text))
        
        if i < len(batches) - 1:
            logger.info("Waiting 5 seconds before next batch...")
            time.sleep(5)

    return all_alerts

@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5), retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException))
def send_telegram_notification(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    http_session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=15).raise_for_status()

@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5), retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException))
def send_ntfy_notification(message: str) -> None:
    if not NTFY_TOPIC: return
    http_session.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), timeout=15).raise_for_status()

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3), retry=tenacity.retry_if_exception_type(requests.exceptions.RequestException))
def ping_healthcheck() -> None:
    if not HEALTHCHECK_URL: return
    http_session.get(HEALTHCHECK_URL, timeout=10).raise_for_status()

def main() -> None:
    if not should_run(): return
    logger.info("Starting QueryPulse (Groq-Only Mode)")
    prompts = read_prompts()
    if not prompts: return

    try:
        alerts = process_prompts_in_batches(prompts, batch_size=10)
        if alerts:
            msg = f"{FREQUENCY.capitalize()} Updates:\n" + "\n".join(f"- {a}" for a in alerts)
            send_ntfy_notification(msg)
            send_telegram_notification(msg)
        
        ping_healthcheck()
        update_last_run()
    except Exception as e:
        logger.exception(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
