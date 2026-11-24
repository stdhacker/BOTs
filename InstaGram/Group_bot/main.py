#!/usr/bin/env python3
# ============================================================
#  Instagram AI Group Bot  (v1)  -  port of the WhatsApp bot
#  Libraries: instagrapi (pip install instagrapi)  
# ============================================================
import copy
import inspect
import json
import math
import os
import random
import re
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests
from instagrapi import Client
from instagrapi import exceptions as igx

# ====== SETTINGS (only edit this block) ======
IG_USERNAME = os.environ.get('IG_USERNAME', 'PASTE_USERNAME_HERE')   # the Instagram account the bot runs on
IG_PASSWORD = os.environ.get('IG_PASSWORD', 'PASTE_PASSWORD_HERE')   # its password
IG_SESSIONID = os.environ.get('IG_SESSIONID', '')                     
TWO_FACTOR_SEED = ''                  
ADMIN_USERNAME = os.environ.get('IG_ADMIN', 'std_0x404')  
EXTRA_ADMINS = ['']                     

GROQ_API_KEY = 'PASTE_API_HERE'       
GEMINI_API_KEY = 'PASTE_API_HERE'         
GROQ_MODELS = ['openai/gpt-oss-120b', 'openai/gpt-oss-20b']  
GEMINI_MODEL = 'gemini-flash-lite-latest'
PRIMARY_AI = 'gemini'                 
BOT_OWNER = 'Nitesh!'
BOT_INTRO = 'I am JIRAYA, I love research of girls 🤤 and I am made by std_0x404 & Abhigyan mere PAPA.'

SESSION_FILE = 'ig_session.json'      
CONFIG_FILE = 'ig_config.json'        
PROXY = ''                            


POLL_MIN = 1                          
POLL_MAX = 2                          
LONG_BREAK_CHANCE = 0.0               
LONG_BREAK_RANGE = (40, 120)         
READ_DELAY_RANGE = (0.0, 0.2)         
DELAY_RANGE = [0, 0]                  
THREADS_PER_POLL = 3                  
PROCESS_PENDING = True                
PENDING_EVERY = 3                     
MAX_REPLIES_PER_HOUR = 0            


HAS_GROQ = bool(GROQ_API_KEY) and not GROQ_API_KEY.startswith('PASTE')
HAS_GEMINI = bool(GEMINI_API_KEY) and not GEMINI_API_KEY.startswith('PASTE')
ADMIN_NAME = ADMIN_USERNAME.strip().lstrip('@').lower()
ADMIN_NAMES = {ADMIN_NAME} | {x.strip().lstrip('@').lower() for x in EXTRA_ADMINS if x.strip()}


def log(*a):
    print(datetime.now().strftime('%H:%M:%S'), *a, flush=True)


if not ADMIN_NAME or ADMIN_NAME.startswith('paste'):
    log('Set ADMIN_USERNAME (your Instagram username) in the SETTINGS block.')
    sys.exit(1)
if IG_USERNAME.startswith('PASTE') or (IG_PASSWORD.startswith('PASTE') and not IG_SESSIONID):
    log('Set IG_USERNAME and IG_PASSWORD (or IG_SESSIONID) of the bot account in the SETTINGS block.')
    sys.exit(1)
if not HAS_GROQ and not HAS_GEMINI:
    log('Add at least one API key (Groq or Gemini) in the SETTINGS block.')
    sys.exit(1)


def _exc(*names):
    return tuple(getattr(igx, n) for n in names if hasattr(igx, n))


RATE_ERRORS = _exc('PleaseWaitFewMinutes', 'RateLimitError', 'ClientThrottledError', 'FeedbackRequired')
LOGIN_ERRORS = _exc('LoginRequired', 'ClientLoginRequired')
CHALLENGE_ERRORS = _exc('ChallengeRequired', 'ChallengeUnknownStep', 'SelectContactPointRecoveryForm',
                        'RecaptchaChallengeForm', 'ChallengeSelfieCaptcha')
