#!/usr/bin/env python3
# ============================================================
#  WhatsApp AI Group Bot
# ============================================================
import atexit
import copy
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv

# ====== SETTINGS (only edit this block) ======
GROQ_API_KEY = 'PASTE_GROQ_KEY_HERE'        # console.groq.com/keys   (leave as is to skip Groq)
GEMINI_API_KEY = 'PASTE_GEMINI_KEY_HERE'    # aistudio.google.com/apikey (leave as is to skip Gemini)
ADMIN = '91XXXXXXXXXX'                      # YOUR number: country code + number, digits only
GROQ_MODELS = ['openai/gpt-oss-120b', 'openai/gpt-oss-20b'] 
GEMINI_MODEL = 'gemini-flash-lite-latest'
PRIMARY_AI = 'gemini'                       # which AI answers first: 'gemini' or 'groq' 
SESSION_DB = 'session.db'                   # WhatsApp login is saved here (scan the QR only once)
# =============================================

HAS_GROQ = bool(GROQ_API_KEY) and not GROQ_API_KEY.startswith('PASTE')
HAS_GEMINI = bool(GEMINI_API_KEY) and not GEMINI_API_KEY.startswith('PASTE')
ADMIN_DIGITS = re.sub(r'\D', '', ADMIN)
CONFIG_FILE = 'config.json'
CHAT_LOG_FILE = 'chat_log.json'
LOCK = threading.RLock()


def log(*a):
    print(datetime.now().strftime('%H:%M:%S'), *a, flush=True)


if not ADMIN_DIGITS or 'X' in ADMIN.upper():
    log('Set ADMIN (your number) in the SETTINGS block.')
    sys.exit(1)
if not HAS_GROQ and not HAS_GEMINI:
    log('Add at least one API key (Gemini or Groq) in the SETTINGS block.')
    sys.exit(1)


defaults = {
    'enabled': True,
    'mode': 'mention',
    'cooldownSec': 20,
    'chancePct': 60,
    'allowedGroups': [],
    'groupNames': {},
    'admins': [],
    'ignored': [],
    'personaCustom': False,
    'persona': 'Tum is group ke ek normal dost ho jo casual Hinglish me baat karta hai.',
}
cfg = copy.deepcopy(defaults)
try:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg.update(json.load(f))
except Exception:
    pass
if not cfg.get('personaCustom'):
    cfg['persona'] = defaults['persona']


def save():
    with LOCK:
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log('Could not save config:', e)


history = {}
style_samples = {}
chat_log = {}
last_reply = {}
_flush = {'t': 0.0}
ME_IDS = set()


def load_chat_log():
    try:
        with open(CHAT_LOG_FILE, 'r', encoding='utf-8') as f:
            chat_log.update(json.load(f))
    except Exception:
        pass
    for gid in cfg['allowedGroups']:
        history[gid] = list(chat_log.get(gid, []))[-25:]
        rebuild_samples(gid)


