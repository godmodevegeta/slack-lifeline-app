# 🚀 Setup & Testing Guide

This guide will walk you through setting up the Lifeline environment, running the MCP backend, and executing the end-to-end "Golden Path" test.

## 📋 Prerequisites

- **Python 3.10+**
- **Node.js** (for `ngrok`)
- **Slack Workspace** with admin rights to install apps and create channels.
- **Airtable Account** with a base containing the Shelter Roster.
- **Google Cloud Account** with a Service Account JSON key for Google Sheets API.
- **OpenRouter API Key** (for the LLM).

## 🛠️ Environment Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/godmodevegeta/lifeline-slack-agent.git
   cd lifeline-slack-agent
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create your `.env` file in the root directory. Copy the contents from `.env.sample` and fill in your credentials:
   ```env
   # Slack Core
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_SIGNING_SECRET=your_signing_secret
   
   # LLM Provider
   OPENROUTER_API_KEY=sk-or-...
   
   # Lifeline MCP Backend
   AIRTABLE_PAT=pat...
   LIFELINE_MCP_URL=https://<your-ngrok-url>.ngrok-free.app/mcp
   
   # Channel IDs
   INTAKE_CHANNEL_ID=C_...
   LOGS_CHANNEL_ID=C_...
   LOGISTICS_CHANNEL_ID=C_...
   ```

## ⚙️ Step 1: Run the MCP Backend & Ngrok

The Lifeline MCP server must be running and exposed to the internet before starting the Slack app.

1. Navigate to the backend directory:
   ```bash
   cd lifeline-mcps
   ```
2. Start the MCP server:
   ```bash
   python server.py
   ```
3. In a **new terminal**, start `ngrok` to expose the local server (default port 8000):
   ```bash
   ngrok http 8000
   ```
4. Copy the `https://...ngrok-free.app` URL and update `LIFELINE_MCP_URL` in your root `.env` file.

##  Step 2: Configure the Slack App

1. Go to your Slack App configuration page.
2. Under **Agent Builder** -> **MCP Servers**, add a new server.
3. Paste your `ngrok` URL (e.g., `https://abc-123.ngrok-free.app/mcp`).
4. Ensure the app has the necessary scopes installed (`chat:write`, `channels:history`, `search:read`, etc.).
5. Install/Reinstall the app to your workspace.

## ▶️ Step 3: Run the Slack Agent

In the root directory, start the Bolt application:
```bash
python app.py
```
*Ensure your terminal shows that the Slack MCP Server and Lifeline MCP Server have successfully connected.*

## 🧪 Step 4: Automated Tests

Before running manual tests, ensure the codebase is clean and unit tests pass:

```bash
# Linting
ruff check .

# Unit Tests
pytest -q
```

##  Step 5: The "Golden Path" Manual Test

To verify the full neurosymbolic flow, set up the following in your Slack workspace:

**1. Prepare the Workspace:**
- Ensure you have `#intake`, `#logistics-alerts`, and `#lifeline-logs` channels.
- In `#logistics-alerts`, post a message: `⚠️ ALERT: Mission St in Zone B is closed due to water main.`
- Ensure your Airtable base has a shelter in Zone B (not on Mission St) and your Google Sheet has at least one `heavy_duty_van` and one standard `van`.

**2. Trigger the Agent:**
- Go to `#intake` and type:
  > `@Lifeline Need emergency housing for a family of 2 in Zone B. One person is in a motorized wheelchair.`

**3. Verify the UI & Logic:**
- Watch for the `🔄 Lifeline is assessing...` loading state.
- A Block Kit card should appear. **Verify the "Routing Logic" section:** It must explicitly state that the standard `van` was rejected due to insufficient lift capacity, and the `heavy_duty_van` was selected.
- Verify the `⚠️ Ambient Alert` section warns about Mission St.

**4. Execute the Dispatch:**
- Click the `[ 🔒 Confirm & Dispatch ]` button.
- Watch the card instantly mutate to `🔄 Processing...` and then to `✅ DISPATCH CONFIRMED`.

**5. Verify State Changes (The Proof):**
- Open your **Airtable base**: The shelter's "Capacity Remaining" should have decremented.
- Open your **Google Sheet**: The volunteer's status should now read `DISPATCHED`.
- Check `#lifeline-logs`: A dense, structured audit log with a permalink back to the original request should be posted.

##  Troubleshooting

- **RTS API 429 Errors:** If the Slack search tool hits a rate limit, the agent is designed to gracefully degrade. It will proceed with the dispatch without the ambient alert and log the failure in `#lifeline-logs`.
- **"Unknown vehicle type" in Logs:** Ensure the `Vehicle Type` column in your Google Sheet exactly matches the keys in `lifeline-mcps/ontology.json` (e.g., `van`, `sedan`, `heavy_duty_van`).
- **MCP Connection Drops:** If the Slack app logs show MCP connection errors, restart `ngrok`, update the URL in your `.env`, and restart the Slack app.