TWOFA_ERRORS = _exc('TwoFactorRequired')

defaults = {
    'enabled': True,
    'mode': 'mention',
    'cooldownSec': 20,
    'chancePct': 60,
    'allowedGroups': [],
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
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log('Could not save config:', e)


history = {}
last_reply = {}
style_samples = {}
loaded = {}
names = {}
seen = {}
sent_times = deque()
bot_texts = {}
baselined = set()
first_poll = {'done': False}
ME = {'id': '', 'username': ''}
ADMIN_IDS = set()
IGNORED_IDS = {}


def remember(gid, name, text):
    h = history.setdefault(gid, [])
    h.append({'name': name, 'text': text})
    if len(h) > 25:
        h.pop(0)


def _g(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def msg_ts(m):
    t = getattr(m, 'timestamp', None)
    if not isinstance(t, datetime):
        return datetime.fromtimestamp(0, timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def is_me(m):
    return str(getattr(m, 'user_id', '') or '') == ME['id'] or bool(getattr(m, 'is_sent_by_viewer', False))


def update_names(th):
    for u in (getattr(th, 'users', None) or []):
        pk = str(_g(u, 'pk', '') or '')
        if pk:
            names[pk] = (_g(u, 'full_name') or _g(u, 'username') or 'Someone').strip() or 'Someone'
            uname = _g(u, 'username')
            if uname:
                names['@' + pk] = uname


def name_of(cl, uid):
    uid = str(uid or '')
    if uid in names:
        return names[uid]
    try:
        names[uid] = cl.username_from_user_id(int(uid)) or 'Someone'
    except Exception:
        names[uid] = 'Someone'
    return names[uid]


def is_admin_user(cl, uid):
    uid = str(uid or '')
    if uid in ADMIN_IDS:
        return True
    return (names.get('@' + uid) or '').lower() in ADMIN_NAMES


def is_ignored(uid):
    uid = str(uid or '')
    if not uid or is_admin_user(None, uid):
        return False
    if uid in IGNORED_IDS.values():
        return True
    return (names.get('@' + uid) or '').lower() in cfg['ignored']


def sleep(sec):
    time.sleep(max(0, sec))


def load_history(cl, th):
    gid = str(th.id)
    if loaded.get(gid):
        return True
    loaded[gid] = True
    try:
        msgs = cl.direct_messages(int(gid), amount=80)
        msgs = sorted(msgs, key=msg_ts)
        rows = []
        for m in msgs:
            text = (getattr(m, 'text', None) or '').strip()
            if not text or text.startswith('!'):
                continue
            rows.append({'name': 'Bot' if is_me(m) else name_of(cl, getattr(m, 'user_id', '')), 'text': text})
        history[gid] = rows[-25:]
        style_samples[gid] = [r['text'] for r in rows if r['name'] != 'Bot' and 2 <= len(r['text']) <= 120][-40:]
        log(f'Old chat loaded: {len(rows)} messages ({getattr(th, "thread_title", "") or gid})')
        return True
    except Exception as e:
        loaded[gid] = False
        log('Could not read old chat:', e)
        return False


LENGTHS = [
    {'w': 20, 'max': 35,  'hint': 'Bahut chhota: 1 se 3 shabd (jaise "haha sahi", "arre wah")'},
    {'w': 35, 'max': 70,  'hint': 'Ek chhoti line, 5 se 10 shabd'},
    {'w': 28, 'max': 140, 'hint': 'Do chhoti lines, isse zyada nahi'},
    {'w': 5,  'max': 25,  'hint': 'Sirf ek reaction (jaise "lol", "bruh", "hmm")'},
    {'w': 12, 'max': 260, 'hint': 'Thoda detail me (3-4 lines), kyunki jawab dena zaroori hai'},
]
EMOJI_FALLBACK = ['😂', '😭', '😅', '🤣', '😏', '👀', '💀', '🙄', '😎']
TONES = [
    {'w': 35, 'hint': 'normal dost, seedhi baat'},
    {'w': 15, 'hint': 'thoda sarcastic'},
    {'w': 12, 'hint': 'supportive, sahi salah dene wala'},
    {'w': 12, 'hint': 'lazy aur bore sa'},
    {'w': 10, 'hint': 'seedha serious, koi mazak nahi'},
    {'w': 8,  'hint': 'curious, ek chhota sawal pucho'},
    {'w': 8,  'hint': 'halka mazaakiya'},
]
HUMAN_RULES = (
    'Tum AI assistant nahi ho, ek insaan jaise dost ho jo group me chat kar raha hai. Rules:\n'
    f'- Agar koi poochhe tum kaun ho, who are you, bot ho kya, ya kisne banaya (kisi bhi language me), to apna intro do. Intro ka matlab: "{BOT_INTRO}". '
    f'Isko chhota rakho aur usi language me likho jisme sawal poochha gaya (Hindi me poochha to Hindi me, English me poochha to English me). '
    f'Naam JIRAYA aur {BOT_OWNER} bilkul waise hi likho, badlo mat, aur emoji rakho. Is jawab me length ki limit ignore karo.\n'
    '- PEHLE LOGIC: samjho saamne wale ne kya kaha ya poocha, aur jawab usi se match hona chahiye. Sawal ya "A ya B" jaisa choice ho to seedha ek chuno aur chhota reason do. "sahi hai" ya "kyun?" jaisa bina matlab ka jawab tabhi do jab sach me wahi fit ho.\n'
    '- Length aur tone sirf guide hain, logic unse upar hai.\n'
    '- Group ke log jaise likhte hain (neeche examples) waisi hi language, slang aur vibe rakho, par unki lines copy mat karo aur gaali mat do.\n'
    '- Jis language aur script me last message likha hai usi me jawab do. Hinglish roman letters me; Devanagari tabhi jab saamne wala likhe.\n'
    '- "Sure", "Chalo", "Batao kya scene hai" jaise assistant wale ya baar-baar repeat hone wale opening mat use karo.\n'
    '- Politics, dharm jaise sensitive topics par kisi ka side mat lo; halke mazak me ya "yaar isme mat ghuso" jaisa bolke taal do.\n'
    '- Explain mat karo, gyaan mat do. Bas waise bolo jaise dost WhatsApp pe bolta hai.\n'
    '- Kisi ko hurt karne wali baat bhi bol sakte ho sab apne dost hain.'
)

EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿'
    '©®‼⁉™ℹ〰〽㊗㊙]️?'
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
    needs = bool(NEEDS_ANSWER_RE.search(text or ''))
    return [l for l in LENGTHS if l['max'] >= 70] if needs else LENGTHS


def humanize(raw, style):
    t = (raw or '').strip()
    t = re.sub(r'^["“\'`]+|["”\'`]+$', '', t)
    t = re.sub(r'^(bot|me|tum)\s*:\s*', '', t, flags=re.I)
    emojis = EMOJI_RE.findall(t)
    t = EMOJI_RE.sub('', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
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
        payload['reasoning_effort'] = 'high'
        payload['max_tokens'] = 4000
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
        others = [m for m in live if m not in wanted]
        others.sort(key=lambda x: 0 if 'gpt-oss' in x else 1)
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
        log('Gemini backup models:', ', '.join(GEMINI_MODELS))
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
    msgs = history.get(gid, [])
    convo = '\n'.join(f"{m['name']}: {m['text']}" for m in msgs)
    mine = '\n'.join(f"- {m['text']}" for m in [m for m in msgs if m['name'] == 'Bot'][-3:])
    samples = '\n'.join(f'- {x}' for x in style_samples.get(gid, [])[-30:])
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

    groq_lanes = []
    gemini_lanes = []
    if HAS_GROQ:
        groq_lanes.append(lambda: [(lambda m=m: call_groq(m, system, user, 20000)) for m in GROQ_MODELS[:2]])
    if HAS_GEMINI:
        gemini_lanes.append(lambda: [(lambda m=m: call_gemini_retry(m, system, user, 12000)) for m in GEMINI_MODELS[:2]])
        gemini_lanes.append(lambda: [(lambda m=m: call_gemini_retry(m, system, user, 12000)) for m in GEMINI_MODELS[2:]])
    lanes = (gemini_lanes + groq_lanes) if PRIMARY_AI == 'gemini' else (groq_lanes + gemini_lanes)
    for lane in lanes:
        jobs = lane()
        if not jobs:
            continue
        try:
            return race(jobs)
        except Exception as e:
            log('AI fail:', e)
    return None


def new_client():
    cl = Client()
    cl.delay_range = DELAY_RANGE
    if PROXY:
        cl.set_proxy(PROXY)
    return cl


def do_login(cl):
    code = ''
    if TWO_FACTOR_SEED:
        try:
            code = cl.totp_generate_code(TWO_FACTOR_SEED)
        except Exception as e:
            log('Could not generate 2FA code:', e)
    try:
        cl.login(IG_USERNAME, IG_PASSWORD, verification_code=code)
    except TWOFA_ERRORS:
        code = input('Instagram 2FA code: ').strip()
        cl.login(IG_USERNAME, IG_PASSWORD, verification_code=code)


def dump_session(cl):
    try:
        cl.dump_settings(SESSION_FILE)
    except Exception as e:
        log('Could not save session:', e)


def ig_login():
    if IG_SESSIONID:
        cl = new_client()
        if os.path.exists(SESSION_FILE):
            try:
                cl.set_settings(cl.load_settings(SESSION_FILE))
                cl.get_timeline_feed()
                log('Logged in using saved session.')
                return cl
            except Exception as e:
                log('Saved session not usable, using IG_SESSIONID:', e)
                cl = new_client()
        cl.login_by_sessionid(IG_SESSIONID)
        log('Logged in using sessionid.')
        dump_session(cl)
        return cl

    cl = new_client()
    old_uuids = None
    ok = False
    if os.path.exists(SESSION_FILE):
        try:
            session = cl.load_settings(SESSION_FILE)
            old_uuids = (session or {}).get('uuids')
            cl.set_settings(session)
            do_login(cl)
            try:
                cl.get_timeline_feed()
            except LOGIN_ERRORS:
                log('Session expired, logging in again with the same device id...')
                relogin(cl)
            ok = True
            log('Logged in using saved session.')
        except Exception as e:
            log('Session login failed:', e)
    if not ok:
        cl = new_client()
        if old_uuids:
            cl.set_uuids(old_uuids)
        do_login(cl)
        log('Logged in with username/password.')
    dump_session(cl)
    return cl


def relogin(cl):
    old = cl.get_settings()
    cl.set_settings({})
    cl.set_uuids((old or {}).get('uuids', {}))
    do_login(cl)
    dump_session(cl)


def supports(fn, param):
    try:
        return param in inspect.signature(fn).parameters
    except Exception:
        return False


def send(cl, thread_id, text, reply_to=None):
    now = time.time()
    while sent_times and now - sent_times[0] > 3600:
        sent_times.popleft()
    sent_times.append(now)
    tids = [int(thread_id)]
    tkey = str(thread_id)
    bot_texts.setdefault(tkey, deque(maxlen=30)).append(text.strip().lower())
    res = None
    sent = False
    if reply_to is not None and supports(cl.direct_send, 'reply_to_message'):
        try:
            res = cl.direct_send(text, thread_ids=tids, reply_to_message=reply_to)
            sent = True
        except Exception as e:
            if RATE_ERRORS and isinstance(e, RATE_ERRORS):
                raise
            log('Quote-reply failed, sending plain message:', e)
    if not sent:
        res = cl.direct_send(text, thread_ids=tids)
    mid = getattr(res, 'id', None)
    if mid:
        seen.setdefault(tkey, set()).add(str(mid))
    return res


def hourly_cap_hit():
    if not MAX_REPLIES_PER_HOUR:
        return False
    now = time.time()
    while sent_times and now - sent_times[0] > 3600:
        sent_times.popleft()
    return len(sent_times) >= MAX_REPLIES_PER_HOUR


HELP = (
    '*Admin commands*\n'
    '!on / !off : bot on/off\n'
    '!allow : enable bot in this group\n'
    '!deny : disable bot in this group\n'
    '!mode mention|all : when to reply\n'
    '!chance <0-100> : in "all" mode, % of messages to answer\n'
    '!cooldown <sec> : gap between replies\n'
    '!persona <text> : change bot style (!persona reset = default)\n'
    "!learn : re-read this group's older chat\n"
    '!ignore <username> : bot stops replying to this user\n'
    '!unignore <username> : bot replies to this user again\n'
    '!ignorelist : show ignored users\n'
    '!status : current settings'
)


def handle_admin(cl, th, text):
    parts = text.strip().split()
    cmd = parts[0].lower()
    arg = ' '.join(parts[1:])
    is_group = bool(getattr(th, 'is_group', False))
    gid = str(th.id) if is_group else None
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
            load_history(cl, th)
            out = 'Bot enabled in this group ✅ (older chat read)'
    elif cmd == '!learn':
        if not gid:
            out = 'Send this command inside a group.'
        else:
            loaded[gid] = False
            load_history(cl, th)
            out = 'Older chat re-read ✅'
    elif cmd == '!deny':
        if not gid:
            out = 'Send this command inside a group.'
        else:
            cfg['allowedGroups'] = [g for g in cfg['allowedGroups'] if g != gid]
            out = 'Bot disabled in this group 🛑'
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
    elif cmd == '!ignore':
        uname = arg.strip().lstrip('@').lower()
        if not uname:
            out = 'Use: !ignore username'
        elif uname in ADMIN_NAMES:
            out = 'Admins cannot be ignored.'
        else:
            try:
                iid = str(cl.user_id_from_username(uname))
            except Exception:
                iid = ''
            if not iid or iid == 'None':
                out = f'@{uname} not found.'
            else:
                if uname not in cfg['ignored']:
                    cfg['ignored'].append(uname)
                IGNORED_IDS[uname] = iid
                names['@' + iid] = uname
                out = f'@{uname} is now ignored 🚫'
    elif cmd == '!unignore':
        uname = arg.strip().lstrip('@').lower()
        if not uname:
            out = 'Use: !unignore username'
        else:
            cfg['ignored'] = [u for u in cfg['ignored'] if u != uname]
            IGNORED_IDS.pop(uname, None)
            out = f'@{uname} is no longer ignored ✅'
    elif cmd == '!ignorelist':
        out = 'Ignored users:\n' + ('\n'.join(f'@{u}' for u in cfg['ignored']) if cfg['ignored'] else '(none)')
    elif cmd == '!status':
        out = (f"Enabled: {cfg['enabled']}\nMode: {cfg['mode']}\nChance: {cfg['chancePct']}%\n"
               f"Cooldown: {cfg['cooldownSec']}s\nAllowed groups: {len(cfg['allowedGroups'])}\n"
               f"Persona: {cfg['persona']}")
    else:
        return False
    save()
    sleep(random.uniform(*READ_DELAY_RANGE) / 2)
    send(cl, th.id, out)
    return True


def process_message(cl, th, m):
    text = (getattr(m, 'text', None) or '').strip()
    if not text:
        return
    uid = str(getattr(m, 'user_id', '') or '')
    if uid == ME['id'] or text.strip().lower() in bot_texts.get(str(th.id), ()):
        return
    admin = is_admin_user(cl, uid)
    is_group = bool(getattr(th, 'is_group', False))
    log(f'MSG | sender: {names.get("@" + uid) or uid} (id {uid}) | me: {ME["id"]} | admin: {admin} | group: {is_group}')

    if admin and text.startswith('!'):
        if handle_admin(cl, th, text):
            return

    if is_ignored(uid):
        return

    if not is_group or text.startswith('!'):
        return
    gid = str(th.id)
    name = name_of(cl, uid)

    if not cfg['enabled'] or gid not in cfg['allowedGroups']:
        return

    was_loaded = loaded.get(gid)
    ok = load_history(cl, th)
    if was_loaded or not ok:
        remember(gid, name, text)

    mentioned = bool(re.search(r'(?<![\w.])@' + re.escape(ME['username']) + r'(?![\w.])', text, re.I))
    replied_to_me = False
    quoted_text = ''
    rep = _g(m, 'reply') or _g(m, 'replied_to_message')
    if rep:
        ruid = str(_g(rep, 'user_id', '') or '')
        replied_to_me = (ruid == ME['id']) or bool(_g(rep, 'is_sent_by_viewer', False))
        quoted_text = str(_g(rep, 'text', '') or '')[:200]
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

    now = time.time()
    gap = (min(cfg['cooldownSec'], 5) if directed else cfg['cooldownSec'])
    if now - last_reply.get(gid, 0) < gap:
        return
    if hourly_cap_hit():
        log('Hourly reply cap reached, skipping this reply.')
        return
    last_reply[gid] = now

    sleep(random.uniform(*READ_DELAY_RANGE))
    style = {'len': pick(lengths_for(text)), 'tone': pick(TONES), 'emoji': random.random() < 0.7}
    raw = ask_ai(gid, style, {'name': name, 'text': text, 'quoted': quoted_text})
    if not raw:
        return
    if BOT_OWNER and BOT_OWNER.lower() in raw.lower():
        reply = raw.strip()
    else:
        reply = humanize(raw, style)

    remember(gid, 'Bot', reply)
    if directed or random.random() < 0.35:
        send(cl, th.id, reply, reply_to=m)
    else:
        send(cl, th.id, reply)


def is_recent(m, secs=600):
    t = getattr(m, 'timestamp', None)
    if not isinstance(t, datetime):
        return False
    now = datetime.now(timezone.utc)
    cands = [t] if t.tzinfo else [t.replace(tzinfo=timezone.utc), t.astimezone(timezone.utc)]
    return any(-secs <= (now - c).total_seconds() <= secs for c in cands)


def handle_thread(cl, th):
    tid = str(th.id)
    update_names(th)
    s = seen.setdefault(tid, set())
    if len(s) > 1000:
        s.clear()
    msgs = sorted(getattr(th, 'messages', None) or [], key=msg_ts)
    if tid not in baselined:
        baselined.add(tid)
        title = getattr(th, 'thread_title', '') or ', '.join(str(_g(u, 'username', '')) for u in (getattr(th, 'users', None) or []))
        log(f'Chat found: {title} | id {tid} | group: {bool(getattr(th, "is_group", False))}')
        if not first_poll['done']:
            for m in msgs:
                s.add(str(getattr(m, 'id', '')))
            return
        newest = msgs[-1:]
        for m in msgs[:-1]:
            s.add(str(getattr(m, 'id', '')))
        if newest and not is_recent(newest[0]):
            s.add(str(getattr(newest[0], 'id', '')))
            newest = []
        msgs = newest
    for m in msgs:
        mid = str(getattr(m, 'id', ''))
        if not mid or mid in s:
            continue
        s.add(mid)
        if is_me(m):
            continue
        try:
            process_message(cl, th, m)
        except Exception as e:
            if (RATE_ERRORS and isinstance(e, RATE_ERRORS)) or (LOGIN_ERRORS and isinstance(e, LOGIN_ERRORS)):
                raise
            log('Error:', e)


def fetch_threads(cl, pending=False, amount=None):
    amount = amount or THREADS_PER_POLL
    if pending:
        if not hasattr(cl, 'direct_pending_inbox'):
            return []
        return cl.direct_pending_inbox(amount=amount)
    kwargs = {'amount': amount}
    if supports(cl.direct_threads, 'thread_message_limit'):
        kwargs['thread_message_limit'] = 10
    return cl.direct_threads(**kwargs)


def poll_once(cl, n):
    if not first_poll['done']:
        for th in fetch_threads(cl, amount=max(THREADS_PER_POLL, 25)):
            handle_thread(cl, th)
        if PROCESS_PENDING:
            try:
                for th in fetch_threads(cl, pending=True, amount=max(THREADS_PER_POLL, 25)):
                    handle_thread(cl, th)
            except Exception as e:
                if (RATE_ERRORS and isinstance(e, RATE_ERRORS)) or (LOGIN_ERRORS and isinstance(e, LOGIN_ERRORS)):
                    raise
                log('Message requests check failed:', e)
        first_poll['done'] = True
        log('Ready: only NEW messages will be answered from now on.')
        return
    for th in fetch_threads(cl):
        handle_thread(cl, th)
    if PROCESS_PENDING and n % PENDING_EVERY == 0:
        try:
            for th in fetch_threads(cl, pending=True):
                handle_thread(cl, th)
        except Exception as e:
            if (RATE_ERRORS and isinstance(e, RATE_ERRORS)) or (LOGIN_ERRORS and isinstance(e, LOGIN_ERRORS)):
                raise
            log('Message requests check failed:', e)


def main():
    cl = ig_login()
    ME['id'] = str(cl.user_id or '')
    try:
        info = cl.account_info()
        ME['id'] = str(info.pk)
        ME['username'] = info.username or IG_USERNAME
    except Exception as e:
        log('Could not read own account info:', e)
        try:
            ME['username'] = cl.username or IG_USERNAME
        except Exception:
            ME['username'] = IG_USERNAME
    if not ME['id'] or ME['id'] == 'None':
        log('WARNING: could not determine bot account id; the bot may answer its own messages.')
    for aname in sorted(ADMIN_NAMES):
        try:
            aid = str(cl.user_id_from_username(aname))
            ADMIN_IDS.add(aid)
            names['@' + aid] = aname
        except Exception as e:
            log(f'Could not resolve admin id of @{aname}, will match by username only:', e)
    for uname in list(cfg['ignored']):
        try:
            iid = str(cl.user_id_from_username(uname))
            IGNORED_IDS[uname] = iid
            names['@' + iid] = uname
        except Exception as e:
            log(f'Could not resolve @{uname} from the ignore list:', e)
    log(f'Bot ready as @{ME["username"]} (admins: {", ".join("@" + a for a in sorted(ADMIN_NAMES))})')
    load_groq_models()
    load_gemini_models()

    n = 0
    errors = 0
    last_dump = time.time()
    while True:
        try:
            poll_once(cl, n)
            errors = 0
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if RATE_ERRORS and isinstance(e, RATE_ERRORS):
                wait = random.uniform(300, 900)
                log(f'Rate limited by Instagram ({e}). Sleeping {int(wait)}s.')
                sleep(wait)
            elif LOGIN_ERRORS and isinstance(e, LOGIN_ERRORS):
                log('Session expired, re-logging in...')
                try:
                    relogin(cl)
                except Exception as e2:
                    log('Re-login failed:', e2)
                    sleep(random.uniform(120, 300))
            elif CHALLENGE_ERRORS and isinstance(e, CHALLENGE_ERRORS):
                log('Instagram asked for a security check. Open the Instagram app with this account, '
                    'confirm "it was me", then the bot will continue. Waiting 15 min.')
                sleep(900)
            else:
                errors += 1
                wait = min(300, 20 * (2 ** min(errors, 4))) + random.uniform(0, 10)
                log(f'Error: {e} (retry in {int(wait)}s)')
                sleep(wait)
        n += 1
        if time.time() - last_dump > 1800:
            dump_session(cl)
            last_dump = time.time()
        wait = random.uniform(POLL_MIN, POLL_MAX)
        if random.random() < LONG_BREAK_CHANCE:
            wait += random.uniform(*LONG_BREAK_RANGE)
        sleep(wait)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log('Stopped.')