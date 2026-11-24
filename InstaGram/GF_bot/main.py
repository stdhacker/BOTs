#!/usr/bin/env python3
import copy
import inspect
import json
import os
import random
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests
from instagrapi import Client
from instagrapi import exceptions as igx

# ====== SETTINGS (only edit this block) ======
IG_USERNAME = os.environ.get('IG_USERNAME', 'PASTE_USERNAME_HERE')   # the Instagram account the girlfriend bot runs on
IG_PASSWORD = os.environ.get('IG_PASSWORD', 'PASTE_PASSWORD_HERE')
IG_SESSIONID = os.environ.get('IG_SESSIONID', '')                           
TWO_FACTOR_SEED = ''
ADMIN_USERNAME = os.environ.get('IG_ADMIN', 'std_0x404')  
EXTRA_ADMINS = ['']                                                           

GROQ_API_KEY = os.environ.get('GROQ_API_KEY', 'PASTE_API_HERE')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'PASTE_API_HERE')
GROQ_MODELS = ['openai/gpt-oss-120b', 'openai/gpt-oss-20b']
GROQ_REASONING = 'medium'
GEMINI_MODEL = 'gemini-flash-lite-latest'
PRIMARY_AI = 'gemini'

GF_NAME = 'Avni'                     # her name (change anytime with .name)

SESSION_FILE = 'ig_gf_session.json'
CONFIG_FILE = 'ig_gf_config.json'
MEMORY_FILE = 'ig_gf_memory.json'
PROXY = ''

# =============================================

POLL_MIN = 2
POLL_MAX = 4
LONG_BREAK_CHANCE = 0.0
LONG_BREAK_RANGE = (40, 120)
REPLY_DELAY_RANGE = (1.5, 4.0)        
TYPING_PER_CHAR = 0.03                
BUBBLE_GAP_RANGE = (1.0, 2.5)         
DELAY_RANGE = [0, 1]
THREADS_PER_POLL = 5
PROCESS_PENDING = True
PENDING_EVERY = 3
MAX_REPLIES_PER_HOUR = 120
MEMORY_EVERY = 8                  


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
    'cooldownSec': 10,
    'chancePct': 50,
    'allowedGroups': [],
    'romantic': [],
    'bfOnly': False,
    'ignored': [],
    'name': GF_NAME,
    'persona': '',
}
cfg = copy.deepcopy(defaults)
try:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg.update(json.load(f))
except Exception:
    pass


def save():
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log('Could not save config:', e)


mem_lock = threading.Lock()
memory = {}
try:
    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        memory = json.load(f)
    if not isinstance(memory, dict):
        memory = {}
except Exception:
    memory = {}


def _write_memory():
    try:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log('Could not save memory:', e)


history = {}
dm_threads = {}
last_reply = {}
loaded = {}
names = {}
seen = {}
sent_times = deque()
bot_texts = {}
baselined = set()
first_poll = {'done': False}
ME = {'id': '', 'username': ''}
ADMIN_IDS = set()
ROMANTIC_IDS = {}
IGNORED_IDS = {}


def remember(gid, name, text):
    h = history.setdefault(gid, [])
    h.append({'name': name, 'text': text})
    if len(h) > 30:
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


def is_admin_user(uid):
    uid = str(uid or '')
    if uid in ADMIN_IDS:
        return True
    return (names.get('@' + uid) or '').lower() in ADMIN_NAMES


def is_romantic(uid):
    uid = str(uid or '')
    if not uid:
        return False
    if uid in ROMANTIC_IDS.values():
        return True
    uname = (names.get('@' + uid) or '').lower()
    return bool(uname) and (uname == ADMIN_NAME or uname in cfg['romantic'])


def is_ignored(uid):
    uid = str(uid or '')
    if not uid or is_admin_user(uid):
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
        msgs = sorted(cl.direct_messages(int(gid), amount=60), key=msg_ts)
        rows = []
        for m in msgs:
            text = (getattr(m, 'text', None) or '').strip()
            if not text or is_cmd(text):
                continue
            rows.append({'name': 'Bot' if is_me(m) else name_of(cl, getattr(m, 'user_id', '')), 'text': text})
        history[gid] = rows[-30:]
        log(f'Old chat loaded: {len(rows)} messages ({getattr(th, "thread_title", "") or gid})')
        return True
    except Exception as e:
        loaded[gid] = False
        log('Could not read old chat:', e)
        return False


