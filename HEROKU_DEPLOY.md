# Deploying on Heroku

## 1. Create the Heroku app

Install the Heroku CLI, log in, and run these commands from this project folder:

```bash
heroku login
heroku create your-app-name
```

The repository already includes the Heroku files Heroku needs:

- `Procfile` starts the FastAPI app on Heroku's assigned `$PORT`.
- `runtime.txt` selects Python 3.12.
- `requirements.txt` installs the Python dependencies.

## 2. Add the required config vars

Do not commit these values to the repository. Set them in Heroku:

```bash
heroku config:set \
  API_ID="your-telegram-api-id" \
  API_HASH="your-telegram-api-hash" \
  BOT_TOKEN="your-bot-token" \
  OWNER_ID="your-telegram-user-id" \
  STORAGE_CHANNEL="your-storage-channel-id-or-username" \
  DATABASE_URL="your-mongodb-connection-string" \
  BASE_URL="https://your-app-name.herokuapp.com" \
  --app your-app-name
```

Optional values:

```bash
heroku config:set \
  FORCE_SUB_CHANNEL="your-force-sub-channel-id-or-username" \
  MULTI_TOKEN_1="another-bot-token" \
  REDIRECT_BLOGGER_URL="https://example.com/redirect" \
  BLOGGER_PAGE_URL="https://example.com/page" \
  --app your-app-name
```

`BASE_URL` must be the public HTTPS app URL and must not end with `/`.

## 3. Deploy

```bash
git push heroku main
heroku ps:scale web=1 --app your-app-name
heroku logs --tail --app your-app-name
```

The health endpoint is:

```text
https://your-app-name.herokuapp.com/
```

It should return a JSON response indicating that the server is healthy.

## Telegram and database prerequisites

Before deploying, make sure:

1. The bot is an administrator in `STORAGE_CHANNEL`.
2. The bot has permission to post, read messages, and manage members there.
3. `FORCE_SUB_CHANNEL`, if used, is accessible to the bot.
4. The MongoDB service accepts connections from Heroku.
5. The Telegram and MongoDB credentials are stored only as Heroku config vars.