# -*- coding: utf-8 -*-
"""
Rasp Bot v2.2 FIXED (PTB 20.6)
--------------------------------
✔ Исправлено: все сообщения используют реальные переводы строк (\n), а не текстовые \n
✔ Убрана логика «избранного класса»
✔ Добавлена кнопка «↩️ Назад к выбору даты» после показа расписания
✔ Сохранены: Яндекс.Диск, автообновление при старте, тихие часы, статистика, админ-команды
"""

from __future__ import annotations
import os
import re
import json
import copy
import asyncio
import logging
from io import BytesIO
from datetime import datetime, timedelta, time, date
from typing import Dict, List, Tuple, Any

import requests
import openpyxl
import locale
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, ApplicationBuilder, ContextTypes,
    CommandHandler, ConversationHandler, CallbackQueryHandler,
    MessageHandler, filters
)

# ---------- Локаль ----------
try:
    locale.setlocale(locale.LC_TIME, 'ru_RU')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
    except locale.Error:
        pass

# ---------- Состояния ----------
SELECT_CLASS_OR_DATE, SELECT_PARALLEL, SELECT_CLASS, SELECT_DATE = range(4)

# ---------- ENV ----------
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YANDEX_OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN")
YANDEX_API_URL = "https://cloud-api.yandex.net/v1/disk/resources"
YANDEX_FOLDER_PATH = os.getenv("YANDEX_FOLDER_PATH", "rasp")

try:
    ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
except (TypeError, ValueError):
    ADMIN_ID = 0

QUIET_START_HOUR = int(os.getenv("QUIET_START_HOUR", "23") or "23")
QUIET_END_HOUR = int(os.getenv("QUIET_END_HOUR", "7") or "7")

HOLIDAYS_START = os.getenv("HOLIDAYS_START", "27.10.2025")
HOLIDAYS_END = os.getenv("HOLIDAYS_END", "04.11.2025")

# ---------- Пути ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_FILE = os.path.join(BASE_DIR, "users.json")
OVERRIDES_FILE = os.path.join(BASE_DIR, "overrides.json")
CHANGES_LOG_FILE = os.path.join(BASE_DIR, "schedule_changes.json")
PENDING_FILE = os.path.join(BASE_DIR, "pending_notifications.json")
STATS_FILE = os.path.join(BASE_DIR, "stats.json")

# ---------- Глобальные ----------
schedule_data: Dict[str, Dict[str, Any]] = {}
file_hashes: Dict[str, str] = {}
user_stats: Dict[str, Any] = {}
first_schedule_check_done = False

user_data_cache: Dict[str, Any] = {}  # для статистики (без избранного класса)
override_data: Dict[str, Any] = {}

_cached_all_classes: List[str] | None = None
_cached_parallels: Dict[str, List[str]] | None = None

USER_CACHE_INTERVAL = 600
DATES_PER_PAGE = 7
LAST_ADMIN_ERROR_TIME = 0

# ---------- Логирование ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger("rasp-bot-v2.2-fixed")

# ---------- Эмодзи ----------
SUBJECT_EMOJIS = {
    "математика": "➗", "алгебра": "➗", "геометрия": "📐",
    "физика": "🧪", "химия": "⚗️", "русский": "📝",
    "литература": "📚", "английский": "🇬🇧", "немецкий": "🇩🇪",
    "история": "🏛️", "обществознание": "👥", "география": "🌍",
    "биология": "🧬", "информатика": "💻", "труд": "🛠️", "физкультура": "🏃",
}