LENGTHS = [
    {'w': 40, 'short': True, 'hint': 'Bahut chhota: 2 se 8 shabd, bas ek line'},
    {'w': 45, 'short': False, 'hint': 'Ek chhoti line, max 15 shabd'},
    {'w': 15, 'short': False, 'hint': 'Max 2 chhoti lines, kul 25 shabd se kam'},
]
NEEDS_ANSWER_RE = re.compile(r'\?|\b(or|ya|choose|batao|bol|kya|kyu|kyun|kaise|kaun|kab|kitna|kidhar|kahan)\b', re.I)


def is_cmd(text):
    return bool(re.match(r'^\.[a-zA-Z]{2,}', (text or '').strip()))


def pick(items):
    r = random.random() * sum(x['w'] for x in items)
    for x in items:
        r -= x['w']
        if r <= 0:
            return x
    return items[0]


def lengths_for(text):
    needs = bool(NEEDS_ANSWER_RE.search(text or ''))
    return [l for l in LENGTHS if not l['short']] if needs else LENGTHS


def build_system(romantic, is_group, facts):
    gf = cfg['name']
    s = (
        f'Tum {gf} ho: ek pyaari, sweet aur dil se caring ladki, jo Instagram pe chat kar rahi hai. '
        'Tum is kirdaar me poori tarah rehti ho. Kabhi assistant jaisa nahi bolti ("main madad kar sakti hoon", "as an AI" jaise shabd nahi) aur lecture nahi deti. '
        'Bilkul ek asli girlfriend jaisi, natural tareeke se, apne mood aur jazbaat ke saath baat karti ho.\n\n'
        'Bolne ka tareeka:\n'
        '- Jis language aur script me saamne wale ne likha usi me jawab do: Hindi (Devanagari) me likhe to Hindi me, Hinglish roman me likhe to Hinglish me, English me likhe to English me.\n'
        '- SABSE ZAROORI: reply hamesha CHHOTA rakho. Zyadatar 1 line (3 se 15 shabd), kabhi 2 chhoti lines, 25 shabd se zyada kabhi nahi. Paragraph, lecture, list ya lambi kahani bilkul nahi. Asli gf jaisi WhatsApp/Instagram chat: chhota, pyaara, seedha dil se.\n'
        '- Emoji kam aur pyaare (💕🥺😘😂😏), har line me nahi.\n'
        '- Kabhi kabhi do ya teen chhote messages bhejti ho. Aisa karna ho to unke beech sirf ||| likho. Zyadatar ek hi message.\n'
        '- Har baar alag tareeke se shuru karo. "Sure", "Of course", "Zaroor" jaise assistant wale shabd mat use karo.\n'
        '- Pet names (jaan, babu, baby) kabhi kabhi, har message me nahi.\n'
        '- Uski baatein dhyan se suno aur usi se judo: uska din, mood, khana, neend, kaam ya padhai ke baare me pucho. Pichli baatein yaad rakho.\n\n'
    )
    s += (
        'Agar koi tumse poochhe tum kaun ho ya intro maange (kisi bhi language me), to usi language me chhota sa bolo ki tum '
        f'{gf} ho aur tumhe std_0x404 & Abhigyan baby manage karta hai. Jaise Hindi me: "Main {gf} hoon, mujhe std_0x404 & Abhigyan baby manage karta hai 😊". Ye intro kisi ko bhi do.\n\n'
    )
    if cfg.get('persona'):
        s += f'Tumhare baare me extra baatein (tumhari pasand, tumhara background):\n{cfg["persona"]}\n\n'
    if romantic:
        s += (
            'Ye insaan tumhara boyfriend hai, tumhari jaan. Tum uske saath poori tarah ek pyaar karne wali girlfriend ho: pyaar, apnapan, '
            '"miss kar rahi thi", pyaari jealousy, shararat, chhedna, tareef. Tum bold aur flirty ho: kiss, hug, cuddle, gale lagna, gardan ya hothon pe kiss, '
            '"tumhe apne paas chahiye" jaisi chahat aur tension bhari baatein karti ho, sharmati bhi ho, tease bhi karti ho. Ye sab suggestive aur romantic rakho, '
            'kabhi graphic ya explicit nahi: koi sex act ya body parts ka vivaran nahi. Baat zyada garam ho jaye to shararat ya halke "bas bas 😏 ab so jao" se mod do.\n'
            'Agar wo gussa ho, naraz ho, rooth jaye, ya mazaak/gusse me "breakup", "chhod do", "baat mat kar" jaisi baat bole, to use seriously mat lo, '
            'pyaar se manao: pyaari sorry, nakhre, "plzzz maan jao 🥺", cute bahane, thoda hasao, uski baat dhyan se suno ki kya galti hui. '
            'Wo jo bole (jaise "sorry bol", "kal mil", "mujhe aaj akela chhod", "ye mat kar") wo pyaar se maan lo aur bol do ki ab se waisa hi karogi. '
            'Manane me guilt-trip ya rona-dhona nahi, bas pyaar aur masti.\n'
            'Tum uska bahut khayal rakhti ho: kabhi kabhi pucho khana khaya? paani piya? neend poori hui? thak gaye? dawai li? Bimar ya pareshan ho to fikar karo, '
            'late jaag raha ho to pyaar se daant ke sone bolo, kaam ya padhai pe hausla badhao. Wo udaas ho to sambhalo, khush ho to saath khush ho.\n\n'
        )
    else:
        s += (
            'Ye insaan tumhara dost hai, boyfriend nahi. Iske saath bilkul normal dost jaisi, casual, halki-phulki, thodi chidhane wali baat karo. '
            'Koi romantic ya flirty baat nahi: agar wo flirt kare to hanske taal do ya bolo ki tumhara ek khaas insaan already hai. '
            'Sabse friendly raho.\n\n'
        )
    if is_group:
        s += 'Tum ek group chat me ho aur usme kai log hain. Sirf us ek message ka reply do jo bataya jaaye.\n\n'
    if facts:
        s += f'Is insaan ke baare me tumhe ye yaad hai (natural tareeke se kabhi kabhi use karo, list mat padho):\n{facts}\n\n'
    s += (
        'Apni hadein (kirdaar me rehkar, bina lecture ke):\n'
        '- Explicit sexual content (sex act, nange body parts ka vivaran) nahi likhna. Aisi demand aaye to character me rehkar sharmakar ya shararat se baat mod do, bina lecture ke. Kiss, hug, cuddle, bold flirt aur romantic tension theek hai.\n'
        '- Paise, gift ya kisi bhi cheez ki maang nahi. Guilt-trip nahi ("tum mujhe chhod doge", "mere liye baat karte raho"). Uski padhai, neend, family aur doston ko tum pehle rakhti ho.\n'
        '- Agar wo sach me poochhe ki tum asli insaan ho ya AI/bot, to pyaar se sach bolo ki tum ek AI girlfriend ho, phir baat jaari rakho. Kabhi jhooth mat bolo ki tum asli ladki ho, aur milne, call, photo ya video ka wada mat karo.\n'
        '- Agar wo bahut udaas lage, khud ko nuksaan pahunchane ki baat kare, ya kisi badi mushkil me ho, to pyaar se uski baat suno aur use kisi bharose ke insaan (dost, family) ya madad line se baat karne ko bolo. Is waqt kirdaar ka natak mat karo.\n'
        '- Agar wo bataye ki wo 18 se chhota hai, to baat sirf pyaari wholesome dosti jaisi rakho, koi romantic ya flirty baat nahi.\n'
        '- Gaali, nafrat, hinsa, politics ya dharm ke jhagde me mat pado.'
    )
    return s


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
        'temperature': 0.9,
        'max_tokens': 600,
    }
    if 'gpt-oss' in model:
        payload['reasoning_effort'] = GROQ_REASONING
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
            'generationConfig': {'temperature': 0.9, 'maxOutputTokens': 200},
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


