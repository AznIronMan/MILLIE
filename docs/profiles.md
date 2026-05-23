# Profiles

Profiles let MILLIE keep separate test or real mail libraries without mixing databases, blobs, import history, and export history.

## Behavior

- MILLIE stores global settings and the profile registry in `.private/local/millie.settings` by default.
- Profile databases, blob stores, and profile-specific settings files live under `.private/local/profiles/` by default.
- Profile settings are SQLite files named after the profile id, such as `default.settings` or `fixture-mail.settings`.
- The `default` profile points at `.private/local/millie.sqlite` and `.private/local/data`.
- The previously selected profile opens automatically the next time the server starts.
- Switching profiles changes the active SQLite database and data directory for API, import, export, and web UI operations.

## Environment

```sh
MILLIE_SETTINGS=.private/local/millie.settings
MILLIE_PROFILES_DIR=.private/local/profiles
```

## CLI

```sh
PYTHONPATH=src python3 -m millie profiles
PYTHONPATH=src python3 -m millie profile-create "Fixture Mail"
PYTHONPATH=src python3 -m millie profile-use fixture-mail
```

## API

- `GET /api/v1/profiles`
- `POST /api/v1/profiles`
- `POST /api/v1/profiles/active`

Example create payload:

```json
{
  "name": "Fixture Mail",
  "switch": true
}
```

Example switch payload:

```json
{
  "profileId": "fixture-mail"
}
```

## Notes

Profiles are local runtime state. They should not contain secrets directly, and generated profile databases should stay ignored by git.
