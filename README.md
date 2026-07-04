# SNS Discord Bot

Python bot that checks the latest Instagram post from one target account with
`instagrapi` and sends a notification to one Discord webhook.

## Setup

1. Create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in values:

```dotenv
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_ROLE_ID=1488594717842342053
INSTAGRAM_USERNAME=your_instagram_login_username
INSTAGRAM_PASSWORD=your_instagram_login_password
INSTAGRAM_TARGET_USERNAME=instagram_account_to_watch
INSTAGRAM_PROXY=
INSTAGRAM_SESSIONID=
POLL_INTERVAL_SECONDS=600
```

4. Run the bot:

```powershell
python main.py
```

## Behavior

- The first run initializes `state.json` with the current latest Instagram post
  and does not send an old post to Discord.
- Later runs fetch the 6 most recent profile media items, sort them by
  `taken_at`, and send every item newer than the saved state.
- `state.json` stores the last successfully sent media PK and timestamp so posts
  made between polling cycles are not skipped when they are still in the latest
  6 media items.
- The watched profile media types are photo posts, feed videos, carousel albums,
  and reels when Instagram returns them through the profile media endpoint.
- Discord messages are sent through a webhook with media uploaded as
  attachments.
- Instagram login settings are saved to `instagram_session.json` to reduce
  repeated login prompts and rate-limit risk.

## Notes

- Keep `.env` and `instagram_session.json` private.
- If Instagram keeps returning rate-limit, challenge, or login trust errors,
  open Instagram manually on a trusted device first. For server automation, use
  one stable proxy/IP in `INSTAGRAM_PROXY`, for example
  `http://username:password@host:port`.
- If password login is blocked, set `INSTAGRAM_SESSIONID` from a logged-in
  browser cookie. The value usually starts with your numeric Instagram user ID.
- Polling faster than 60 seconds is rejected by config validation.
- Phase 1 covers Instagram only. Twitter and YouTube can be added later behind
  separate provider modules.