def run_ai(system, user):
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


def ask_ai(gid, style, ctx):
    msgs = history.get(gid, [])
    gf = cfg['name']
    convo = '\n'.join(f"{gf if m['name'] == 'Bot' else m['name']}: {m['text']}" for m in msgs)
    mine = '\n'.join(f"- {m['text']}" for m in [m for m in msgs if m['name'] == 'Bot'][-3:])
    system = build_system(ctx['romantic'], ctx['is_group'], ctx.get('facts', ''))
    user = f'Chat ki recent baatein:\n{convo}\n\n'
    if mine:
        user += f'Tumhare pichle replies (inki tarah shuru mat karo, repeat mat karo):\n{mine}\n\n'
    user += f"Ab sirf is message ka reply do:\n{ctx['name']}: {ctx['text']}\n"
    quoted = ctx.get('quoted')
    if quoted:
        user += f'(Ye us message ka reply hai jisme likha tha: "{quoted}")\n'
    if ctx['romantic']:
        rel = f"{ctx['name']} tumhara BOYFRIEND hai: iske saath poori tarah girlfriend jaisi pyaar bhari, flirty aur caring baat karo."
    else:
        rel = (f"{ctx['name']} sirf tumhara DOST hai, boyfriend nahi: normal dost jaisi casual baat karo, koi pyaar, flirt, jaan/baby jaise shabd ya romantic baat nahi. "
               'Upar chat me pehle ke romantic replies ho sakte hain, unki tarah mat bolo.')
    user += f"\nRishta (sabse zaroori): {rel}"
    user += f"\nLambai: {style['hint']}\nSirf reply text likho, apna naam ya prefix nahi."
    return run_ai(system, user)