def flush_chat_log(force=False):
    with LOCK:
        if not force and time.time() - _flush['t'] < 10:
            return
        _flush['t'] = time.time()
        try:
            with open(CHAT_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(chat_log, f, ensure_ascii=False)
        except Exception as e:
            log('Could not save chat log:', e)


atexit.register(lambda: flush_chat_log(True))


def rebuild_samples(gid):
    rows = chat_log.get(gid, [])
    style_samples[gid] = [r['text'] for r in rows if r['name'] != 'Bot' and 2 <= len(r['text']) <= 120][-40:]


def remember(gid, name, text):
    with LOCK:
        row = {'name': name, 'text': text}
        h = history.setdefault(gid, [])
        h.append(row)
        if len(h) > 25:
            h.pop(0)
        c = chat_log.setdefault(gid, [])
        c.append(row)
        if len(c) > 300:
            del c[:-300]
        rebuild_samples(gid)
    flush_chat_log()


LENGTHS = [
    {'w': 20, 'max': 35,  'hint': 'Bahut chhota: 1 se 3 shabd (jaise "haha sahi", "arre wah")'},
    {'w': 35, 'max': 70,  'hint': 'Ek chhoti line, 5 se 10 shabd'},
    {'w': 28, 'max': 140, 'hint': 'Do chhoti lines, isse zyada nahi'},
    {'w': 5,  'max': 25,  'hint': 'Sirf ek reaction (jaise "lol", "bruh", "hmm")'},
    {'w': 12, 'max': 260, 'hint': 'Thoda detail me (3-4 lines), kyunki jawab dena zaroori hai'},
]
EMOJI_FALLBACK = ['😂', '😭', '😅', '🤣', '😏', '👀', '💀', '🙄', '😎']
TONES = [
    {'w': 35, 'hint': 'normal chill dost, seedhi baat'},
    {'w': 15, 'hint': 'thoda sarcastic'},
    {'w': 12, 'hint': 'supportive, sahi salah dene wala'},
    {'w': 12, 'hint': 'lazy aur bore sa'},
    {'w': 10, 'hint': 'seedha serious, koi mazak nahi'},
    {'w': 8,  'hint': 'curious, ek chhota sawal pucho'},
    {'w': 8,  'hint': 'halka mazaakiya'},
]
HUMAN_RULES = (
    'Tum AI assistant nahi ho, ek insaan jaise dost ho jo group me chat kar raha hai. Rules:\n'
    '- Kisi ko hurt karne wali baat kar sakte kyoki sab dost hai gali bhi chalega\n'
    '- PEHLE LOGIC: samjho saamne wale ne kya kaha ya poocha, aur jawab usi se match hona chahiye. Sawal ya "A ya B" jaisa choice ho to seedha ek chuno aur chhota reason do. "sahi hai" ya "kyun?" jaisa bina matlab ka jawab tabhi do jab sach me wahi fit ho.\n'
    '- Length aur tone sirf guide hain, logic unse upar hai.\n'
    '- Group ke log jaise likhte hain (neeche examples) waisi hi language, slang aur vibe rakho, par unki lines copy mat karo aur gaali do.\n'
    '- Jis language aur script me last message likha hai usi me jawab do. Hinglish roman letters me; Devanagari tabhi jab saamne wala likhe.\n'
    '- "Sure", "Chalo", "Batao kya scene hai" jaise assistant wale ya baar-baar repeat hone wale opening mat use karo.\n'
    '- Politics, dharm jaise sensitive topics par kisi ka side mat lo; halke mazak me ya "yaar isme mat ghuso" jaisa bolke taal do.\n'
    '- Explain mat karo, gyaan mat do. Bas waise bolo jaise dost WhatsApp pe bolta hai.\n'
    '- Kisi ko hurt karne wali baat kar sakte kyoki sab dost hai gali bhi chalega'
)

EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2300-\u23FF'
    '\u00A9\u00AE\u203C\u2049\u2122\u2139\u3030\u303D\u3297\u3299]\uFE0F?'
)
NEEDS_ANSWER_RE = re.compile(r'\?|\b(or|ya|choose|batao|bol|kya|kyu|kyun|kaise|kaun|kab|kitna|kidhar|kahan)\b', re.I)


def pick(items):
    r = random.random() * sum(x['w'] for x in items)
    for x in items:
        r -= x['w']
        if r <= 0:
            return x
    return items[0]


def lengths_for(text):
    return [l for l in LENGTHS if l['max'] >= 70] if NEEDS_ANSWER_RE.search(text or '') else LENGTHS


