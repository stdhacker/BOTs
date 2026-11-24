<h1 align="center">🤖 Bots</h1>
<p align="center">AI chat bots for Instagram and WhatsApp<br>Gemini + Groq · controlled from inside the chat</p>

> **⚠️ Heads up:** these bots use unofficial APIs, which go against the platforms' Terms of Service.
> Accounts can be limited or banned. **Always use a separate throw-away account.**

## Pick a bot

| | Bot | What it does | Folder |
|---|---|---|---|
| 📸 | **Instagram Group Bot** | Chats like a normal friend in group chats | [`InstaGram/Group_bot`](InstaGram/Group_bot) |
| 💕 | **Instagram GF Bot** | AI companion for DMs and groups | [`InstaGram/GF_bot`](InstaGram/GF_bot) |
| 💬 | **WhatsApp Bot** (Python) | Same as the group bot, for WhatsApp | [`WhatsApp/Python`](WhatsApp/Python) |
| 💬 | **WhatsApp Bot** (Node.js) | Same, using whatsapp-web.js | [`WhatsApp/NodeJS`](WhatsApp/NodeJS) |
| ✈️ | **Telegram Bot** | Coming soon | [`TeleGram`](TeleGram) |

## Quick start

1. **Install Python 3.11+** ([python.org](https://www.python.org/downloads/)). On Windows, tick *Add to PATH*, and use `py` if `python` is not found.
2. **Open a bot folder**, then install and run:
   ```
   pip install -r requirements.txt
   python main.py
   ```
3. **Fill the `SETTINGS` block** at the top of that bot's `main.py` before you run it.

Each folder has its own short README with the exact steps.

## Free AI keys

You need one key, two is better (the bot switches if one fails).

- [Gemini](https://aistudio.google.com/apikey)
- [Groq](https://console.groq.com/keys)

## Stay safe

- 🔑 Never commit passwords, API keys, `sessionid` or session files. The `.gitignore` already skips them.
- 🧪 Use a new account, a few groups only, and one copy of the bot per account.
- 🐢 Faster replies mean more ban risk. Slow the polling down if you see errors.

## License

[MIT](LICENSE) · Not affiliated with Instagram, WhatsApp or Meta.
