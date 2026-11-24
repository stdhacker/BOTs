# 📸 Instagram Group Bot

Reads the group, copies how people talk, and replies like a friend. Control it from the chat with `!commands`.

## Setup
```
pip install -r requirements.txt
python main.py
```
1. Open `main.py` and fill the `SETTINGS` block: bot username, password, **your** username (`ADMIN_USERNAME`) and one AI key.
2. Add the bot account to a group (3+ people) and accept it under **Requests**.
3. Send `!allow` in the group from your admin account.

## Commands (admins only)

| Command | Does |
|---|---|
| `!allow` / `!deny` | Bot on / off in this group |
| `!mode mention` / `!mode all` | Reply only when tagged, or to anything |
| `!ignore <username>` | Bot stops replying to that person |
| `!help` | Everything else |

> **Login says "app out of date"?** Copy the `sessionid` cookie of the bot account from instagram.com and put it in `IG_SESSIONID`.
