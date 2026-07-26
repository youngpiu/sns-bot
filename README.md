# SNS Discord Bot

Python bot that monitors K-pop artist social media accounts and sends notifications to Discord webhooks.

## Supported Platforms

| Platform | Status | Requirements |
|---|---|---|
| Instagram | Required | `IG_USERNAME` + `IG_PASSWORD` |
| JYP Fans | Optional | Logged-in session (run `login.py`) |
| X (Twitter) | Optional | `TWITTER_AUTH_TOKEN` from browser cookie |
| YouTube | Optional | `NGROK_TOKEN` to expose webhook |

Extra features:
- Korean → Vietnamese translation via Gemini
- Discord thread support (optional)
- Per-platform webhook
- Error alert via separate webhook

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the values.

### Instagram
```dotenv
IG_USERNAME=...
IG_PASSWORD=...
IG_TARGET=nmixx_official
```

### JYP Fans
Run the interactive login script:
```powershell
python login.py
```
Enter your email, get the verification code from your email, and enter it. The session is saved to `sessions/fans_session.json`.

### X (Twitter)
Get `auth_token` from your browser cookies after logging into Twitter.

### YouTube (push notification)
1. Register a token at https://dashboard.ngrok.com/get-started/your-authtoken
2. Set `NGROK_TOKEN`
3. The bot will auto-expose a webhook and subscribe to YouTube push notifications

## Run

```powershell
python main.py
```

## Project Structure

```
sns-bot/
├── main.py                   # Entry point, polling loops
├── config.py                 # Config loaded from .env
├── login.py                  # Interactive Fans login
├── providers/
│   ├── fans.py               # Fans GraphQL client
│   ├── ig.py                 # Instagram client (instagrapi)
│   ├── twitter.py            # Twitter client (tweety-ns)
│   ├── yt.py                 # YouTube push notification
│   └── translator.py         # Gemini translation
├── sessions/                 # Saved sessions (tokens, cookies)
├── states/                   # Last-seen state per platform
├── .env                      # Secrets (do not commit)
└── prompt.txt                # Gemini translation prompt
```

## JYP Fans Notification Categories

From `api.app.fans` GraphQL API:

**Community:**
- `POST_CREATED_BY_ARTIST` — Artist created a new post *(currently tracked)*
- `COMMENT_CREATED_BY_ARTIST` — Artist commented on a fan's post
- `POST_LIKE_CREATED_BY_ARTIST` — Artist liked a fan's post

**Shop:**
- `NOTICE` — Shop notices (shipping, payments...)

## Notes

- Keep `.env` and `sessions/*` private (already in `.gitignore`)
- Instagram needs a stable proxy when running on a server
- Fans session expires periodically; auto-refresh is handled in code
