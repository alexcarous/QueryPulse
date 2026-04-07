import os
import json
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
import tenacity

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

if not GEMINI_API_KEY or not NTFY_TOPIC:
    print("Error: GEMINI_API_KEY and NTFY_TOPIC must be set in the .env file.")
    exit(1)

if GEMINI_API_KEY == "your_gemini_api_key_here":
    print("Error: You are still using the default placeholder for GEMINI_API_KEY. Please edit the .env file and add your real key.")
    exit(1)

# Initialize Gemini Client
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Error initializing Gemini client: {e}")
    exit(1)

def read_prompts(filename="prompts.txt"):
    """Reads prompts from the specified file, ignoring empty lines."""
    try:
        with open(filename, "r") as f:
            prompts = [line.strip() for line in f if line.strip()]
        return prompts
    except FileNotFoundError:
        print(f"Error: {filename} not found.")
        exit(1)

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    # Only retry if it is NOT a client error (e.g., don't retry 400 or 403 errors)
    retry=tenacity.retry_if_not_exception_type(genai.errors.ClientError)
)
def query_gemini(prompts):
    """Queries Gemini using Google Search grounding and returns a JSON list."""

    system_instruction = (
        "You are an assistant that evaluates a list of conditional queries based on CURRENT, REAL-TIME information. "
        "You MUST use the Google Search tool to check the current status of each query. "
        "For each query, if the condition is TRUE (or roughly true/achieved), create a short, concise, single-sentence string explaining what was fulfilled (e.g., 'BTC is now over $100k'). "
        "If the condition is FALSE or you cannot verify it, DO NOT include it in your output. "
        "Your final output MUST be a valid JSON array of these short strings. "
        "If NONE of the conditions are true, output an empty JSON array `[]`. "
        "Do not include Markdown formatting like ```json ... ```, just the raw JSON array."
    )

    combined_prompt = "Please evaluate the following conditions:\n\n"
    for i, prompt in enumerate(prompts):
        combined_prompt += f"{i+1}. {prompt}\n"

    print("Querying Gemini...")
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=combined_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[{"google_search": {}}], # Enable Google Search Grounding
            temperature=0.1, # Keep it deterministic
            response_mime_type="application/json", # Request JSON output
        )
    )

    return response.text

def send_ntfy_notification(message):
    """Sends a notification to the configured ntfy topic."""
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    print(f"Sending notification to {url}...")
    try:
        response = requests.post(url, data=message.encode('utf-8'))
        response.raise_for_status()
        print("Notification sent successfully.")
    except requests.exceptions.RequestException as e:
        print(f"Failed to send notification: {e}")

def main():
    prompts = read_prompts()
    if not prompts:
        print("No prompts found in prompts.txt. Exiting.")
        return

    try:
        response_text = query_gemini(prompts)
        print(f"Raw Gemini response:\n{response_text}")

        # Parse the JSON response
        try:
            alerts = json.loads(response_text)
        except json.JSONDecodeError as e:
            print(f"Failed to parse Gemini response as JSON: {e}")
            print("Trying to clean up markdown if present...")
            # Fallback if Gemini accidentally includes markdown despite instructions
            cleaned_text = response_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            try:
                alerts = json.loads(cleaned_text.strip())
            except json.JSONDecodeError:
                 print("Could not recover JSON. Exiting.")
                 return

        if not isinstance(alerts, list):
            print("Error: Expected Gemini to return a JSON list.")
            return

        if alerts:
            # Join the alerts with a newline or custom separator
            notification_message = "Weekly Updates:\n" + "\n".join(f"- {alert}" for alert in alerts)
            send_ntfy_notification(notification_message)
        else:
            print("No conditions were met. No notification sent.")

    except tenacity.RetryError as e:
        print(f"An error occurred during processing: {e.last_attempt.exception()}")
    except Exception as e:
         print(f"An error occurred during processing: {e}")

if __name__ == "__main__":
    main()
