# Development

MILLIE currently uses:

- Python for storage, import/export logic, CLI, and the local API server.
- SQLite as the first database.
- TypeScript/Vite for the web app.

The backend intentionally starts with the Python standard library so the project can run before choosing heavier framework dependencies.

## Local Environment

Create optional local settings from the safe template:

```sh
cp .env.example .env
```

Runtime databases, imported mail, attachment payloads, secrets, and generated export output should stay under ignored local paths such as `.private/local/` or `.private/secrets/`.

## Run The Backend

```sh
PYTHONPATH=src python3 -m millie init-db
PYTHONPATH=src python3 -m millie serve
```

The server binds to `0.0.0.0:8765` by default.

## Build The Web App

```sh
cd web
npm install
npm run build
```

After the web app is built, the Python server serves it from `web/dist`.

## Import Mail

```sh
PYTHONPATH=src python3 -m millie import /path/to/message.eml --format eml
PYTHONPATH=src python3 -m millie import /path/to/archive.mbox --format mbox
PYTHONPATH=src python3 -m millie import /path/to/Maildir --format maildir
```

## Export Mail

```sh
PYTHONPATH=src python3 -m millie export --format eml --output .private/local/exports
PYTHONPATH=src python3 -m millie export --format mbox --output .private/local/exports
PYTHONPATH=src python3 -m millie export --format maildir --output .private/local/exports
```

## Test

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
cd web
npm run build
```
