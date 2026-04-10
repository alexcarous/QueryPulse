import os
import json
import requests
import time
import re
import logging
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tenacity import retry, wait_exponential, stop_after_attempt
from pydantic import BaseModel
import tenacity

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("gemini.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL")

if not GEMINI_API_KEY or not TAVILY_API_KEY:
    logging.error("GEMINI_API_KEY and TAVILY_API_KEY must be set in the .env file.")
    exit(1)

if not NTFY_TOPIC and not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
    logging.error("You must configure at least one notification channel (NTFY_TOPIC or Telegram credentials).")
    exit(1)

if GEMINI_API_KEY == "your_gemini_api_key_here" or TAVILY_API_KEY == "your_tavily_api_key_here":
    logging.error("You are still using default placeholders for your API keys. Please edit the .env file and add your real keys.")
    exit(1)

# Initialize Gemini Client
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logging.error(f"Error initializing Gemini client: {e}")
    exit(1)

def extract_urls(text):
    """Extracts URLs from a string using regex."""
    url_pattern = re.compile(r'https?://[^\s]+')
    return url_pattern.findall(text)

def fetch_jina_reader(url):
    """Uses Jina Reader API to fetch raw text from a specific URL."""
    logging.info(f"Fetching Jina Reader for URL: {url}")
    jina_url = f"https://r.jina.ai/{url}"
    try:
        response = requests.get(jina_url, timeout=15)
        response.raise_for_status()
        text = response.text[:3000] # Cap context size to prevent massive prompts
        logging.info(f"Jina Reader success: fetched {len(text)} characters.")
        return text
    except requests.exceptions.RequestException as e:
        logging.warning(f"Jina Reader failed for '{url}': {e}")
        return None

def read_prompts(filename="prompts.txt"):
    """Reads prompts from the specified file, ignoring empty lines."""
    try:
        with open(filename, "r") as f:
            prompts = [line.strip() for line in f if line.strip()]
        logging.info(f"Loaded {len(prompts)} prompts from {filename}")
        return prompts
    except FileNotFoundError:
        logging.error(f"{filename} not found.")
        exit(1)

def search_tavily(query):
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

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

        # Combine snippets from the results
        snippets = []
        for result in data.get("results", []):
            snippets.append(result.get("content", ""))

        snippet_text = "\n".join(snippets)
        logging.info(f"Tavily search successful. Fetched {len(snippet_text)} characters of context.")
        return snippet_text
    except requests.exceptions.RequestException as e:
        logging.warning(f"Tavily search failed for query '{query}': {e}")
        return "Search failed. Do your best to evaluate based on your internal knowledge."

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    # Only retry if it is NOT a client error (e.g., don't retry 400 or 403 errors)
    retry=tenacity.retry_if_not_exception_type(genai.errors.ClientError)
)
def query_gemini_batch(combined_prompt):
    """Queries Gemini to evaluate a pre-constructed prompt containing search context."""
    system_instruction = (
        "You are a strict, factual assistant that evaluates a list of conditional queries based on the provided CURRENT, REAL-TIME search snippets, and by utilizing your Google Search tool if necessary. "
        "For each query, read its associated search context carefully and verify the factual reality using your tools. "
        "You must be absolutely certain the condition is TRUE right now. If it is speculative, future-looking, or simply discussing the topic without the condition being met, it is FALSE. "
        "If the condition is explicitly TRUE, create a short, concise, single-sentence string explaining what was fulfilled (e.g., 'BTC is now over $100k'). "
        "If the condition is FALSE, or if you cannot definitively verify it is true right now, DO NOT include it in your output. "
        "Your final output MUST be a valid JSON object with a single key \"fulfilled_conditions\" containing an array of these short strings. "
        "If NONE of the conditions are true, output `{\"fulfilled_conditions\": []}`. "
        "Do not include Markdown formatting like ```json ... ```, just the raw JSON object."
    )

    class FulfilledConditions(BaseModel):
        fulfilled_conditions: list[str]

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=combined_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1, # Keep it deterministic
            response_mime_type="application/json", # Request JSON output
            tools=[{"google_search": {}}], # Enable Google Search Grounding
            response_schema=FulfilledConditions
        )
    )

    return response.text

def query_groq_batch(combined_prompt):
    """Fallback function: queries Groq if Gemini fails."""
    if not GROQ_API_KEY:
        logging.warning("Gemini failed and GROQ_API_KEY is not set. Cannot fallback.")
        return None

    logging.info("Initiating Groq fallback...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    # We embed the system instruction directly into the developer/system message
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
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": combined_prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"} # Groq supports JSON mode
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        content = data['choices'][0]['message']['content']
        # Groq json_object requires an object, so we might get {"alerts": []} or just an array string if it ignored strict object rules
        return content
    except Exception as e:
        logging.error(f"Groq fallback failed: {e}")
        return None