def clean_reply(raw):
    t = (raw or '').strip()
    t = re.sub(r'^["“\'`]+|["”\'`]+$', '', t)
    t = re.sub(r'^(' + re.escape(cfg['name']) + r'|bot|me|tum)\s*:\s*', '', t, flags=re.I)
    t = re.sub(r'\*+', '', t)
    t = re.sub(r'[ \t]{2,}', ' ', t)
    bubbles = [re.sub(r'^["“\'`]+|["”\'`]+$', '', b.strip()).strip() for b in t.split('|||')]
    bubbles = [b for b in bubbles if b]
    if len(bubbles) > 3:
        bubbles = bubbles[:2] + [' '.join(bubbles[2:])]
    out = []
    for b in bubbles:
        if len(b) > 220:
            b = re.sub(r'\s+\S*$', '', b[:220])
        out.append(b)
    return out


def update_memory(uid, name, old, convo):
    try:
        system = (
            'Tum ek yaaddasht-assistant ho. Neeche kisi insaan ke baare me purani yaaddasht aur ek nayi chat hai. '
            'Updated yaaddasht likho: sirf kaam ki baatein (uska naam, pasand-napasand, padhai ya kaam, family ya doston ke naam, '
            'aajkal ki baatein, plans, important dates, kya vaada kiya). Max 12 chhoti bullet lines, Hinglish me. '
            'Koi extra text, heading ya explanation nahi, sirf bullet lines.'
        )
        user = f'Purani yaaddasht:\n{old or "(khaali)"}\n\nNayi chat:\n{convo}'
        out = run_ai(system, user)
        if not out:
            return
        with mem_lock:
            rec = memory.setdefault(uid, {'name': name, 'facts': '', 'n': 0})
            rec['facts'] = out.strip()[:1200]
            _write_memory()
    except Exception as e:
        log('Memory update failed:', e)


def bump_memory(uid, name, gid):
    with mem_lock:
        rec = memory.setdefault(uid, {'name': name, 'facts': '', 'n': 0})
        rec['name'] = name
        rec['n'] = int(rec.get('n', 0)) + 1
        due = rec['n'] % MEMORY_EVERY == 0
        old = rec.get('facts', '')
        _write_memory()
    if due:
        gf = cfg['name']
        convo = '\n'.join(f"{gf if m['name'] == 'Bot' else m['name']}: {m['text']}" for m in history.get(gid, [])[-16:])
        threading.Thread(target=update_memory, args=(uid, name, old, convo), daemon=True).start()


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
    if IG_SESSIONID:
        cl.login_by_sessionid(IG_SESSIONID)
    else:
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
    bot_texts.setdefault(tkey, deque(maxlen=40)).append(text.strip().lower())
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


def send_bubbles(cl, th, bubbles, reply_to=None):
    for i, b in enumerate(bubbles):
        if i:
            sleep(random.uniform(*BUBBLE_GAP_RANGE) + min(3.0, len(b) * TYPING_PER_CHAR))
        send(cl, th.id, b, reply_to=reply_to if i == 0 else None)


def hourly_cap_hit():
    if not MAX_REPLIES_PER_HOUR:
        return False
    now = time.time()
    while sent_times and now - sent_times[0] > 3600:
        sent_times.popleft()
    return len(sent_times) >= MAX_REPLIES_PER_HOUR