def humanize(raw, style):
    t = (raw or '').strip()
    t = re.sub(r'^["“\'`]+|["”\'`]+$', '', t)
    t = re.sub(r'^(bot|me|tum)\s*:\s*', '', t, flags=re.I)
    emojis = EMOJI_RE.findall(t)
    t = EMOJI_RE.sub('', t).replace('\u200d', '')
    t = re.sub(r'\s{2,}', ' ', t).strip()
    t = re.sub(r'\s+([.!?,])', r'\1', t)
    limit = math.floor(style['len']['max'] * 1.6)
    if len(t) > limit:
        sentences = re.split(r'(?<=[.!?])\s+', t)
        out = ''
        for sen in sentences:
            if len((out + ' ' + sen).strip()) > limit:
                break
            out = (out + ' ' + sen).strip()
        if not out:
            first = sentences[0]
            out = first if len(first) <= limit * 2.5 else re.sub(r'[\s,;:-]+\S*$', '', first[:limit])
        t = out.strip()
    t = re.sub(r'\.+$', '', t)
    if random.random() < 0.5:
        t = t.lower()
    if style['emoji']:
        use = emojis[:2]
        if not use:
            use.append(random.choice(EMOJI_FALLBACK))
        t = (t + ' ' + ''.join(use)).strip()
    if not t:
        t = emojis[0] if emojis else 'haha'
    return t


GEMINI_MODELS = [GEMINI_MODEL]


def _json(res):
    try:
        data = res.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def call_groq(model, system, user, ms):
    global GROQ_MODELS
    payload = {
        'model': model,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'temperature': 0.85,
        'max_tokens': 600,
    }
    if 'gpt-oss' in model:
        payload['reasoning_effort'] = 'medium'
        payload['max_tokens'] = 2500
    res = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {GROQ_API_KEY}'},
        json=payload, timeout=ms / 1000,
    )
    data = _json(res)
    choices = data.get('choices') or [{}]
    text = ((choices[0].get('message') or {}).get('content') or '').strip()
    if text:
        return text
    if res.status_code == 404:
        GROQ_MODELS = [m for m in GROQ_MODELS if m != model]
    err = data.get('error') or {}
    why = (err.get('message') if isinstance(err, dict) else str(err)) or f"empty reply, finish_reason={choices[0].get('finish_reason')}"
    raise RuntimeError(f'groq/{model} -> {res.status_code} {str(why)[:150]}')


def call_gemini(model, system, user, ms):
    global GEMINI_MODELS
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}'
    res = requests.post(
        url, headers={'Content-Type': 'application/json'},
        json={
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'parts': [{'text': user}]}],
            'generationConfig': {'temperature': 0.85, 'maxOutputTokens': 400},
        },
        timeout=ms / 1000,
    )
    data = _json(res)
    cands = data.get('candidates') or [{}]
    parts = ((cands[0].get('content') or {}).get('parts')) or []
    text = ''.join(p.get('text', '') for p in parts).strip()
    if text:
        return text
    if res.status_code == 404:
        GEMINI_MODELS = [m for m in GEMINI_MODELS if m != model]
    raise RuntimeError(f'gemini/{model} -> {res.status_code} {json.dumps(data)[:100]}')


def call_gemini_retry(model, system, user, ms):
    try:
        return call_gemini(model, system, user, ms)
    except Exception as e:
        if ' 503 ' in str(e):
            time.sleep(1.5)
            return call_gemini(model, system, user, ms)
        raise


def load_groq_models():
    global GROQ_MODELS
    if not HAS_GROQ:
        return
    try:
        res = requests.get('https://api.groq.com/openai/v1/models',
                           headers={'Authorization': f'Bearer {GROQ_API_KEY}'}, timeout=15)
        data = _json(res)
        bad = re.compile(r'whisper|guard|tts|orpheus|compound|embed|playai|safeguard|prompt', re.I)
        live = [m.get('id') for m in (data.get('data') or [])
                if m.get('active') is not False and m.get('id') and not bad.search(m.get('id'))]
        if not live:
            return
        wanted = [m for m in GROQ_MODELS if m in live]
        others = sorted([m for m in live if m not in wanted], key=lambda x: 0 if 'gpt-oss' in x else 1)
        GROQ_MODELS = (wanted + others)[:3]
        log('Groq models:', ', '.join(GROQ_MODELS))
    except Exception as e:
        log('Could not load Groq model list:', e)


