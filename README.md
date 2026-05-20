# QueryPulse: Condition-Based Alerts

This project is a lightweight Python script that runs on a schedule (e.g., via a cron job on a Raspberry Pi). It reads a list of condition-based questions from a file (`prompts.txt`), fetches live real-time context via the **Tavily Search API** (or **Jina Reader** for specific URLs), queries the **Gemini API** (or **Groq** as a fallback) to evaluate the condition against the search results, and sends a notification to your phone via [ntfy.sh](https://ntfy.sh) or Telegram if any of the conditions are true.

## Features

- **Real-time Evaluation:** Uses Tavily Search to gather live context and the latest Gemini Flash model to evaluate conditions, bypassing strict Google Search Grounding free-tier limits.
- **Deep Web Scraping:** Automatically detects URLs in your prompts and routes them through Jina Reader to get exact webpage content.
- **LLM Fallback:** If the Gemini API fails or runs out of quota, it automatically falls back to Groq (dynamically selecting the best available model) to ensure your questions are always answered.
- **Conditional Notifications:** Only sends a push notification if one or more conditions in your prompt list evaluate to "True" via ntfy.sh or Telegram.
- **Cron Monitoring:** Supports Healthchecks.io dead-man switches to alert you if the Raspberry Pi dies or the script fails.
- **Auto-updating:** Includes a `run.sh` script that automatically pulls the latest `prompts.txt` and code from GitHub before running.
- **Retry Logic:** Implements exponential backoff via `tenacity` to handle transient API rate limits.

## Setup Instructions (Raspberry Pi)

### 1. Clone the Repository

```bash
git clone https://github.com/alexcarous/QueryPulse.git
cd QueryPulse
```

### 2. Set Up the Environment

It's recommended to use a Python virtual environment:

```bash
# Create a virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Secrets

1. Copy the example environment file:
   ```bash
   cp env.example .env
   ```
2. Edit `.env` and fill in your details. You must provide the core API keys and at least one notification channel.
   - `GEMINI_API_KEY`: Get a free key from [Google AI Studio](https://aistudio.google.com/).
   - `TAVILY_API_KEY`: Get a free key from [Tavily](https://tavily.com/) (provides 1,000 free searches/month).

   **Notification Channels (Choose one or both):**
   - `NTFY_TOPIC`: Create a unique, secret topic name for [ntfy.sh](https://ntfy.sh) (e.g., `my_secret_crypto_alerts_99`). Leave blank if not using.
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`: Get these from BotFather on Telegram to send alerts to a private chat. Leave blank if not using.

   **Robustness Tools (Optional but recommended):**
   - `GROQ_API_KEY`: Get a free key from [Groq](https://groq.com/). The script will use this ultra-fast API as a fallback if Google Gemini is down or you hit a rate limit.
   - `HEALTHCHECK_URL`: Get a free ping URL from [Healthchecks.io](https://healthchecks.io/). The script will ping this URL when it finishes successfully. If it fails to ping, Healthchecks will email you.

   **Scheduling / Frequency (Optional):**
   - `SCHEDULE`: (Default: `weekly`) Controls how often the script actually executes when triggered (e.g., by an hourly cron job). Options: `always`, `hourly`, `daily`, `weekly`, `annually`.
- `FREQUENCY`: (Default: `weekly`) Changes the title of your notifications (e.g. "Daily Updates", "Hourly Updates"). If `SCHEDULE` is not set, it also defines the run frequency.

   **Model Overrides (Optional):**
   - `GEMINI_MODEL`: (Default: `gemini-flash-latest`) The Gemini model to use.
   - `GROQ_MODEL`: (Default: `groq-flash-latest`) The Groq model to use as fallback. Set to `groq-flash-latest` to use the best current Llama model.

### 4. Configure Prompts

1. Copy the example prompts file:
   ```bash
   cp prompts.example.txt prompts.txt
   ```
2. Edit `prompts.txt` and add your conditional questions, one per line. For example:
```text
Is Bitcoin's price over $100k USD yet?
Is the S&P 500 currently trading above 6000?
```

### 5. Update `run.sh` (Optional)

The `run.sh` script assumes your virtual environment is named `venv` and is located in the same directory. If you named your virtual environment something else, edit `run.sh` to update the path.

The script will automatically pull the latest changes from the `master` branch before running.

### 6. Testing the Setup

Before setting up the cron job, it's highly recommended to test the script manually to ensure everything is configured correctly (API keys, ntfy topic, virtual environment).

1. Make sure you are in the repository directory.
2. Add a prompt to `prompts.txt` that is **guaranteed to be true** so you can confirm the notification system works. For example:
   ```text
   Is the sky generally considered blue?
   ```
3. Run the wrapper script manually:
   ```bash
   ./run.sh
   ```
4. Watch the terminal output. It should pull from git, activate the environment, query Gemini, and then output `Notification sent successfully.`. Check your phone/browser ntfy subscription to see the alert!

### 7. Set Up the Cron Job

To run the script on a schedule (e.g., every Monday at 9:00 AM), set up a cron job.

1. Open the crontab editor:
   ```bash
   crontab -e
   ```
2. Add the following line (replace `/path/to/your/repo` with the actual absolute path):
   ```cron
   0 9 * * 1 /path/to/your/repo/run.sh >> /path/to/your/repo/cron.log 2>&1
   ```

This will run the script, fetch the latest code/prompts from GitHub, query Gemini, and log the output to `cron.log`.
