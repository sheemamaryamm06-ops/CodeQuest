# CodeQuest

CodeQuest uses a small Python and SQLite backend for player accounts, sessions, language choice, level progress, and XP. It uses only the Python standard library.

## Run locally

1. Install Python 3.8 or newer.
2. Open a terminal in this folder and run:

   ```powershell
   python server.py
   ```

3. Open http://127.0.0.1:8000 in your browser. Keep the terminal open while playing; press Ctrl+C to stop the server.

The server creates `codequest.sqlite3` beside `server.py` the first time it starts. Passwords are stored as salted PBKDF2 hashes. This server binds to the local computer and is intended for development, not public hosting.

## Run the API test

```powershell
python -m unittest test_server.py
```