def load_gemini_models():
    global GEMINI_MODELS
    if not HAS_GEMINI:
        return
    try:
        res = requests.get(f'https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}&pageSize=100',
                           timeout=15)
        data = _json(res)
        skip = re.compile(r'image|tts|live|audio|embed|robotics|computer|native')
        found = []
        for m in (data.get('models') or []):
            n = (m.get('name') or '').replace('models/', '')
            if 'generateContent' in (m.get('supportedGenerationMethods') or []) and 'flash' in n \
                    and not skip.search(n) and n != GEMINI_MODEL:
                found.append(n)
        found.sort(key=lambda x: 0 if 'lite' in x else 1)
        GEMINI_MODELS = ([GEMINI_MODEL] + found)[:4]
        log('Gemini models:', ', '.join(GEMINI_MODELS))
    except Exception as e:
        log('Could not load Gemini model list:', e)


def race(jobs):
    ex = ThreadPoolExecutor(max_workers=len(jobs))
    futs = [ex.submit(j) for j in jobs]
    errors = []
    try:
        for f in as_completed(futs):
            try:
                return f.result()
            except Exception as e:
                errors.append(e)
        raise RuntimeError(' | '.join(str(e) for e in errors))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def ask_ai(gid, style, ctx):
    with LOCK:
        msgs = list(history.get(gid, []))
        samples = '\n'.join(f'- {x}' for x in style_samples.get(gid, [])[-30:])
    convo = '\n'.join(f"{m['name']}: {m['text']}" for m in msgs)
    mine = '\n'.join(f"- {m['text']}" for m in [m for m in msgs if m['name'] == 'Bot'][-3:])
    system = f"{cfg['persona']}\n\n{HUMAN_RULES}"
    if samples:
        system += f'\n\nIs group ke log aise likhte hain (sirf style dekho, copy mat karo):\n{samples}'
    user = f'Group ki recent chat:\n{convo}\n\n'
    if mine:
        user += f'Tumhare pichle replies (inki tarah shuru mat karo, repeat mat karo):\n{mine}\n\n'
    user += f"Ab sirf is message ka reply do:\n{ctx['name']}: {ctx['text']}\n"
    if ctx.get('quoted'):
        user += f"(Ye us message ka reply hai jisme likha tha: \"{ctx['quoted']}\")\n"
    user += f"\nLength: {style['len']['hint']}\nTone: {style['tone']['hint']}\n"
    user += f"Emoji: {'1-2 emoji lagao jo mood se match karein' if style['emoji'] else 'bilkul emoji mat lagao'}\n"
    user += 'Sirf reply text likho, naam ya prefix nahi.'

    def tagged(src, fn):
        return lambda: (src, fn())

    gemini_lanes, groq_lanes = [], []
    if HAS_GEMINI:
        gemini_lanes.append(lambda: [tagged(f'gemini/{m}', lambda m=m: call_gemini_retry(m, system, user, 12000))
                                     for m in GEMINI_MODELS[:2]])
        gemini_lanes.append(lambda: [tagged(f'gemini/{m}', lambda m=m: call_gemini_retry(m, system, user, 12000))
                                     for m in GEMINI_MODELS[2:]])
    if HAS_GROQ:
        groq_lanes.append(lambda: [tagged(f'groq/{m}', lambda m=m: call_groq(m, system, user, 15000))
                                   for m in GROQ_MODELS[:2]])

    if PRIMARY_AI == 'gemini':
        lanes = gemini_lanes[:1] + groq_lanes + gemini_lanes[1:]
    else:
        lanes = groq_lanes + gemini_lanes
    for lane in lanes:
        jobs = lane()
        if not jobs:
            continue
        try:
            src, text = race(jobs)
            log('ANSWER FROM:', src)
            return text
        except Exception as e:
            log('AI fail:', e)
    return None


def digits(x):
    return re.sub(r'\D', '', str(x or ''))


def jid_user(j):
    if j is None:
        return ''
    if isinstance(j, str):
        return digits(j.split('@')[0].split(':')[0])
    return digits(getattr(j, 'User', ''))


def jid_str(j):
    return f"{getattr(j, 'User', '')}@{getattr(j, 'Server', '')}"