def process_prompts_in_batches(prompts, batch_size=10):
    """Processes prompts in batches to avoid giant requests while minimizing API calls."""
    all_alerts = []

    # Split prompts into chunks of batch_size
    batches = [prompts[i:i + batch_size] for i in range(0, len(prompts), batch_size)]

    for i, batch in enumerate(batches):
        logging.info(f"Preparing context for Batch {i+1}/{len(batches)}...")

        # 1. Fetch context ONCE for the batch (outside of retry loops)
        prompt_parts = ["Please evaluate the following conditions based on their provided search context:\n\n"]
        for j, prompt in enumerate(batch):
            urls = extract_urls(prompt)
            context = fetch_jina_reader(urls[0]) if urls else ""
            if not context:
                context = search_tavily(prompt)
                time.sleep(1) # Small delay to respect Tavily free tier limits
            prompt_parts.append(f"Condition {j+1}: {prompt}\nSearch Context: {context}\n\n")

        combined_prompt = "".join(prompt_parts)
        logging.info(f"Combined prompt for batch {i+1} built. Length: {len(combined_prompt)}")

        # 2. Query Gemini
        logging.info(f"Querying Gemini (Batch {i+1}/{len(batches)})...")
        try:
            response_text = query_gemini_batch(combined_prompt)
            logging.info(f"Raw Gemini response for batch {i+1}:\n{response_text}")
        except tenacity.RetryError as e:
            logging.error(f"Error evaluating batch {i+1} with Gemini: {e.last_attempt.exception()}")
            response_text = None
        except Exception as e:
            logging.error(f"Error evaluating batch {i+1} with Gemini: {e}")
            response_text = None

        if not response_text:
            # 3. Fallback to Groq using the EXACT same pre-fetched context
            response_text = query_groq_batch(combined_prompt)
            if not response_text:
                logging.error("Both Gemini and Groq failed. Skipping batch.")
                continue
            logging.info(f"Raw Groq response for batch {i+1}:\n{response_text}")

        # Try to parse whatever response_text we ended up with (Gemini or Groq)
        try:
            try:
                batch_alerts = json.loads(response_text)
            except json.JSONDecodeError as e:
                logging.warning(f"Failed to parse AI response as JSON: {e}")
                logging.info("Trying to clean up markdown if present...")
                # Fallback if AI accidentally includes markdown despite instructions
                cleaned_text = response_text.strip()
                if cleaned_text.startswith("```json"):
                    cleaned_text = cleaned_text[7:]
                if cleaned_text.endswith("```"):
                    cleaned_text = cleaned_text[:-3]
                try:
                    batch_alerts = json.loads(cleaned_text.strip())
                except json.JSONDecodeError:
                     logging.error("Could not recover JSON for this batch. Skipping.")
                     batch_alerts = []

            # Groq's JSON mode might wrap it in an object like {"alerts": []}
            if isinstance(batch_alerts, dict):
                # Try to extract the array
                for val in batch_alerts.values():
                    if isinstance(val, list):
                        batch_alerts = val
                        break

            if isinstance(batch_alerts, list):
                all_alerts.extend(batch_alerts)
            else:
                logging.error("Expected JSON list for this batch.")

        except Exception as e:
            logging.error(f"Unexpected error parsing batch {i+1}: {e}")

        # Add a delay between batches to avoid rate limits (except after the last batch)
        if i < len(batches) - 1:
            logging.info("Waiting 5 seconds before next batch to prevent rate limits...")
            time.sleep(5)

    return all_alerts

def send_telegram_notification(message):
    """Sends a notification to the configured Telegram Chat ID."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    logging.info("Sending notification via Telegram...")
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        logging.info("Telegram notification sent successfully.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Telegram notification: {e}")

def send_ntfy_notification(message):
    """Sends a notification to the configured ntfy topic."""
    if not NTFY_TOPIC:
        return

    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    logging.info(f"Sending notification to {url}...")
    try:
        response = requests.post(url, data=message.encode('utf-8'), timeout=15)
        response.raise_for_status()
        logging.info("Ntfy notification sent successfully.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send ntfy notification: {e}")

def ping_healthcheck():
    """Pings Healthchecks.io if configured to signal a successful run."""
    if not HEALTHCHECK_URL:
        return

    logging.info("Pinging Healthchecks.io...")
    try:
        requests.get(HEALTHCHECK_URL, timeout=10)
        logging.info("Healthcheck ping successful.")
    except requests.exceptions.RequestException as e:
        logging.warning(f"Failed to ping Healthchecks.io: {e}")

def main():
    logging.info("Starting Weekly Gemini Query Script")
    prompts = read_prompts()
    if not prompts:
        logging.warning("No prompts found in prompts.txt. Exiting.")
        return

    try:
        alerts = process_prompts_in_batches(prompts, batch_size=10)

        if alerts:
            # Join the alerts with a newline or custom separator
            notification_message = "Weekly Updates:\n" + "\n".join(f"- {alert}" for alert in alerts)
            logging.info("Conditions met. Sending notifications...")
            send_ntfy_notification(notification_message)
            send_telegram_notification(notification_message)
        else:
            logging.info("No conditions were met. No notification sent.")

        # If we made it this far without crashing, the job was successful
        ping_healthcheck()

    except Exception as e:
         logging.exception(f"A fatal error occurred during processing: {e}")

if __name__ == "__main__":
    main()