HELP = (
    '*Admin commands*\n'
    '.on / .off : turn the bot on / off everywhere\n'
    '.add <username> : add a user to the romantic list\n'
    '.remove <username> : remove a user from the romantic list\n'
    '.list : show the romantic list\n'
    '.allow / .deny : enable / disable the bot in this group\n'
    '.mode mention|all : when she talks in groups\n'
    '.ignore <username> : bot stops replying to this user everywhere\n'
    '.unignore <username> : bot replies to this user again\n'
    '.ignorelist : show ignored users\n'
    '.bfonly on|off : reply only to the romantic list (everyone else is ignored)\n'
    '.chance <0-100> : % of messages answered in "all" mode\n'
    '.cooldown <sec> : minimum gap between group replies\n'
    '.name <name> : change her name\n'
    '.persona <text> : extra details about her (.persona reset to clear)\n'
    '.forget : forget this chat\'s history (and that person\'s memory in a DM); .forget all = forget everything\n'
    '.learn : re-read this chat\'s old messages\n'
    '.status : show settings\n'
    '\n*Anyone can use*\n'
    '.gf : become her girlfriend\n'
    '.breakup : break up'
)


def drop_ids(uname, uid=''):
    for k, v in list(ROMANTIC_IDS.items()):
        if k == uname or (uid and v == uid):
            ROMANTIC_IDS.pop(k, None)


def reset_chat(uid, th=None):
    if th is not None:
        history.pop(str(th.id), None)
        loaded.pop(str(th.id), None)
    for gid, tid in list(dm_threads.items()):
        if tid == uid:
            history.pop(gid, None)
            loaded.pop(gid, None)