def get_text(msg):
    try:
        from neonize.utils.message import extract_text
        t = extract_text(msg)
        if t:
            return t
    except Exception:
        pass
    ext = getattr(msg, 'extendedTextMessage', None)
    return getattr(msg, 'conversation', '') or getattr(ext, 'text', '') or ''


def ctx_info(msg):
    return getattr(getattr(msg, 'extendedTextMessage', None), 'contextInfo', None)


def quoted_text_of(ci):
    qm = getattr(ci, 'quotedMessage', None) if ci is not None else None
    if qm is None:
        return ''
    ext = getattr(qm, 'extendedTextMessage', None)
    return (getattr(qm, 'conversation', '') or getattr(ext, 'text', '') or '')[:200]


def msg_age(info):
    try:
        t = float(getattr(info, 'Timestamp', 0) or 0)
        if t > 1e12:
            t /= 1000
        return time.time() - t if t > 0 else 0
    except Exception:
        return 0


def load_me(client):
    try:
        me = client.get_me()
        for attr in ('JID', 'LID'):
            u = jid_user(getattr(me, attr, None))
            if u:
                ME_IDS.add(u)
        log('Bot account ids:', ', '.join(sorted(ME_IDS)) or 'unknown')
    except Exception as e:
        log('Could not read own account id:', e)


