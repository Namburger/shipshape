# Developer & Customization Guide

This guide covers local setup, architecture, HTTP API routes, and testing for developers who want to run or customize the dashboard. For a user-focused overview of app features and screenshots, see [`README.md`](README.md).

---

## Prerequisites

- **Python 3.9+** (uses only the Python standard library—no `pip install` needed)
- A **Strava** account and Strava API application

---

## Local Setup & Configuration

### 1. Create a Strava API Application

1. Go to [https://www.strava.com/settings/api](https://www.strava.com/settings/api) while signed in to Strava.
2. Create an application (if you don't already have one):
   - **Application Name**: e.g., `Ship Shape Tracker`
   - **Website**: `http://localhost:8501` (or `http://localhost:8000`)
   - **Authorization Callback Domain**: `localhost`
3. Copy your **Client ID** and **Client Secret**.

### 2. Configure `.env`

Create a `.env` file in the repository root:

```env
STRAVA_CLIENT_ID=your_client_id_here
STRAVA_CLIENT_SECRET=your_client_secret_here
```

> **Note**: `.env` is listed in `.gitignore` and should never be committed. You can also supply `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` directly as shell environment variables (which override `.env`).

### 3. Start the Server

Run the server with default port selection (`8000`, falling back to `8080`, `8765`, `8502`, or an ephemeral port):

```bash
python3 app.py
```

Or deploy locally on port `8501` using the included helper script (runs unit tests first):

```bash
./scripts/deploy.sh            # Foreground on http://localhost:8501
./scripts/deploy.sh --bg       # Detached background mode (logs to data/server.log)
./scripts/deploy.sh --stop     # Stop background server
```

The terminal will print the active URL, e.g.:

```text
Ship Shape Strava Dashboard running at http://localhost:8501
```

### 4. Complete Onboarding in Your Browser

1. Open `http://localhost:8501` (or the printed port). Use `localhost` rather than `127.0.0.1`.
2. **Step 1 (Required) — Connect Strava Account**: Click **Connect with Strava →**, authorize access on Strava (`approval_prompt=force` ensures the consent screen is shown), and wait for the redirect back to `/?synced=1`.
3. **Step 2 (Optional) — Upload Training Plan PDF**: Drag and drop or select a Ship Shape `.pdf` training schedule (synthetic test schedules are available at `assets/jane_doe_training.pdf` and `assets/john_doe_training.pdf`) either during onboarding or anytime later inside the dashboard.

---

## Project Structure

```text
strap/
├── app.py                      # Executable entrypoint (delegates to strap.handler.run_server)
├── index.html                  # Single-page dashboard UI, SVG charts, and client logic
├── README.md                   # User-facing feature overview and screenshots
├── DEVELOPMENT.md              # Local setup, architecture, API reference, and tests
├── strap/
│   ├── __init__.py             # Package exports
│   ├── config.py               # File paths, host/port config, and .env helpers
│   ├── storage.py              # JSON persistence for app state, tokens, cache, and plan
│   ├── strava.py               # Strava OAuth2 flow, token refresh, and activity sync
│   ├── pdf_parser.py           # Zero-dependency Ship Shape PDF text & schedule parser
│   ├── plans.py                # PDF upload validation, auto-parsing, and /api/state assembly
│   └── handler.py              # BaseHTTPRequestHandler routes and TCP server runner
├── assets/
│   ├── jane_doe_training.pdf   # Synthetic 16-week Ship Shape test schedule (Jane Doe)
│   └── john_doe_training.pdf   # Synthetic 16-week Ship Shape test schedule (John Doe)
├── docs/
│   └── screenshots/            # Feature screenshots used in README.md
├── scripts/
│   ├── test.sh                 # Run syntax checks and unit tests
│   └── deploy.sh               # Run checks and start/stop local server
├── tests/                      # Standard-library unittest suite
└── data/
    ├── uploaded_plan.pdf       # (Generated, gitignored) User-uploaded PDF
    ├── .app_state.json         # (Generated, gitignored) Plan upload metadata
    ├── .strava_tokens.json     # (Generated, gitignored) OAuth access & refresh tokens
    └── .strava_cache.json      # (Generated, gitignored) Cached activities & splits
```

---

## HTTP API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves `index.html` |
| `GET` | `/api/state` | Returns configuration status, auth state, synced runs, and plan JSON |
| `GET` | `/api/plan-pdf` | Serves the uploaded PDF inline (or fallback sample PDF) |
| `GET` | `/auth/strava` | Redirects to Strava's OAuth2 authorization screen |
| `GET` | `/oauth/callback` | Exchanges authorization code for tokens and performs an initial sync |
| `GET` | `/api/activity/<id>` | Fetches (and caches) detailed laps and mile splits for an activity |
| `POST` | `/api/upload-plan` | Validates, parses, and stores a base64-encoded `.pdf` plan upload |
| `POST` | `/api/remove-plan` | Deletes the uploaded plan PDF and clears the plan overlay |
| `POST` | `/api/sync` | Re-syncs Strava activities (last 12 months) into `data/.strava_cache.json` |
| `POST` | `/api/config` | Saves `client_id` and `client_secret` to `.env` |
| `POST` | `/api/disconnect` | Deletes `data/.strava_tokens.json` and signs out |

---

## Running Tests

Run the full unit test suite using the test script (or directly with Python's built-in `unittest` runner—no external test dependencies required):

```bash
./scripts/test.sh
```
