# Settings

MILLIE stores application settings in the root `millie.settings` file. This file is a SQLite3 database, not an environment file, and it is ignored by Git.

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
- `microsoft_oauth_tenant`: Microsoft OAuth tenant value. Use `organizations` for work or school accounts, `common` for mixed account support, or a tenant ID/domain.
- `microsoft_oauth_client_id`: Microsoft Entra application client ID.
- `microsoft_oauth_client_secret`: optional Microsoft Entra client secret. Leave blank for public/native local flows. Stored in plain text for now if used.
- `microsoft_oauth_redirect_uri`: redirect URI registered in Entra. Current local value: `http://localhost:22013/oauth/microsoft/callback`.
- `microsoft_oauth_scopes`: Microsoft OAuth scopes. Current IMAP value: `offline_access https://outlook.office.com/IMAP.AccessAsUser.All`.
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

## Microsoft Entra OAuth For Outlook IMAP

Create the Entra application before authorizing an Outlook IMAP account:

1. Open the Microsoft Entra admin center.
2. Go to **Identity** > **Applications** > **App registrations**.
3. Choose **New registration**.
4. Enter a name such as `MILLIE Local Mail`.
5. For supported account types, choose **Accounts in this organizational directory only** for one tenant, or **Accounts in any organizational directory** if MILLIE should support multiple Microsoft work or school tenants.
6. Add a redirect URI for platform **Web** using `http://localhost:22013/oauth/microsoft/callback`.
7. Register the app.
8. Copy the **Application (client) ID** into `microsoft_oauth_client_id`.
9. If you choose to use a confidential web flow, create a client secret under **Certificates & secrets** and copy it into `microsoft_oauth_client_secret`. For a local public/native flow, leave the secret blank.
10. Under **API permissions**, add delegated permission `IMAP.AccessAsUser.All` for Office 365 Exchange Online.
11. Add or confirm delegated `offline_access` so MILLIE can request refresh tokens.
12. Grant admin consent if your tenant policy requires it.

Use this authorization URL after replacing `CLIENT_ID` with the saved `microsoft_oauth_client_id`:

```text
https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?client_id=CLIENT_ID&response_type=code&redirect_uri=http%3A%2F%2Flocalhost%3A22013%2Foauth%2Fmicrosoft%2Fcallback&response_mode=query&scope=offline_access%20https%3A%2F%2Foutlook.office.com%2FIMAP.AccessAsUser.All&state=millie-outlook-imap&prompt=consent
```

The redirect URI in Entra must exactly match `microsoft_oauth_redirect_uri`.

If you prefer `http://127.0.0.1:22013/oauth/microsoft/callback`, Microsoft supports loopback IP redirects, but the Entra portal can require editing the application manifest for HTTP `127.0.0.1` values.

## Security Note

The temporary settings editor keeps secrets simple and stores them in plain text. Do not commit real API keys, database passwords, OAuth client secrets, tokens, or mail account passwords.