def typing(client, chat):
    try:
        from neonize.utils.enum import ChatPresence, ChatPresenceMedia
        client.send_chat_presence(chat, ChatPresence.CHAT_PRESENCE_COMPOSING,
                                  ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
    except Exception:
        pass


def find_targets(event, arg):
    ci = ctx_info(event.Message)
    raw = []
    if ci is not None:
        raw += [str(x) for x in (getattr(ci, 'mentionedJID', None) or [])]
        p = getattr(ci, 'participant', '')
        if p:
            raw.append(str(p))
    typed = digits(arg)
    if len(typed) >= 10:
        raw.append(typed)
    out = []
    for r in raw:
        u = jid_user(r)
        if u and u not in ME_IDS and [u] not in out:
            out.append([u])
    return out


HELP = (
    '*Admin commands*\n'
    '!on / !off : bot on/off\n'
    '!allow : enable bot in this group\n'
    '!deny : disable bot in this group\n'
    '!mode mention|all : when to reply\n'
    '!chance <0-100> : in "all" mode, % of messages to answer\n'
    '!cooldown <sec> : gap between replies\n'
    '!persona <text> : change bot style (!persona reset = default)\n'
    '!addadmin : (owner) reply to / @tag / type number\n'
    '!removeadmin : (owner) same as above\n'
    '!ignore : reply to / @tag / type number\n'
    '!unignore : same as above\n'
    '!ignorelist : list ignored people\n'
    '!admins : list admins\n'
    '!groups : groups where bot is ON\n'
    "!learn : re-learn this group's style from saved chat\n"
    '!status : current settings'
)


def handle_admin(client, event, text, is_owner):
    parts = text.strip().split()
    cmd = parts[0].lower()
    arg = ' '.join(parts[1:])
    chat = event.Info.MessageSource.Chat
    is_group = bool(event.Info.MessageSource.IsGroup)
    gid = jid_str(chat) if is_group else None
    out = None

    if cmd == '!help':
        out = HELP
    elif cmd == '!on':
        cfg['enabled'] = True
        out = 'Bot ON ✅'
    elif cmd == '!off':
        cfg['enabled'] = False
        out = 'Bot OFF 🛑'
    elif cmd == '!allow':
        if not gid:
            out = 'Send this command inside a group.'
        else:
            if gid not in cfg['allowedGroups']:
                cfg['allowedGroups'].append(gid)
            try:
                cfg['groupNames'][gid] = client.get_group_info(chat).GroupName.Name
            except Exception:
                pass
            history.setdefault(gid, list(chat_log.get(gid, []))[-25:])
            rebuild_samples(gid)
            out = 'Bot enabled in this group ✅ (it learns the group style as it watches)'
    elif cmd == '!deny':
        if not gid:
            out = 'Send this command inside a group.'
        else:
            cfg['allowedGroups'] = [g for g in cfg['allowedGroups'] if g != gid]
            out = 'Bot disabled in this group 🛑'
    elif cmd == '!learn':
        if not gid:
            out = 'Send this command inside a group.'
        else:
            rebuild_samples(gid)
            out = f"Style re-learned from {len(chat_log.get(gid, []))} saved messages ✅"
    elif cmd == '!mode':
        if arg not in ('mention', 'all'):
            out = 'Use: !mode mention  or  !mode all'
        else:
            cfg['mode'] = arg
            out = f'Mode: {arg}'
    elif cmd == '!chance':
        try:
            v = float(arg)
            if not 0 <= v <= 100:
                raise ValueError
            cfg['chancePct'] = int(v) if v == int(v) else v
            out = f'Chance: {arg}%'
        except ValueError:
            out = 'Use: !chance 60'
    elif cmd == '!cooldown':
        try:
            v = float(arg)
            cfg['cooldownSec'] = int(v) if v == int(v) else v
            out = f'Cooldown: {arg}s'
        except ValueError:
            out = 'Use: !cooldown 20'
    elif cmd == '!persona':
        if not arg:
            out = 'Use: !persona <describe the style>  or  !persona reset'
        elif arg.lower() == 'reset':
            cfg['persona'] = defaults['persona']
            cfg['personaCustom'] = False
            out = 'Persona reset ✅'
        else:
            cfg['persona'] = arg
            cfg['personaCustom'] = True
            out = 'Persona updated ✅'
    elif cmd in ('!addadmin', '!removeadmin'):
        if not is_owner:
            out = 'Only the owner can manage admins.'
        else:
            targets = find_targets(event, arg)
            if not targets:
                out = 'Reply to the person, @tag them, or type the number. Example: !addadmin 919876543210'
            else:
                adding = cmd == '!addadmin'
                count = 0
                for ids in targets:
                    if ADMIN_DIGITS in ids:
                        continue
                    known = any(set(a) & set(ids) for a in cfg['admins'])
                    if adding and not known:
                        cfg['admins'].append(ids)
                        count += 1
                    if not adding and known:
                        cfg['admins'] = [a for a in cfg['admins'] if not set(a) & set(ids)]
                        count += 1
                out = f"Admin {'added' if adding else 'removed'} ✅ ({count})"
    elif cmd in ('!ignore', '!unignore'):
        targets = find_targets(event, arg)
        if not targets:
            out = f'Reply to the person, @tag them, or type the number. Example: {cmd} 919876543210'
        else:
            adding = cmd == '!ignore'
            count = 0
            for ids in targets:
                if ADMIN_DIGITS in ids or any(set(a) & set(ids) for a in cfg['admins']):
                    continue
                known = any(set(a) & set(ids) for a in cfg['ignored'])
                if adding and not known:
                    cfg['ignored'].append(ids)
                    count += 1
                if not adding and known:
                    cfg['ignored'] = [a for a in cfg['ignored'] if not set(a) & set(ids)]
                    count += 1
            out = f"{'Ignored' if adding else 'Un-ignored'} ✅ ({count})"
    elif cmd == '!ignorelist':
        out = 'Ignored: ' + (', '.join(a[0] for a in cfg['ignored']) or 'none')
    elif cmd == '!admins':
        extra = ', '.join(a[0] for a in cfg['admins']) or 'none'
        out = f'Owner: {ADMIN_DIGITS}\nAdmins: {extra}'
    elif cmd == '!groups':
        names = [cfg['groupNames'].get(g, g) for g in cfg['allowedGroups']]
        out = (f'Bot is ON in {len(names)} group(s):\n' + '\n'.join(f'{i + 1}. {n}' for i, n in enumerate(names))
               if names else 'No group enabled yet. Send !allow inside a group.')
    elif cmd == '!status':
        out = (f"Enabled: {cfg['enabled']}\nMode: {cfg['mode']}\nChance: {cfg['chancePct']}%\n"
               f"Cooldown: {cfg['cooldownSec']}s\nAllowed groups: {len(cfg['allowedGroups'])}\n"
               f"Extra admins: {len(cfg['admins'])}\nPersona: {cfg['persona']}")
    else:
        return False
    save()
    client.send_message(chat, out)
    return True


def process(client, event):
    info = event.Info
    src = info.MessageSource
    if src.IsFromMe:
        return
    text = get_text(event.Message).strip()
    if not text:
        return

    sender_ids = {jid_user(src.Sender), jid_user(getattr(src, 'SenderAlt', None))} - {''}
    is_owner = ADMIN_DIGITS in sender_ids
    is_admin = is_owner or any(set(a) & sender_ids for a in cfg['admins'])
    is_group = bool(src.IsGroup)
    log('MSG | sender:', ','.join(sorted(sender_ids)), '| owner:', is_owner, '| admin:', is_admin, '| group:', is_group)

    if is_admin and text.startswith('!'):
        if handle_admin(client, event, text, is_owner):
            return

    if not is_admin and any(set(a) & sender_ids for a in cfg['ignored']):
        return

    if not is_group or text.startswith('!'):
        return
    chat = src.Chat
    gid = jid_str(chat)
    if not cfg['enabled'] or gid not in cfg['allowedGroups']:
        return

    name = (getattr(info, 'Pushname', '') or getattr(info, 'PushName', '') or '').strip() \
        or (sorted(sender_ids)[0] if sender_ids else 'Someone')
    remember(gid, name, text)

    if msg_age(info) > 120:
        return

    ci = ctx_info(event.Message)
    mentions = [jid_user(x) for x in (getattr(ci, 'mentionedJID', None) or [])] if ci is not None else []
    mentioned = any(m in ME_IDS for m in mentions) or \
        bool(ME_IDS and re.search(r'@(?:' + '|'.join(map(re.escape, ME_IDS)) + r')\b', text))
    replied_to_me = bool(ci is not None and jid_user(getattr(ci, 'participant', '')) in ME_IDS and ME_IDS)
    quoted = quoted_text_of(ci)
    directed = mentioned or replied_to_me or bool(re.match(r'^bot\b', text, re.I))

    should_reply = directed
    if cfg['mode'] == 'all' and not directed:
        p = cfg['chancePct'] / 100
        if '?' in text:
            p = max(p, 0.9)
        if len(text.strip()) < 3:
            p = min(p, 0.2)
        should_reply = random.random() < p
    if not should_reply:
        return

    with LOCK:
        now = time.time()
        gap = min(cfg['cooldownSec'], 5) if directed else cfg['cooldownSec']
        if now - last_reply.get(gid, 0) < gap:
            return
        last_reply[gid] = now

    typing(client, chat)
    style = {'len': pick(lengths_for(text)), 'tone': pick(TONES), 'emoji': random.random() < 0.7}
    raw = ask_ai(gid, style, {'name': name, 'text': text, 'quoted': quoted})
    if not raw:
        return
    reply = humanize(raw, style)

    typing(client, chat)
    time.sleep(min(3.5, 0.3 + len(reply) * 0.035 + random.random() * 0.5))
    remember(gid, 'Bot', reply)
    if directed or random.random() < 0.35:
        try:
            client.reply_message(reply, event)
            return
        except Exception as e:
            log('Quote-reply failed, sending plain message:', e)
    client.send_message(chat, reply)


POOL = ThreadPoolExecutor(max_workers=4)
client = NewClient(SESSION_DB)


def safe_process(c, event):
    try:
        process(c, event)
    except Exception as e:
        log('Error:', repr(e))


@client.event(ConnectedEv)
def on_connected(c, _):
    log('Bot ready!')
    load_me(c)
    load_groq_models()
    load_gemini_models()


@client.event(MessageEv)
def on_message(c, event):
    POOL.submit(safe_process, c, event)


if __name__ == '__main__':
    load_chat_log()
    log('Starting... scan the QR code if one appears (WhatsApp > Linked Devices > Link a Device)')
    try:
        client.connect()
    except KeyboardInterrupt:
        log('Stopped.')