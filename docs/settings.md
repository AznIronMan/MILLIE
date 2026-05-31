# Settings

MILLIE stores application settings in the root `millie.settings` file. This file is a SQLite3 database, not an environment file.

During early development, run the temporary settings editor from the project root:

```sh
./tmp_settings.sh
```

The script starts a local browser page at `http://127.0.0.1:22011/`. From there, review settings, edit values, then choose **Save changes** or **Cancel**.

## Current Settings

- `database_mode`: `sqlite` or `postgres`.
- `sqlite_location`: optional SQLite data path. If blank, MILLIE should create `data/<instance_id>.millie`.
- `postgres_host_ip`, `postgres_port`, `postgres_username`, `postgres_password`, `postgres_database`: PostgreSQL connection settings. Password values are hidden in the editor and stored in plain text for now.
- `main_api_provider`, `second_api_provider`, `third_api_provider`: `openai`, `claude`, `gemini`, `xai`, `local`, or blank for unused fallback tiers.
- `main_api_key`, `second_api_key`, `third_api_key`: provider API keys. Leave empty for local models. Values are hidden in the editor and stored in plain text for now.
- `main_api_model`, `second_api_model`, `third_api_model`: provider model identifier, or a local LLM path when the provider is `local`.
- `main_api_thinking`, `second_api_thinking`, `third_api_thinking`: `low`, `med`, `high`, `xhigh`, or blank for the default behavior.
- `logging_type`: `files`, `sqlite`, or `cnblogger`. File logging should append to `logs/YYYYMMDD.log`. SQLite logging should create `data/<instance_id>.logdb` when possible. `cnblogger` is reserved and disabled for now.

## Mail Accounts

The temporary settings editor stores repeatable IMAP and SMTP accounts in the `mail_accounts` table inside `millie.settings`.

Each account includes:

- Account type: `imap` for retrieval or `smtp` for sending.
- Display name.
- Email address.
- Host.
- Port.
- Username.
- Password, stored in plain text for now and hidden in the editor after save.
- Security mode: blank/default, `none`, `starttls`, or `ssl_tls`.
- Auth method: `password`, `oauth`, or `none`.
- Enabled state.

The editor can add one or many IMAP accounts and one or many SMTP accounts. Removing an account and choosing **Save changes** deletes it from `millie.settings`.

## Security Note

The temporary settings editor keeps secrets simple and stores them in plain text. Do not commit real API keys, database passwords, or mail account passwords.