# ---------- Утилиты ----------
def escape_md(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    return (text.replace('_', '\\_').replace('*', '\\*')
                .replace('[', '\\[').replace(']', '\\]')
                .replace('(', '\\(').replace(')', '\\)')
                .replace('`', '\\`'))

DAY_MAPPING = {0:'понедельник',1:'вторник',2:'среда',3:'четверг',4:'пятница',5:'суббота',6:'воскресенье'}

def get_day_name_from_date_str(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%d.%m.%Y")
        return DAY_MAPPING.get(d.weekday(), "неизвестный день")
    except ValueError:
        return "неизвестный день"

def get_today_date_str(offset: int = 0) -> str:
    today = datetime.now() + timedelta(days=offset)
    # воскресенье пропускаем
    if today.weekday() == 6:
        today += timedelta(days=1)
    return today.strftime("%d.%m.%Y")

# ---------- IO JSON ----------
def load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                c = f.read().strip()
                return json.loads(c) if c else default
    except Exception as e:
        logger.warning(f"Не удалось прочитать {path}: {e}")
    return default

def save_json(path: str, data) -> None:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка записи {path}: {e}")

# ---------- Users / Stats ----------
def load_user_data_from_disk():
    global user_data_cache
    user_data_cache = load_json(USER_DATA_FILE, {})

def get_user_data() -> Dict[str, Any]:
    if not user_data_cache:
        load_user_data_from_disk()
    return user_data_cache

def save_user_data_job(_: ContextTypes.DEFAULT_TYPE):
    if user_data_cache is not None:
        save_json(USER_DATA_FILE, user_data_cache)

def save_user_id(user_id: int, username: str | None, full_name: str | None):
    users = get_user_data()
    k = str(user_id)
    now = datetime.now().isoformat()
    if k not in users:
        users[k] = {'first_seen': now, 'last_seen': now}
    users[k]['last_seen'] = now
    users[k]['username'] = username or ''
    users[k]['full_name'] = full_name or ''

def update_user_stats(user_id: int, action: str):
    k = str(user_id)
    if k not in user_stats:
        user_stats[k] = {'first_seen': datetime.now().isoformat(), 'last_seen': datetime.now().isoformat(), 'actions': {}, 'total_requests': 0}
    user_stats[k]['last_seen'] = datetime.now().isoformat()
    user_stats[k]['total_requests'] += 1
    user_stats[k]['actions'][action] = user_stats[k]['actions'].get(action, 0) + 1

def record_daily_stats():
    today = date.today().strftime('%Y-%m-%d')
    total = sum(int(u.get('total_requests', 0)) for u in user_stats.values())
    stats = load_json(STATS_FILE, {})
    stats[today] = { 'total_requests': total }
    save_json(STATS_FILE, stats)

# ---------- Overrides ----------
def load_overrides():
    global override_data
    override_data = load_json(OVERRIDES_FILE, {})

def save_overrides():
    save_json(OVERRIDES_FILE, override_data)

def apply_overrides(date_str: str, class_name: str, raw_schedule: Dict[str, List[Dict[str, str]]]):
    schedule = copy.deepcopy(raw_schedule)
    key = f"{date_str}_{class_name}"
    for o in override_data.get(key, []):
        day = o.get('day'); ln = int(o.get('lesson_num', 0)); val = o.get('new_value', '')
        if day in schedule and 1 <= ln <= len(schedule[day]):
            i = ln - 1; cur = schedule[day][i]
            if val.startswith('room:'):
                cur['room'] = val[5:].strip()
            elif val.startswith('teacher:'):
                cur['teacher'] = val[8:].strip()
            elif val.startswith('subject:'):
                cur['subject'] = val[8:].strip(); cur['room'] = ''; cur['teacher'] = ''
            schedule[day][i] = cur
    return schedule

def check_override(date_str: str, class_name: str, day_name_lower: str, lesson_num: int) -> bool:
    key = f"{date_str}_{class_name}"
    for o in override_data.get(key, []):
        try:
            if o.get('day') == day_name_lower and int(o.get('lesson_num')) == lesson_num:
                return True
        except Exception:
            pass
    return False

# ---------- Яндекс.Диск ----------
def yheaders():
    return { 'Authorization': f'OAuth {YANDEX_OAUTH_TOKEN}', 'Accept': 'application/json' }

def get_yandex_disk_files() -> List[Dict[str, str]]:
    params = {'path': YANDEX_FOLDER_PATH, 'limit': 100}
    r = requests.get(YANDEX_API_URL, headers=yheaders(), params=params, timeout=15)
    r.raise_for_status()
    data = r.json(); files = []
    if '_embedded' in data and 'items' in data['_embedded']:
        for it in data['_embedded']['items']:
            if it.get('type') == 'file' and it.get('name', '').endswith('.xlsx'):
                dl = requests.get(f"{YANDEX_API_URL}/download", headers=yheaders(), params={'path': it['path']}, timeout=15)
                if dl.status_code == 200:
                    files.append({'name': it['name'], 'url': dl.json().get('href'), 'yandex_hash': it.get('md5',''), 'file_path': it['path']})
    return files

def download_file_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        logger.error(f"Ошибка загрузки файла: {e}")
    return None

def extract_date_from_filename(filename: str) -> str | None:
    if filename.endswith('.xlsx'):
        filename = filename[:-5]
    fmts = ["%d.%m.%Y","%d.%m.%y","%Y.%m.%d","%d-%m-%Y","%Y-%m-%d","%Y%m%d"]
    for fmt in fmts:
        try:
            d = datetime.strptime(filename, fmt)
            return d.strftime('%d.%m.%Y')
        except ValueError:
            continue
    m = re.search(r'(\d{1,2}\.\d{1,2})', filename)
    if m:
        try:
            d = datetime.strptime(m.group(1) + f".{datetime.now().year}", '%d.%m.%Y')
            return d.strftime('%d.%m.%Y')
        except ValueError:
            pass
    return None

# ---------- Парсинг XLSX ----------
def is_valid_lesson_cell(subject: Any) -> bool:
    if subject is None:
        return False
    s = str(subject).strip()
    if not s or s.lower() in ('none','nan'):
        return False
    if any(d in s.lower() for d in ['понедельник','вторник','среда','четверг','пятница','суббота']):
        return False
    if len(s.split()) >= 3 and re.match(r'^[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.$', s):
        return False
    return True

def parse_schedule_row(row: Tuple[Any,...], next_rows: List[Tuple[Any,...]], day_col: int) -> List[Dict[str,str]]:
    dp = []
    dp.append({'col_subj': row[day_col], 'col_room': row[day_col+1] if day_col+1 < len(row) else None})
    for nr in next_rows:
        nrl = tuple(c if c is not None else '' for c in nr)
        dp.append({'col_subj': nrl[day_col] if day_col < len(nrl) else None, 'col_room': nrl[day_col+1] if day_col+1 < len(nrl) else None})
    lessons: List[Dict[str,str]] = []
    i = 0
    while i < len(dp):
        sub = dp[i]['col_subj']
        if is_valid_lesson_cell(sub):
            subj = str(sub).strip(); room1 = str(dp[i]['col_room']).strip() if dp[i]['col_room'] else ''
            lessons.append({'subject': subj, 'room': room1, 'teacher': ''}); idx = len(lessons)-1
            if i+1 < len(dp):
                t1 = dp[i+1]['col_subj']
                if t1 and not is_valid_lesson_cell(t1):
                    lessons[idx]['teacher'] = str(t1).strip()
                    room2 = str(dp[i+1]['col_room']).strip() if dp[i+1]['col_room'] else ''
                    if i+2 < len(dp):
                        sub2 = dp[i+2]['col_subj']
                        if is_valid_lesson_cell(sub2):
                            lessons.append({'subject': str(sub2).strip(), 'room': room2, 'teacher': ''})
                            if i+3 < len(dp):
                                t2 = dp[i+3]['col_subj']
                                if t2 and not is_valid_lesson_cell(t2):
                                    lessons[-1]['teacher'] = str(t2).strip(); i += 4; continue
                            i += 3; continue
                    i += 2; continue
            i += 1; continue
        i += 1
    return [l for l in lessons if l['subject'].strip()]

def sync_load_schedule_files() -> Tuple[List[Tuple[str,str]], str | None]:
    global schedule_data, file_hashes, _cached_all_classes, _cached_parallels
    changed: List[Tuple[str,str]] = []
    old = schedule_data
    new: Dict[str, Any] = {}

    if not YANDEX_OAUTH_TOKEN:
        return changed, "Не задан YANDEX_OAUTH_TOKEN"

    try:
        files = get_yandex_disk_files()
    except Exception as e:
        logger.error(f"Ошибка доступа к Я.Диску: {e}")
        return changed, f"Ошибка Яндекс.Диск: {e}"

    if not files:
        return changed, "Файлы на Яндекс.Диске не найдены"

    new_hashes: Dict[str, str] = {}
    for fi in files:
        name = fi['name']; url = fi['url']; ymd5 = fi.get('yandex_hash','')
        if name in file_hashes and file_hashes[name] == ymd5:
            ds_old = extract_date_from_filename(name)
            if ds_old and ds_old in old:
                new[ds_old] = old[ds_old]
            new_hashes[name] = ymd5; continue
        logger.info(f"Файл изменился: {name} — загружаю и парсю...")
        content = download_file_bytes(url)
        if not content:
            logger.warning(f"Не удалось скачать {name}"); continue
        new_hashes[name] = ymd5
        ds = extract_date_from_filename(name)
        if not ds:
            logger.warning(f"Не распознана дата из имени: {name}"); continue
        try:
            wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
            sheet = wb.active
            new_classes: Dict[str, Dict[str, List[Dict[str,str]]]] = {}
            current_class = None
            day_columns: Dict[str,int] = {}
            all_rows = list(sheet.iter_rows(values_only=True))
            for row_idx, row in enumerate(all_rows):
                if not any(row):
                    continue
                row = tuple(c if c is not None else '' for c in row)
                if row[0] == '#' and 'Время' in str(row[1]):
                    day_columns = {}
                    for col_idx, cell in enumerate(row):
                        cl = str(cell).strip().lower()
                        for d in ['понедельник','вторник','среда','четверг','пятница','суббота']:
                            if d in cl:
                                day_columns[d] = col_idx; break
                elif 'Класс -' in str(row[0]):
                    current_class = str(row[0]).split(' - ')[1].strip().upper(); new_classes[current_class] = {}
                elif current_class and row[0] and str(row[0]).isdigit():
                    time_slot = row[1]
                    if not time_slot:
                        continue
                    next_rows: List[Tuple[Any,...]] = []
                    for i in range(1,6):
                        if row_idx + i < len(all_rows):
                            nx = all_rows[row_idx + i]
                            if nx[0] and (isinstance(nx[0], int) or str(nx[0]).isdigit()):
                                break
                            next_rows.append(nx)
                    for day, col in day_columns.items():
                        lessons = parse_schedule_row(row, next_rows, col)
                        for l in lessons:
                            full = {'time': time_slot, 'subject': l['subject'], 'room': l['room'], 'teacher': l['teacher']}
                            new_classes.setdefault(current_class, {}).setdefault(day, []).append(full)
            old_classes = old.get(ds, {}).get('classes', {})
            for cname in set(new_classes.keys()) | set(old_classes.keys()):
                if new_classes.get(cname) != old_classes.get(cname):
                    changed.append((ds, cname))
            new[ds] = {'classes': new_classes}
            logger.info(f"Загружено расписание на {ds} — классов: {len(new_classes)}")
        except Exception as e:
            logger.error(f"Ошибка обработки {name}: {e}")
    schedule_data = new; file_hashes = new_hashes; _cached_all_classes = None; _cached_parallels = None
    return changed, None

# ---------- Кэш классов ----------
def get_all_classes() -> List[str]:
    global _cached_all_classes
    if _cached_all_classes is None:
        classes = set()
        for d in schedule_data.values():
            classes.update(d.get('classes', {}).keys())
        _cached_all_classes = sorted(list(classes))
    return _cached_all_classes

def group_classes_by_parallel(all_classes: List[str]) -> Dict[str, List[str]]:
    global _cached_parallels
    if _cached_parallels is None:
        p: Dict[str,List[str]] = {}
        for c in all_classes:
            m = re.match(r'(\d+)', c)
            key = m.group(1) if m else 'Прочее'
            p.setdefault(key, []).append(c)
        _cached_parallels = p
    return _cached_parallels

# ---------- Логи изменений ----------
def log_schedule_changes(changes: List[Dict[str,str]]):
    if not changes:
        return
    log = load_json(CHANGES_LOG_FILE, [])
    now_iso = datetime.now().isoformat()
    for ch in changes:
        log.append({'timestamp': now_iso,'date': ch['date_str'],'class': ch['class_name'],'notification_message': ch['message_text'],'description': f"Изменение расписания класса {ch['class_name']} на {ch['date_str']}"})
    save_json(CHANGES_LOG_FILE, log)

def cleanup_schedule_changes_log(days: int = 2):
    cutoff = datetime.now() - timedelta(days=days)
    log = load_json(CHANGES_LOG_FILE, [])
    new_log = []
    for e in log:
        try:
            if datetime.fromisoformat(e['timestamp']) >= cutoff:
                new_log.append(e)
        except Exception:
            new_log.append(e)
    if len(new_log) != len(log):
        save_json(CHANGES_LOG_FILE, new_log)

# ---------- Меню/экран ----------
async def send_main_menu(update: Update, user_name: str, user_id: int):
    kb: List[List[InlineKeyboardButton]] = []
    kb.append([InlineKeyboardButton("📅 Выбрать класс и дату", callback_data="menu_date_class")])
    kb.append([InlineKeyboardButton("✍️ Написать Администратору", callback_data="menu_help")])

    text = f"Привет, *{escape_md(user_name)}*\nВыберите класс и дату."
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def show_parallel_selection_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    allc = get_all_classes()
    if not allc:
        await q.message.edit_text("❌ Расписание пусто.")
        return SELECT_CLASS_OR_DATE
    p = group_classes_by_parallel(allc)
    kb: List[List[InlineKeyboardButton]] = []; row: List[InlineKeyboardButton] = []
    for par, _ in sorted(p.items(), key=lambda it: int(it[0]) if it[0].isdigit() else 999):
        if len(row) == 4:
            kb.append(row); row = []
        row.append(InlineKeyboardButton(par, callback_data=f"parallel_{par}"))
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("↩️ Меню", callback_data="menu_main")])
    await q.message.edit_text("🗓️ *ШАГ 1: Выберите параллель*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return SELECT_PARALLEL

async def show_class_selection_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, parallel: str):
    q = update.callback_query
    allc = get_all_classes(); p = group_classes_by_parallel(allc)
    if parallel not in p:
        await q.message.edit_text(f"❌ Нет классов для параллели {parallel}.")
        return SELECT_CLASS_OR_DATE
    kb: List[List[InlineKeyboardButton]] = []; row: List[InlineKeyboardButton] = []
    for cname in p[parallel]:
        if len(row) == 3:
            kb.append(row); row = []
        row.append(InlineKeyboardButton(f"{cname}", callback_data=f"class_{cname}"))
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("↩️ К параллелям", callback_data="menu_date_class")])
    await q.message.edit_text(f"🗓️ *ШАГ 2: Выберите класс (Параллель {parallel})*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return SELECT_CLASS

async def show_date_selection_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, class_name: str, page: int = 0):
    q = update.callback_query
    context.user_data['current_class'] = class_name
    all_dates = sorted([d for d in schedule_data if class_name in schedule_data[d].get('classes', {})], key=lambda d: datetime.strptime(d,'%d.%m.%Y'))
    total = len(all_dates); start = page*DATES_PER_PAGE; end = min(start+DATES_PER_PAGE, total)
    if start >= total and page > 0:
        return await show_date_selection_menu(update, context, class_name, 0)
    kb: List[List[InlineKeyboardButton]] = []
    for ds in all_dates[start:end]:
        dow = datetime.strptime(ds,'%d.%m.%Y').strftime('%A').capitalize()
        kb.append([InlineKeyboardButton(f"📅 {ds} ({dow})", callback_data=f"date_{ds}_{class_name}")])
    nav: List[InlineKeyboardButton] = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"dates_page_{page-1}_{class_name}"))
    if total > DATES_PER_PAGE:
        pages = (total + DATES_PER_PAGE - 1)//DATES_PER_PAGE
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="ignore"))
    if end < total: nav.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"dates_page_{page+1}_{class_name}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("🏠 Меню", callback_data="menu_main")])
    await q.message.edit_text(f"🗓️ *ШАГ 3: Выберите дату*\n\nКласс: *{escape_md(class_name)}*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return SELECT_DATE

async def display_final_schedule(update: Update, date_str: str, class_name: str, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        req = datetime.strptime(date_str, '%d.%m.%Y').date()
        h1 = datetime.strptime(HOLIDAYS_START, '%d.%m.%Y').date()
        h2 = datetime.strptime(HOLIDAYS_END, '%d.%m.%Y').date()
    except ValueError:
        msg = f"❌ Неверная дата '{date_str}'."
        if q: await q.message.edit_text(msg)
        else: await context.bot.send_message(chat_id=update.effective_user.id, text=msg)
        return SELECT_CLASS_OR_DATE

    if h1 <= req <= h2:
        txt = (f"🎉 *КАНИКУЛЫ!*\n\n**{date_str}** входит в период: *{HOLIDAYS_START} — {HOLIDAYS_END}*.\nОтдыхайте!")
        kb = [
            [InlineKeyboardButton("↩️ Назад к выбору даты", callback_data=f"back_to_dates_{class_name}")],
            [InlineKeyboardButton("🏠 Меню", callback_data="menu_main")],
        ]
        if q: await q.message.edit_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        else: await context.bot.send_message(update.effective_user.id, txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return SELECT_CLASS_OR_DATE

    raw = schedule_data.get(date_str, {}).get('classes', {}).get(class_name)
    if not raw:
        txt = f"❌ Расписание *{escape_md(class_name)}* на *{date_str}* не найдено."
        kb = [
            [InlineKeyboardButton("↩️ Назад к выбору даты", callback_data=f"back_to_dates_{class_name}")],
            [InlineKeyboardButton("🏠 Меню", callback_data="menu_main")],
        ]
        if q: await q.message.edit_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        else: await context.bot.send_message(update.effective_user.id, txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        return SELECT_CLASS_OR_DATE

    day_lower = get_day_name_from_date_str(date_str)
    final = apply_overrides(date_str, class_name, raw)
    lessons = final.get(day_lower, [])

    try:
        ru_day = datetime.strptime(date_str, '%d.%m.%Y').strftime('%A').capitalize()
    except ValueError:
        ru_day = day_lower.capitalize()

    sep = "\n" + "─" * 28 + "\n"
    msg = f"📚 *Расписание {escape_md(class_name)}* на *{date_str} ({ru_day})*"
    if not lessons:
        msg += "\n\n🎉 *Уроков нет!*"
    else:
        grouped: Dict[str, List[Dict[str,str]]] = {}
        for l in lessons:
            t = escape_md(l.get('time','—').strip()); grouped.setdefault(t, []).append(l)
        msg += sep; counter = 0
        for t, lst in grouped.items():
            msg += f"🕐 *{t}*\n"; uniq_teachers: List[str] = []
            for l in lst:
                counter += 1
                subj = l.get('subject','Предмет').strip(); room = l.get('room','').strip(); teach = l.get('teacher','').strip()
                emoji = next((v for k,v in SUBJECT_EMOJIS.items() if k in subj.lower()), '📘')
                flag = " ✏️" if check_override(date_str, class_name, day_lower, counter) else ""
                line = f"   {emoji} *{escape_md(subj)}*" + (f" ({escape_md(room)})" if room else '')
                msg += line + flag + "\n"
                if teach:
                    uniq_line = escape_md(teach)
                    if uniq_line not in uniq_teachers:
                        uniq_teachers.append(uniq_line)
            if uniq_teachers:
                msg += f"   🧑‍🏫 _{', '.join(uniq_teachers)}_\n"
            msg += "─" * 28 + "\n"

    kb = [
        [InlineKeyboardButton("↩️ Назад к выбору даты", callback_data=f"back_to_dates_{class_name}")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_main")],
    ]
    if q:
        await q.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await context.bot.send_message(update.effective_user.id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return SELECT_CLASS_OR_DATE

async def handle_menu_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if ADMIN_ID:
        txt = f"📩 Связь с администратором: напишите в ЛС.\n\nID: `{ADMIN_ID}`"
    else:
        txt = "❌ Администратор не указан в настройках."
    await q.message.edit_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Меню", callback_data="menu_main")]]))
    return SELECT_CLASS_OR_DATE

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data; uid = q.from_user.id

    if data == 'menu_date_class':
        await show_parallel_selection_menu(update, context)
        return SELECT_PARALLEL

    elif data == 'menu_main':
        await send_main_menu(update, q.from_user.first_name, uid)
        return SELECT_CLASS_OR_DATE

    elif data == 'menu_help':
        return await handle_menu_help(update, context)

    elif data.startswith('parallel_'):
        par = data.split('_')[1]
        context.user_data['parallel'] = par
        await show_class_selection_menu(update, context, par)
        return SELECT_CLASS

    elif data.startswith('class_'):
        cname = data.split('_', 1)[1]
        context.user_data['class_name'] = cname
        await show_date_selection_menu(update, context, cname, 0)
        return SELECT_DATE

    elif data.startswith('dates_page_'):
        parts = data.split('_'); page = int(parts[2]); cname = parts[3]
        await show_date_selection_menu(update, context, cname, page)
        return SELECT_DATE

    elif data.startswith('date_'):
        _, ds, cname = data.split('_', 2)
        await display_final_schedule(update, ds, cname, context)
        return SELECT_CLASS_OR_DATE

    elif data.startswith('back_to_dates_'):
        cname = data.split('_', 3)[3]
        await show_date_selection_menu(update, context, cname, 0)
        return SELECT_DATE

    elif data == 'ignore':
        return SELECT_DATE

    return SELECT_CLASS_OR_DATE

# ---------- Текстовые запросы ----------
SUBJECT_KEYWORDS = '|'.join(sorted({'математика','алгебра','геометрия','русский','литература','английский','немецкий','история','обществознание','география','биология','информатика','физика','химия','физкультура','труд'}))
CLASS_RE = re.compile(r"(\d{1,2}[абвгд])", re.IGNORECASE)
SUBJECT_RE = re.compile(rf"({SUBJECT_KEYWORDS})", re.IGNORECASE)

async def handle_text_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').lower(); uid = update.effective_user.id
    update_user_stats(uid, 'text_query')
    if 'завтра' in text: date_str = get_today_date_str(1)
    elif 'сегодня' in text: date_str = get_today_date_str(0)
    else: date_str = None
    cm = CLASS_RE.search(text)
    class_name = cm.group(1).upper() if cm else None
    if not class_name:
        await update.message.reply_text("❌ Укажи класс, например: 'расписание 7Б сегодня'."); return
    sm = SUBJECT_RE.search(text); subject = sm.group(1).lower() if sm else None
    if subject:
        date_str = date_str or get_today_date_str(0)
        day_lower = get_day_name_from_date_str(date_str)
        raw = schedule_data.get(date_str, {}).get('classes', {}).get(class_name, {})
        if not raw:
            await update.message.reply_text(f"❌ Для {class_name} нет расписания на {date_str}."); return
        final = apply_overrides(date_str, class_name, raw); lessons = final.get(day_lower, [])
        hits = [l for l in lessons if subject in l.get('subject','').lower()]
        if not hits:
            await update.message.reply_text(f"❌ У {class_name} предмет '{subject}' на {date_str} не найден."); return
        lines = [f"• {l['subject']} ({l['time']}) — {l.get('room','')}" for l in hits]
        await update.message.reply_text(f"🔎 *{class_name}* — {subject.capitalize()} на {date_str}:\n" + "\n".join(lines), parse_mode='Markdown'); return
    if date_str:
        dummy = Update(update_id=0, message=None); dummy.callback_query = None
        await display_final_schedule(dummy, date_str, class_name, context)
    else:
        await update.message.reply_text("🗓️ Укажи дату (сегодня/завтра) или конкретный день.")

# ---------- Команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user_id(user.id, user.username, user.full_name)
    update_user_stats(user.id, 'start')
    # Автообновление при старте (как в 2.1.1)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, sync_load_schedule_files)
    except Exception as e:
        logger.warning(f"Не удалось обновить расписание при старте: {e}")
    await send_main_menu(update, user.first_name, user.id)
    return SELECT_CLASS_OR_DATE

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа."); return
    users = get_user_data(); users_count = len(users)
    last_update = max(schedule_data.keys()) if schedule_data else '—'
    y_disk_ok = 'OK'
    try: _ = get_yandex_disk_files()
    except Exception as e: y_disk_ok = f"Ошибка: {e}"
    msg = (f"📊 *Статус:*\n"
           f"👥 Пользователей: {users_count}\n"
           f"🗓️ Последнее расписание: {last_update}\n"
           f"☁️ Яндекс.Диск: {y_disk_ok}\n")
    await update.message.reply_text(msg, parse_mode='Markdown')

async def reload_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not ADMIN_ID:
        await update.message.reply_text("❌ Нет прав."); return
    m = await update.message.reply_text("⏳ Загрузка расписания...")
    loop = asyncio.get_event_loop(); changed, err = await loop.run_in_executor(None, sync_load_schedule_files)
    if err:
        await m.edit_text(f"❌ Ошибка: {escape_md(err)}", parse_mode='Markdown'); return
    if changed:
        changes_for_log = []
        for ds, cname in sorted(set(changed), key=lambda x: datetime.strptime(x[0], '%d.%m.%Y')):
            changes_for_log.append({'date_str': ds,'class_name': cname,'message_text': f"🔔 *РАСПИСАНИЕ ОБНОВЛЕНО!*\nКласс *{escape_md(cname)}* на *{ds}*."})
        log_schedule_changes(changes_for_log)
    await m.edit_text(f"✅ Готово. Изменений: {len(changed)}.", parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not ADMIN_ID:
        await update.message.reply_text("❌ Нет прав."); return
    if not context.args:
        await update.message.reply_text("❌ Укажите текст: /broadcast Текст сообщения"); return
    text = " ".join(context.args); users = get_user_data(); total = len(users)
    await update.message.reply_text(f"⏳ Рассылка {total} пользователям...")
    sent = 0
    for uid in list(users.keys()):
        try:
            await context.bot.send_message(chat_id=int(uid), text=text, parse_mode='Markdown')
            await asyncio.sleep(0.1); sent += 1
        except Exception as e:
            logger.info(f"Не доставлено {uid}: {e}")
    await update.message.reply_text(f"✅ Готово. Отправлено: {sent}/{total}")

# ---------- Джобы ----------
async def schedule_checker(context: ContextTypes.DEFAULT_TYPE):
    global first_schedule_check_done, LAST_ADMIN_ERROR_TIME
    loop = asyncio.get_event_loop(); is_first = not first_schedule_check_done
    changed, err = await loop.run_in_executor(None, sync_load_schedule_files)
    if is_first:
        first_schedule_check_done = True
    if err:
        logger.error(f"Критическая ошибка загрузки: {err}")
        now = datetime.now().timestamp()
        if ADMIN_ID and now - LAST_ADMIN_ERROR_TIME > 3600:
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"🚨 *Ошибка загрузки расписания:*\n{escape_md(err)}", parse_mode='Markdown')
                LAST_ADMIN_ERROR_TIME = now
            except Exception:
                pass
        return
    if not changed:
        return
    changes_for_log = []
    changed_sorted = sorted(list(set(changed)), key=lambda x: datetime.strptime(x[0], '%d.%m.%Y'))
    prefix = "*ℹ️ Бот был перезапущен.* Используйте /start для обновления меню.\n\n" if is_first else ""
    for ds, cname in changed_sorted:
        txt = "🔔 *РАСПИСАНИЕ ОБНОВЛЕНО!*\n\n" + prefix + f"Изменения для класса *{escape_md(cname)}* на *{ds}*."
        changes_for_log.append({'date_str': ds, 'class_name': cname, 'message_text': txt})
    log_schedule_changes(changes_for_log)
    hour = datetime.now().hour; is_quiet = hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR
    if is_quiet and not is_first:
        pend = load_json(PENDING_FILE, []); pend.extend(changes_for_log); save_json(PENDING_FILE, pend)
        logger.info("Изменения сохранены в pending (тихое время)"); return
    # Персональные рассылки (по избранному классу) отключены
    logger.info("Изменения зафиксированы; персональная рассылка отключена (избранные классы удалены).")

async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    # Персональные утренние рассылки отключены (нет избранных классов)
    logger.info("Ежедневная рассылка пропущена: избранные классы отключены.")

async def send_pending_notifications(context: ContextTypes.DEFAULT_TYPE):
    pend = load_json(PENDING_FILE, [])
    if not pend: return
    logger.info("Очистка pending уведомлений (персональные рассылки отключены).")
    save_json(PENDING_FILE, [])

async def cleanup_log_job(context: ContextTypes.DEFAULT_TYPE):
    cleanup_schedule_changes_log(2)

async def stats_job(context: ContextTypes.DEFAULT_TYPE):
    record_daily_stats()

# ---------- Запуск ----------
def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан!"); return
    load_user_data_from_disk(); load_overrides()
    app: Application = ApplicationBuilder().token(TOKEN).build()

    # Команды вне диалога
    app.add_handler(CommandHandler('status', status_command))
    app.add_handler(CommandHandler('reload_schedule', reload_schedule_command))
    app.add_handler(CommandHandler('broadcast', broadcast_command))

    # Диалог
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECT_CLASS_OR_DATE: [CallbackQueryHandler(button_handler)],
            SELECT_PARALLEL: [CallbackQueryHandler(button_handler)],
            SELECT_CLASS: [CallbackQueryHandler(button_handler)],
            SELECT_DATE: [CallbackQueryHandler(button_handler)],
        },
        fallbacks=[CommandHandler('start', start)],
    )
    app.add_handler(conv)

    # Текстовые запросы
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_query))

    # Джобы
    jq = app.job_queue
    jq.run_repeating(lambda c: save_user_data_job(c), interval=USER_CACHE_INTERVAL, first=USER_CACHE_INTERVAL)
    jq.run_repeating(schedule_checker, interval=300, first=5)
    jq.run_daily(cleanup_log_job, time=time(0, 0))
    jq.run_daily(send_daily_schedule, time=time(7, 30))
    jq.run_daily(send_pending_notifications, time=time(7, 1))
    jq.run_daily(stats_job, time=time(23, 59))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    if os.name == 'nt':
        try:
            import asyncio as _asyncio
            _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    main()
