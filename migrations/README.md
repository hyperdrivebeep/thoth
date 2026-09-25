# THOTH database migrations

Run from repository root:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Tests override `THOTH_ALEMBIC_URL` with an isolated SQLite database.