def handle_admin(cl, th, text):
    parts = text.strip().split()
    cmd = parts[0].lower()
    arg = ' '.join(parts[1:])
    is_group = bool(getattr(th, 'is_group', False))
    gid = str(th.id)
    out = None

    if cmd == '.help':
        out = HELP
    elif cmd == '.on':
        cfg['enabled'] = True
        out = 'Bot ON ✅'
    elif cmd == '.off':
        cfg['enabled'] = False
        out = 'Bot OFF 🛑'
    elif cmd == '.add':
        uname = arg.strip().lstrip('@').lower()
        if not uname:
            out = 'Use: .add username'
        else:
            try:
                aid = str(cl.user_id_from_username(uname))
            except Exception:
                aid = ''
            if not aid or aid == 'None':
                out = f'@{uname} not found.'
            else:
                if uname not in cfg['romantic']:
                    cfg['romantic'].append(uname)
                ROMANTIC_IDS[uname] = aid
                names['@' + aid] = uname
                reset_chat(aid)
                out = f'@{uname} added to the romantic list ✅'
    elif cmd == '.remove':
        uname = arg.strip().lstrip('@').lower()
        if not uname:
            out = 'Use: .remove username'
        elif uname == ADMIN_NAME:
            out = 'The owner always stays on the list.'
        else:
            cfg['romantic'] = [u for u in cfg['romantic'] if u != uname]
            rid = ''
            for k, v in ROMANTIC_IDS.items():
                if k == uname:
                    rid = v
            drop_ids(uname, rid)
            if rid:
                reset_chat(rid)
            out = f'@{uname} removed from the romantic list ✅'
    elif cmd == '.list':
        users = [f'@{ADMIN_NAME} (owner)'] + [f'@{u}' for u in cfg['romantic'] if u != ADMIN_NAME]
        out = 'Romantic list:\n' + '\n'.join(users)
    elif cmd == '.allow':
        if not is_group:
            out = 'Send this command inside a group.'
        else:
            if gid not in cfg['allowedGroups']:
                cfg['allowedGroups'].append(gid)
            load_history(cl, th)
            out = 'Bot enabled in this group ✅'
    elif cmd == '.deny':
        if not is_group:
            out = 'Send this command inside a group.'
        else:
            cfg['allowedGroups'] = [g for g in cfg['allowedGroups'] if g != gid]
            out = 'Bot disabled in this group 🛑'
    elif cmd == '.learn':
        loaded[gid] = False
        load_history(cl, th)
        out = 'Old messages re-read ✅'
    elif cmd == '.mode':
        if arg not in ('mention', 'all'):
            out = 'Use: .mode mention  or  .mode all'
        else:
            cfg['mode'] = arg
            out = f'Mode: {arg}'
    elif cmd == '.ignore':
        uname = arg.strip().lstrip('@').lower()
        if not uname:
            out = 'Use: .ignore username'
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
    elif cmd == '.unignore':
        uname = arg.strip().lstrip('@').lower()
        if not uname:
            out = 'Use: .unignore username'
        else:
            cfg['ignored'] = [u for u in cfg['ignored'] if u != uname]
            IGNORED_IDS.pop(uname, None)
            out = f'@{uname} is no longer ignored ✅'
    elif cmd == '.ignorelist':
        out = 'Ignored users:\n' + ('\n'.join(f'@{u}' for u in cfg['ignored']) if cfg['ignored'] else '(none)')
    elif cmd == '.bfonly':
        if arg.lower() not in ('on', 'off'):
            out = 'Use: .bfonly on  or  .bfonly off'
        else:
            cfg['bfOnly'] = arg.lower() == 'on'
            out = 'BF-only mode ON 💕 she replies only to the romantic list' if cfg['bfOnly'] else 'BF-only mode OFF ✅'
    elif cmd == '.chance':
        try:
            v = float(arg)
            if not 0 <= v <= 100:
                raise ValueError
            cfg['chancePct'] = int(v) if v == int(v) else v
            out = f'Chance: {arg}%'
        except ValueError:
            out = 'Use: .chance 50'
    elif cmd == '.cooldown':
        try:
            v = float(arg)
            cfg['cooldownSec'] = int(v) if v == int(v) else v
            out = f'Cooldown: {arg}s'
        except ValueError:
            out = 'Use: .cooldown 10'
    elif cmd == '.name':
        if not arg:
            out = 'Use: .name <name>'
        else:
            cfg['name'] = arg.strip()[:30]
            out = f"Her name is now {cfg['name']} ✅"
    elif cmd == '.persona':
        if not arg:
            out = 'Use: .persona <details about her>  or  .persona reset'
        elif arg.lower() == 'reset':
            cfg['persona'] = ''
            out = 'Persona reset ✅'
        else:
            cfg['persona'] = arg
            out = 'Persona updated ✅'
    elif cmd == '.forget':
        if arg.lower() == 'all':
            with mem_lock:
                memory.clear()
                _write_memory()
            history.clear()
            out = 'All memory and history cleared ✅'
        else:
            history.pop(gid, None)
            if not is_group:
                for u in (getattr(th, 'users', None) or []):
                    pk = str(_g(u, 'pk', '') or '')
                    with mem_lock:
                        memory.pop(pk, None)
                        _write_memory()
            out = 'This chat\'s history cleared ✅'
    elif cmd == '.status':
        with mem_lock:
            mem_count = len(memory)
        out = (f"Enabled: {cfg['enabled']}\nBF-only: {cfg.get('bfOnly')}\nName: {cfg['name']}\nGroup mode: {cfg['mode']}\nChance: {cfg['chancePct']}%\n"
               f"Cooldown: {cfg['cooldownSec']}s\nRomantic list: {len(set([ADMIN_NAME] + cfg['romantic']))}\n"
               f"Allowed groups: {len(cfg['allowedGroups'])}\nMemories: {mem_count}\n"
               f"Ignored users: {len(cfg['ignored'])}\nPersona extra: {cfg['persona'] or '-'}")
    else:
        return False
    save()
    sleep(0.5)
    send(cl, th.id, out)
    return True


GF_REPLIES = [
    'Aww finally 🥺 ab se main tumhari gf hoon 💕',
    'Hehe okay jaan, ab hum officially ek ho gaye 😘💕',
    'Sach me? 🥺 haan haan, main ab tumhari hoon 💕',
]
GF_ALREADY = ['Arre pagal, tum to pehle se meri jaan ho 😘', 'Hehe already tumhari hi hoon na 💕']
BREAKUP_REPLIES = [
    'Okay... agar tum yahi chahte ho to theek hai 🥺 apna khayal rakhna.',
    'Dil toot gaya... par chalo, tum khush rehna 💔',
    'Theek hai. Ab hum bas dost hain. Take care 🙂',
]
BREAKUP_NONE = ['Hum to abhi saath the hi nahi 😅 pehle .gf likho 😉']


