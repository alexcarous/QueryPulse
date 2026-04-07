# Weekly Gemini Queries via ntfy

This project is a lightweight Python script that runs on a schedule (e.g., via a cron job on a Raspberry Pi). It reads a list of condition-based questions from a file (`prompts.txt`), queries the Gemini API (using Google Search grounding for real-time data), and sends a notification to your phone via [ntfy.sh](https://ntfy.sh) if any of the conditions are true.

## Features

- **Real-time Evaluation:** Uses Gemini 2.5 Flash with Google Search enabled to evaluate conditions using the latest internet data.
- **Conditional Notifications:** Only sends a push notification if one or more conditions in your prompt list evaluate to "True".
- **Auto-updating:** Includes a `run.sh` script that automatically pulls the latest `prompts.txt` and code from GitHub before running.
- **Retry Logic:** Implements exponential backoff via `tenacity` to handle transient API rate limits.

## Setup Instructions (Raspberry Pi)

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd <repository-directory>
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
   cp .env.example .env
   ```
2. Edit `.env` and fill in your details:
   - `GEMINI_API_KEY`: Get a free key from [Google AI Studio](https://aistudio.google.com/).
   - `NTFY_TOPIC`: Create a unique, secret topic name for [ntfy.sh](https://ntfy.sh) (e.g., `my_secret_crypto_alerts_99`).

### 4. Configure Prompts

Edit `prompts.txt` and add your conditional questions, one per line. For example:
```text
Is Bitcoin's price over $100k USD yet?
Is the S&P 500 currently trading above 6000?
```

### 5. Update `run.sh` (Optional)

The `run.sh` script assumes your virtual environment is named `venv` and is located in the same directory. If you named your virtual environment something else, edit `run.sh` to update the path.

Also, ensure the branch name in `git pull origin main` in `run.sh` matches your repository's default branch.

### 6. Set Up the Cron Job

To run the script weekly (e.g., every Monday at 9:00 AM), set up a cron job.

1. Open the crontab editor:
   ```bash
   crontab -e
   ```
2. Add the following line (replace `/path/to/your/repo` with the actual absolute path):
   ```cron
   0 9 * * 1 /path/to/your/repo/run.sh >> /path/to/your/repo/cron.log 2>&1
   ```

This will run the script, fetch the latest code/prompts from GitHub, query Gemini, and log the output to `cron.log`.
