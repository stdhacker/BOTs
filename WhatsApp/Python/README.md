# 💬 WhatsApp Bot (Python)

Reads the group, copies how people talk, and replies like a friend. Control it from the chat with `!commands`.

## Setup
```
pip install -r requirements.txt
python main.py
```
1. Open `main.py` and fill the `SETTINGS` block: **your** number (`ADMIN`) and one AI key.
2. Run it and scan the QR code: *WhatsApp → Linked Devices → Link a Device*. You only do this once.
3. Add the bot's number to a group and send `!allow` from your admin number.

## Commands (admins only)

| Command | Does |
|---|---|
| `!allow` / `!deny` | Bot on / off in this group |
| `!mode mention` / `!mode all` | Reply only when tagged, or to anything |
| `!ignore` | Reply to / @tag / type a number: bot stops replying to them |
| `!help` | Everything else |