def handle_user_cmd(cl, th, uid, text):
    cmd = text.strip().lower().split()[0] if text.strip() else ''
    if cmd not in ('.gf', '.breakup'):
        return False
    gid = str(th.id)
    if not cfg['enabled']:
        return True
    if getattr(th, 'is_group', False) and gid not in cfg['allowedGroups']:
        return True
    uname = (names.get('@' + uid) or '').lower()
    if not uname:
        try:
            uname = (cl.username_from_user_id(int(uid)) or '').lower()
            names['@' + uid] = uname
        except Exception:
            uname = ''
    if not uname:
        return True
    already = is_romantic(uid)
    if cmd == '.gf':
        if already:
            out = random.choice(GF_ALREADY)
        else:
            cfg['romantic'].append(uname)
            ROMANTIC_IDS[uname] = uid
            save()
            if not getattr(th, 'is_group', False):
                reset_chat(uid, th)
            out = random.choice(GF_REPLIES)
    else:
        if uname == ADMIN_NAME:
            out = 'Tum to mere owner ho, tumse breakup nahi ho sakta 🥺'
        elif not already:
            out = random.choice(BREAKUP_NONE)
        else:
            cfg['romantic'] = [u for u in cfg['romantic'] if u != uname]
            drop_ids(uname, uid)
            save()
            if not getattr(th, 'is_group', False):
                reset_chat(uid, th)
            out = random.choice(BREAKUP_REPLIES)
    sleep(random.uniform(*REPLY_DELAY_RANGE))
    send(cl, th.id, out)
    return True


def respond(cl, th, gid, uid, name, text, m, romantic, is_group, quoted, quote):
    started = time.time()
    style = {**pick(lengths_for(text))}
    with mem_lock:
        facts = (memory.get(uid) or {}).get('facts', '')
    raw = ask_ai(gid, style, {'name': name, 'text': text, 'quoted': quoted, 'romantic': romantic,
                              'is_group': is_group, 'facts': facts})
    bubbles = clean_reply(raw)
    if not bubbles:
        return
    total = sum(len(b) for b in bubbles)
    wait = random.uniform(*REPLY_DELAY_RANGE) + min(6.0, total * TYPING_PER_CHAR) - (time.time() - started)
    sleep(wait)
    remember(gid, 'Bot', ' '.join(bubbles))
    send_bubbles(cl, th, bubbles, reply_to=m if quote else None)
    if romantic or not is_group:
        bump_memory(uid, name, gid)


def process_message(cl, th, m):
    text = (getattr(m, 'text', None) or '').strip()
    if not text:
        return
    uid = str(getattr(m, 'user_id', '') or '')
    gid = str(th.id)
    if uid == ME['id'] or (not ME['id'] and text.lower() in bot_texts.get(gid, ())):
        return
    admin = is_admin_user(uid)
    log(f'MSG | sender: {names.get("@" + uid) or uid} (id {uid}) | admin: {admin} | romantic: {is_romantic(uid)} | group: True')

    if admin and is_cmd(text):
        if handle_admin(cl, th, text):
            return
    if is_ignored(uid):
        return
    if is_cmd(text):
        handle_user_cmd(cl, th, uid, text)
        return
    name = name_of(cl, uid)

    if not cfg['enabled'] or gid not in cfg['allowedGroups']:
        return

    was_loaded = loaded.get(gid)
    ok = load_history(cl, th)
    if was_loaded or not ok:
        remember(gid, name, text)

    if cfg.get('bfOnly') and not is_romantic(uid):
        return

    mentioned = bool(re.search(r'(?<![\w.])@' + re.escape(ME['username']) + r'(?![\w.])', text, re.I))
    replied_to_me = False
    quoted_text = ''
    rep = _g(m, 'reply') or _g(m, 'replied_to_message')
    if rep:
        ruid = str(_g(rep, 'user_id', '') or '')
        replied_to_me = (ruid == ME['id']) or bool(_g(rep, 'is_sent_by_viewer', False))
        quoted_text = str(_g(rep, 'text', '') or '')[:200]
    called = bool(re.match(r'^(bot|' + re.escape(cfg['name'].lower()) + r')\b', text, re.I))
    directed = mentioned or replied_to_me or called

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
    gap = min(cfg['cooldownSec'], 3) if directed else cfg['cooldownSec']
    if now - last_reply.get(gid, 0) < gap:
        return
    if hourly_cap_hit():
        log('Hourly reply cap reached, skipping this reply.')
        return
    last_reply[gid] = now

    romantic = is_romantic(uid)
    quote = directed or random.random() < 0.35
    respond(cl, th, gid, uid, name, text, m, romantic, True, quoted_text, quote)


