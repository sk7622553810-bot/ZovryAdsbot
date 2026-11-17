# ZovryAds

ZovryAds is a Telegram bot built with aiogram, Telethon, and PyMongo. The package includes the bot modules, pinned dependencies, and deployment configuration for a VPS or Render background worker.

## Configure credentials

1. Copy `.env.example` to `.env`.
2. Set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`, `LOGGER_BOT_TOKEN`, `MONGO_URI`, and at least one administrator ID.
3. Generate an `ENCRYPTION_KEY` with:

   ```sh
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Keep this key stable and private. Existing hosted-account sessions can only be decrypted with the Fernet key used when they were stored. For an existing database, configure that original key rather than generating a new one.

Optional channel IDs, image URLs, and feature toggles can be set in `.env`; defaults use `@ZovryAds`, `@ZovryChat`, `@Zovrybot`, and `@ZovryOrg`.

## VPS

Use Python 3.11 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

For continuous operation, run the command under a process manager such as systemd or Supervisor. Keep `.env` and `ENCRYPTION_KEY` private and backed up.

## Render

Create a **Background Worker** from this folder using `render.yaml`. Add the required secrets in the Render environment settings. Keep `ENCRYPTION_KEY` set to the same persistent value across restarts and deployments; do not rely on the generated local key file on an ephemeral filesystem.

## Included runtime

- `main.py` — bot startup, handlers, dashboard, account management, broadcasts, and logger integration.
- `config.py` — environment-based configuration and startup validation.
- `database.py` — MongoDB-backed state and hosted account storage.
- `auto_join.py`, `force_subscription.py`, `keyboards.py`, `profile_enforcement.py`, and `utils.py` — supporting runtime modules.
- `requirements.txt` — pinned Python dependencies.

The package intentionally excludes local `.env` files, Fernet keys, Telegram session databases, logs, tests, caches, virtual environments, and backup source files.