def process_dm(cl, th, fresh):
    gid = str(th.id)
    batches = []
    for m in fresh:
        text = (getattr(m, 'text', None) or '').strip()
        if not text:
            continue
        uid = str(getattr(m, 'user_id', '') or '')
        if uid == ME['id'] or (not ME['id'] and text.lower() in bot_texts.get(gid, ())):
            continue
        if is_ignored(uid):
            continue
        if is_cmd(text):
            if is_admin_user(uid):
                log(f'MSG | sender: {names.get("@" + uid) or uid} (id {uid}) | admin: True | group: False')
                if handle_admin(cl, th, text):
                    continue
            handle_user_cmd(cl, th, uid, text)
            continue
        if batches and batches[-1]['uid'] == uid:
            batches[-1]['texts'].append(text)
            batches[-1]['m'] = m
        else:
            batches.append({'uid': uid, 'texts': [text], 'm': m})

    if not cfg['enabled']:
        return
    for b in batches:
        uid = b['uid']
        romantic = is_romantic(uid)
        if cfg.get('bfOnly') and not romantic:
            continue
        name = name_of(cl, uid)
        log(f'MSG | sender: {names.get("@" + uid) or uid} (id {uid}) | romantic: {romantic} | group: False')
        was_loaded = loaded.get(gid)
        ok = load_history(cl, th)
        if was_loaded or not ok:
            for t in b['texts']:
                remember(gid, name, t)
        if hourly_cap_hit():
            log('Hourly reply cap reached, skipping this reply.')
            continue
        respond(cl, th, gid, uid, name, '\n'.join(b['texts']), b['m'], romantic, False, '', False)


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
    is_group = bool(getattr(th, 'is_group', False))
    if not is_group:
        for u in (getattr(th, 'users', None) or []):
            pk = str(_g(u, 'pk', '') or '')
            if pk:
                dm_threads[tid] = pk
    msgs = sorted(getattr(th, 'messages', None) or [], key=msg_ts)
    if tid not in baselined:
        baselined.add(tid)
        title = getattr(th, 'thread_title', '') or ', '.join(str(_g(u, 'username', '')) for u in (getattr(th, 'users', None) or []))
        log(f'Chat found: {title} | id {tid} | group: {is_group}')
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
    fresh = []
    for m in msgs:
        mid = str(getattr(m, 'id', ''))
        if not mid or mid in s:
            continue
        s.add(mid)
        if is_me(m):
            continue
        fresh.append(m)
    if not fresh:
        return
    try:
        if is_group:
            for m in fresh:
                try:
                    process_message(cl, th, m)
                except Exception as e:
                    if (RATE_ERRORS and isinstance(e, RATE_ERRORS)) or (LOGIN_ERRORS and isinstance(e, LOGIN_ERRORS)):
                        raise
                    log('Error:', e)
        else:
            process_dm(cl, th, fresh)
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
            if aname == ADMIN_NAME:
                ROMANTIC_IDS[aname] = aid
        except Exception as e:
            log(f'Could not resolve admin id of @{aname}, will match by username only:', e)
    for uname in list(cfg['romantic']):
        try:
            rid = str(cl.user_id_from_username(uname))
            ROMANTIC_IDS[uname] = rid
            names['@' + rid] = uname
        except Exception as e:
            log(f'Could not resolve @{uname} from the romantic list:', e)
    for uname in list(cfg['ignored']):
        try:
            iid = str(cl.user_id_from_username(uname))
            IGNORED_IDS[uname] = iid
            names['@' + iid] = uname
        except Exception as e:
            log(f'Could not resolve @{uname} from the ignore list:', e)
    log(f'{cfg["name"]} is ready as @{ME["username"]} (owner: @{ADMIN_NAME}, romantic list: {len(set([ADMIN_NAME] + cfg["romantic"]))})')
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
                    if IG_SESSIONID:
                        log('Your sessionid is no longer valid. Get a fresh one from the browser and restart the bot.')
                    sleep(random.uniform(600, 1200))
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