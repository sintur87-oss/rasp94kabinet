import os
import logging
from datetime import datetime, timedelta
import openpyxl
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import asyncio

# Настройки бота
TOKEN = "8490823353:AAES_Ct4RcBRQBso764mFDeUU8Ag6HLnfns"
ADMIN_ID = 499909752

# Включим логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния разговора
SELECT_DAY, SELECT_PARALLEL, SELECT_CLASS = range(3)

# Глобальные переменные для хранения данных
schedule_data = {}
user_data_file = "users.txt"

def get_base_dir():
    """Получить базовую директорию, где находится исполняемый файл"""
    return os.path.dirname(os.path.abspath(__file__))

def save_user_id(user_id):
    """Сохранить ID пользователя в файл"""
    try:
        with open(user_data_file, 'a+') as f:
            f.seek(0)
            existing_ids = set(line.strip() for line in f)
            if str(user_id) not in existing_ids:
                f.write(f"{user_id}\n")
    except Exception as e:
        logger.error(f"Ошибка при сохранении ID пользователя: {str(e)}")

def get_all_user_ids():
    """Получить все ID пользователей из файла"""
    try:
        with open(user_data_file, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"Ошибка при чтении ID пользователей: {str(e)}")
        return []

def extract_date_from_filename(filename):
    """Извлечь дату из имени файла"""
    if filename.endswith('.xlsx'):
        filename = filename[:-5]
    
    date_formats = [
        "%d.%m.%Y", "%d.%m.%y", "%Y.%m.%d", 
        "%d-%m-%Y", "%Y-%m-%d", "%Y%m%d"
    ]
    
    for date_format in date_formats:
        try:
            datetime.strptime(filename, date_format)
            return filename
        except ValueError:
            continue
    
    return None

def is_valid_lesson(subject):
    """Проверить, является ли урок действительным (не пустым)"""
    if not subject:
        return False
    
    subject_str = str(subject).strip()
    if not subject_str or subject_str == "None":
        return False
    
    # Игнорируем строки, которые являются днями недели
    days_of_week = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
    if any(day in subject_str for day in days_of_week):
        return False
    
    return True

def parse_schedule_row(row, next_rows, time_slot, day_col):
    """Парсим строки расписания и связанные с ней строки"""
    lessons = []
    
    # Получаем предмет и кабинет из текущей строки
    subject = row[day_col] if day_col < len(row) else None
    room = row[day_col + 1] if day_col + 1 < len(row) else None
    
    if is_valid_lesson(subject):
        # Создаем урок из текущей строки
        lesson = {
            'subject': str(subject).strip(),
            'room': str(room).strip() if room and str(room).strip() != "None" else "",
            'teacher': ""
        }
        lessons.append(lesson)
    
    # Ищем дополнительные уроки и учителей в следующих строках
    for next_row in next_rows:
        if not any(next_row):
            continue
            
        next_subject = next_row[day_col] if day_col < len(next_row) else None
        next_room = next_row[day_col + 1] if day_col + 1 < len(next_row) else None
        
        # Если в следующей строке есть предмет - это новый урок
        if is_valid_lesson(next_subject):
            next_lesson = {
                'subject': str(next_subject).strip(),
                'room': str(next_room).strip() if next_room and str(next_room).strip() != "None" else "",
                'teacher': ""
            }
            lessons.append(next_lesson)
        # Если в следующей строке нет предмета, но есть текст - это учитель
        elif next_row[day_col] and str(next_row[day_col]).strip() and not any(str(next_row[day_col]).strip().startswith(word) for word in ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]):
            # Добавляем учителя к последнему уроку
            if lessons:
                if lessons[-1]['teacher']:
                    lessons[-1]['teacher'] += " " + str(next_row[day_col]).strip()
                else:
                    lessons[-1]['teacher'] = str(next_row[day_col]).strip()
    
    # Фильтруем пустые уроки
    valid_lessons = []
    for lesson in lessons:
        if lesson['subject'].strip() and lesson['subject'] != "None":
            valid_lessons.append(lesson)
    
    return valid_lessons

def load_schedule_files():
    """Загрузка всех файлов расписания из директории с исполняемым файлом"""
    global schedule_data
    
    schedule_data = {}
    
    base_dir = get_base_dir()
    logger.info(f"Поиск файлов расписания в директории: {base_dir}")
    
    # Ищем все xlsx файлы в текущей директории
    for file in os.listdir(base_dir):
        if file.endswith(".xlsx"):
            try:
                file_path = os.path.join(base_dir, file)
                logger.info(f"Найден файл расписания: {file_path}")
                
                # Извлекаем дату из имени файла
                date_str = extract_date_from_filename(file)
                
                if not date_str:
                    logger.warning(f"Неверный формат даты в имени файла: {file}. Пропускаем.")
                    continue
                
                # Загружаем файл
                wb = openpyxl.load_workbook(file_path, data_only=True)
                sheet = wb.active
                
                # Парсим расписание
                classes = {}
                current_class = None
                day_columns = {}
                
                # Собираем все строки для анализа
                all_rows = list(sheet.iter_rows(values_only=True))
                
                for row_idx, row in enumerate(all_rows):
                    if not any(row):
                        continue
                    
                    row = tuple(cell if cell is not None else "" for cell in row)
                    
                    # Ищем заголовок с днями недели
                    if row[0] == "#" and "Время" in str(row[1]):
                        # Определяем колонки для каждого дня
                        day_columns = {}
                        
                        # Ищем дни недели в строке
                        for col_idx, cell_value in enumerate(row):
                            if cell_value and any(day in str(cell_value) for day in ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]):
                                day_columns[cell_value] = col_idx
                    
                    # Ищем начало расписания для класса
                    elif "Класс -" in str(row[0]):
                        class_name = str(row[0]).split(" - ")[1].strip()
                        current_class = class_name
                        classes[current_class] = {day: [] for day in day_columns}
                        logger.info(f"Найден класс: {current_class}")
                    
                    # Парсим строки с расписанием
                    elif current_class and row[0] and str(row[0]).isdigit():
                        time_slot = row[1]
                        if not time_slot:
                            continue
                            
                        # Получаем следующие 5 строк для анализа
                        next_rows = []
                        for i in range(1, 6):
                            if row_idx + i < len(all_rows):
                                next_row = all_rows[row_idx + i]
                                next_row = tuple(cell if cell is not None else "" for cell in next_row)
                                # Прерываем, если наткнулись на новую строку с временем
                                if next_row[0] and str(next_row[0]).isdigit():
                                    break
                                next_rows.append(next_row)
                        
                        # Для каждого дня извлекаем данные
                        for day, col in day_columns.items():
                            lessons = parse_schedule_row(row, next_rows, time_slot, col)
                            
                            for lesson in lessons:
                                full_lesson_data = {
                                    'time': time_slot,
                                    'subject': lesson['subject'],
                                    'room': lesson['room'],
                                    'teacher': lesson['teacher']
                                }
                                
                                if day not in classes[current_class]:
                                    classes[current_class][day] = []
                                classes[current_class][day].append(full_lesson_data)
                
                # Сохраняем расписание для этой дата
                schedule_data[date_str] = {
                    'classes': classes
                }
                
                logger.info(f"Загружено расписание на {date_str}")
                logger.info(f"Найдены классы: {list(classes.keys())}")
                
            except Exception as e:
                logger.error(f"Ошибка при загрузке файла {file}: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
    
    logger.info(f"Загружено {len(schedule_data)} файлов расписания")

def get_next_school_day(date_obj):
    """Получить следующий учебный день (пропускаем воскресенье)"""
    next_day = date_obj + timedelta(days=1)
    # Если следующий день воскресенье, переходим на понедельник
    if next_day.weekday() == 6:  # 6 = воскресенье
        next_day += timedelta(days=1)
    return next_day

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"Пользователь {user.id} запустил бота")
    
    # Сохраняем ID пользователя
    save_user_id(user.id)
    
    # Перезагружаем расписания при каждом старте
    load_schedule_files()
    
    if not schedule_data:
        await update.message.reply_text(
            "❌ Файлы расписания не найдены. Пожалуйста, разместите файлы в формате ДД.ММ.ГГГГ.xlsx "
            "в той же папке, где находится бот, и перезапустите бота командой /start."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот для просмотра расписания занятий.\n\n"
        "Выберите день:",
        reply_markup=ReplyKeyboardMarkup([
            ["📅 Расписание на сегодня", "📅 Расписание на завтра"],
            ["🔄 Обновить расписания", "🔄 Перезапуск"]
        ], one_time_keyboard=True, resize_keyboard=True)
    )
    
    return SELECT_DAY

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды помощи"""
    help_text = (
        "🤖 <b>Бот расписания занятий</b>\n\n"
        "📅 <b>Расписание на сегодня</b> - посмотреть расписание на сегодня\n"
        "📅 <b>Расписание на завтра</b> - посмотреть расписание на завтра\n"
        "🔄 <b>Обновить расписания</b> - перезагрузить файлы расписания\n"
        "🔄 <b>Перезапуск</b> - перезапустить бота\n\n"
        "После выбора дня вам будет предложено выбрать параллель и класс.\n"
        "Расписание загружается из Excel-файлов в формате ДД.ММ.ГГГГ.xlsx"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')
    return SELECT_DAY

async def reload_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновление расписаний"""
    load_schedule_files()
    
    if not schedule_data:
        await update.message.reply_text("❌ Файлы расписания не найдены. Пожалуйста, разместите файлы в формате ДД.ММ.ГГГГ.xlsx в той же папке, где находится бот.")
        return SELECT_DAY
    
    await update.message.reply_text("✅ Расписания успешно обновлены!")
    return SELECT_DAY

async def select_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора дня"""
    user_text = update.message.text
    
    if user_text == "🔄 Перезапуск":
        return await start(update, context)
    elif user_text == "🔄 Обновить расписания":
        return await reload_schedules(update, context)
    elif user_text == "📅 Расписание на сегодня":
        today = datetime.now()
        # Если сегодня воскресенье, показываем сообщение об отсутствии занятий
        if today.weekday() == 6:  # 6 = воскресенье
            await update.message.reply_text("Сегодня (воскресенье) уроков нет.")
            return SELECT_DAY
        return await show_schedule_for_date(update, context, today.strftime("%d.%m.%Y"), "сегодня")
    elif user_text == "📅 Расписание на завтра":
        today = datetime.now()
        tomorrow = get_next_school_day(today)
        
        # Определяем правильную метку для дня
        if tomorrow.weekday() == 0 and today.weekday() == 6:  # Если сегодня воскресенье, а завтра понедельник
            day_label = "понедельник"
        elif tomorrow.weekday() == 0 and today.weekday() == 5:  # Если сегодня суббота, а завтра понедельник
            day_label = "понедельник"
        else:
            day_label = "завтра"
            
        return await show_schedule_for_date(update, context, tomorrow.strftime("%d.%m.%Y"), day_label)
    
    await update.message.reply_text("Пожалуйста, выберите один из предложенных вариантов.")
    return SELECT_DAY

async def show_schedule_for_date(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str, date_label):
    """Показать расписание для указанной даты"""
    # Проверяем все загруженные даты на соответствие
    matched_date = None
    for loaded_date in schedule_data.keys():
        try:
            # Пробуем разные форматы дат для сравнения
            date_formats = ["%d.%m.%Y", "%d.%m.%y", "%Y.%m.%d", "%d-%m-%Y", "%Y-%m-%d", "%Y%m%d"]
            for fmt in date_formats:
                try:
                    loaded_date_obj = datetime.strptime(loaded_date, fmt)
                    target_date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                    if loaded_date_obj.date() == target_date_obj.date():
                        matched_date = loaded_date
                        break
                except ValueError:
                    continue
            if matched_date:
                break
        except:
            continue
    
    if not matched_date:
        await update.message.reply_text(f"На {date_label} ({date_str}) расписание не найдено.")
        return SELECT_DAY
    
    # Получаем список всех классов для указанного дня
    classes_on_date = list(schedule_data[matched_date]['classes'].keys())
    if not classes_on_date:
        await update.message.reply_text(f"На {date_label} нет занятий.")
        return SELECT_DAY
    
    # Предлагаем выбрать параллель
    parallels = []
    for cls in classes_on_date:
        if cls and cls[-1].isalpha():
            parallel = cls[:-1]
        else:
            parallel = cls
        if parallel and parallel not in parallels:
            parallels.append(parallel)
    
    parallels = sorted(parallels)
    
    keyboard = []
    row = []
    for i, parallel in enumerate(parallels):
        row.append(parallel)
        if len(row) == 3 or i == len(parallels) - 1:
            keyboard.append(row)
            row = []
    
    keyboard.append(["↩️ Назад"])
    
    context.user_data['selected_date'] = matched_date
    context.user_data['date_label'] = date_label
    context.user_data['date_str'] = date_str
    
    await update.message.reply_text(
        f"Выберите параллель для {date_label} ({date_str}):",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    
    return SELECT_PARALLEL

async def select_parallel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора параллели"""
    user_text = update.message.text
    
    # Проверяем, не нажата ли кнопка "Назад"
    if user_text == "↩️ Назад":
        await update.message.reply_text(
            "Выберите день:",
            reply_markup=ReplyKeyboardMarkup([
                ["📅 Расписание на сегодня", "📅 Расписание на завтра"],
                ["🔄 Обновить расписания", "🔄 Перезапуск"]
            ], one_time_keyboard=True, resize_keyboard=True)
        )
        return SELECT_DAY
    
    selected_date = context.user_data.get('selected_date')
    if not selected_date:
        await update.message.reply_text("Ошибка: дата не выбрана.")
        return SELECT_DAY
    
    # Получаем список классов для выбранной даты
    classes_in_date = list(schedule_data[selected_date]['classes'].keys())
    
    # Получаем выбранную параллель
    selected_parallel = user_text
    
    # Фильтруем классы по параллели
    classes_in_parallel = []
    for cls in classes_in_date:
        if cls and cls[-1].isalpha():
            parallel = cls[:-1]
        else:
            parallel = cls
            
        if parallel == selected_parallel:
            classes_in_parallel.append(cls)
    
    if not classes_in_parallel:
        await update.message.reply_text("Для выбранной параллели нет классов.")
        return SELECT_PARALLEL
    
    # Создаем клавиатуру с классами
    keyboard = [classes_in_parallel[i:i+3] for i in range(0, len(classes_in_parallel), 3)]
    keyboard.append(["↩️ Назад"])
    
    context.user_data['selected_parallel'] = selected_parallel
    
    await update.message.reply_text(
        "Выберите класс:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    
    return SELECT_CLASS

async def select_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора класса"""
    user_text = update.message.text
    
    # Проверяем, не нажата ли кнопка "Назад"
    if user_text == "↩️ Назад":
        # Возвращаемся к выбору параллели
        selected_date = context.user_data.get('selected_date')
        date_label = context.user_data.get('date_label', '')
        date_str = context.user_data.get('date_str', '')
        
        classes_on_date = list(schedule_data[selected_date]['classes'].keys())
        
        parallels = []
        for cls in classes_on_date:
            if cls and cls[-1].isalpha():
                parallel = cls[:-1]
            else:
                parallel = cls
            if parallel and parallel not in parallels:
                parallels.append(parallel)
        
        parallels = sorted(parallels)
        
        keyboard = []
        row = []
        for i, parallel in enumerate(parallels):
            row.append(parallel)
            if len(row) == 3 or i == len(parallels) - 1:
                keyboard.append(row)
                row = []
        
        keyboard.append(["↩️ Назад"])
        
        await update.message.reply_text(
            f"Выберите параллель для {date_label} ({date_str}):",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        
        return SELECT_PARALLEL
    
    selected_date = context.user_data.get('selected_date')
    selected_parallel = context.user_data.get('selected_parallel')
    selected_class = user_text
    
    if not all([selected_date, selected_class]):
        await update.message.reply_text("Ошибка: недостаточно данных.")
        return SELECT_DAY
    
    # Получаем день недели
    date_obj = None
    date_formats = ["%d.%m.%Y", "%d.%m.%y", "%Y.%m.%d", "%d-%m-%Y", "%Y-%m-%d", "%Y%m%d"]
    for fmt in date_formats:
        try:
            date_obj = datetime.strptime(selected_date, fmt)
            break
        except ValueError:
            continue
    
    if not date_obj:
        await update.message.reply_text("Ошибка: неверный формат даты.")
        return SELECT_DAY
    
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    day_name = days[date_obj.weekday()]
    
    # Получаем расписание
    class_schedule = schedule_data[selected_date]['classes'].get(selected_class, {}).get(day_name, [])
    
    # Фильтруем пустые уроки
    class_schedule = [lesson for lesson in class_schedule if lesson.get('subject', '').strip()]
    
    if not class_schedule:
        await update.message.reply_text(f"На {selected_date} ({day_name}) для {selected_class} класса нет занятий.")
        
        # Возвращаем к главному меню
        await update.message.reply_text(
            "Выберите действие:",
            reply_markup=ReplyKeyboardMarkup([
                ["📅 Расписание на сегодня", "📅 Расписание на завтра"],
                ["🔄 Обновить расписания", "🔄 Перезапуск"]
            ], one_time_keyboard=True, resize_keyboard=True)
        )
        return SELECT_DAY
    
    # Группируем уроки по времени
    lessons_by_time = {}
    for lesson in class_schedule:
        time_slot = lesson['time']
        if time_slot not in lessons_by_time:
            lessons_by_time[time_slot] = []
        lessons_by_time[time_slot].append(lesson)
    
    # Формируем сообщение с расписанием
    schedule_text = f"<b>📚 Расписание для {selected_class} на {selected_date} ({day_name})</b>\n\n"
    
    # Определяем порядок уроков по времени
    time_order = [
        "8:00 - 8:40", "8:50 - 9:30", "9:50 - 10:30", "10:50 - 11:30",
        "11:50 - 12:30", "12:40 - 13:20", "13:40 - 14:20", "14:40 - 15:20",
        "15:40 - 16:20", "16:30 - 17:10", "17:20 - 18:00", "18:10 - 18:50"
    ]
    
    lesson_number = 1
    
    for time_slot in time_order:
        if time_slot not in lessons_by_time:
            continue
            
        lessons = lessons_by_time[time_slot]
        # Пропускаем пустые уроки
        valid_lessons = [lesson for lesson in lessons if lesson['subject'].strip()]
        if not valid_lessons:
            continue
        
        # Форматируем время
        formatted_time = time_slot.replace(' - ', '-')
        
        # Форматируем информацию об уроке
        lesson_info = []
        room_info = []
        
        for lesson in valid_lessons:
            subject = lesson['subject']
            teacher = lesson.get('teacher', '').strip()
            room = lesson.get('room', '').strip()
            
            # Формируем строку урока с эмодзи
            if teacher:
                lesson_str = f"- {subject} - {teacher}"
            else:
                lesson_str = f"- {subject}"
            
            lesson_info.append(lesson_str)
            
            # Добавляем кабинет, если есть
            if room and room not in room_info:
                room_info.append(room)
        
        # Добавляем номер урока и время
        schedule_text += f"<code>{lesson_number}️⃣ 🕐 {formatted_time}\n"
        
        # Добавляем информацию об урокаи
        schedule_text += "\n".join(lesson_info) + "\n"
        
        # Добавляем информацию о кабинетах
        if room_info:
            schedule_text += f"🚪 Каб. {', '.join(room_info)}\n"
        
        schedule_text += "</code>\n\n"
        lesson_number += 1
    
    # Убираем лишние переносы в конце
    schedule_text = schedule_text.strip()
    
    # Если сообщение слишком длинное, разбиваем на части
    if len(schedule_text) > 4000:
        parts = []
        while schedule_text:
            if len(schedule_text) > 4000:
                part = schedule_text[:4000]
                last_newline = part.rfind('\n')
                if last_newline != -1:
                    parts.append(part[:last_newline])
                    schedule_text = schedule_text[last_newline+1:]
                else:
                    parts.append(part)
                    schedule_text = schedule_text[4000:]
            else:
                parts.append(schedule_text)
                break
        
        for part in parts:
            await update.message.reply_text(part, parse_mode='HTML')
    else:
        await update.message.reply_text(schedule_text, parse_mode='HTML')
    
    # Возвращаем к главному меню
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=ReplyKeyboardMarkup([
            ["📅 Расписание на сегодня", "📅 Расписание на завтра"],
            ["🔄 Обновить расписания", "🔄 Перезапуск"]
        ], one_time_keyboard=True, resize_keyboard=True)
    )
    
    return SELECT_DAY

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена действия"""
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=ReplyKeyboardMarkup([
            ["📅 Расписание на сегодня", "📅 Расписание на завтра"],
            ["🔄 Обновить расписания", "🔄 Перезапуск"]
        ], one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_DAY

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка сообщения всем пользователям (только для администратора)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /broadcast <сообщение>")
        return
    
    message = " ".join(context.args)
    user_ids = get_all_user_ids()
    
    if not user_ids:
        await update.message.reply_text("Нет сохраненных пользователей для рассылки.")
        return
    
    success_count = 0
    fail_count = 0
    
    # Отправляем сообщение всем пользователям
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 Рассылка от администратора:\n\n{message}")
            success_count += 1
            # Небольшая задержка, чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {uid}: {str(e)}")
            fail_count += 1
    
    await update.message.reply_text(
        f"✅ Рассылка завершена:\n"
        f"Успешно: {success_count}\n"
        f"Не удалось: {fail_count}"
    )

async def save_user_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет ID пользователя при любом взаимодействии"""
    user_id = update.effective_user.id
    save_user_id(user_id)

def main():
    """Основная функция"""
    # Загружаем расписания при запуске
    load_schedule_files()
    
    # Создаем Application
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчик для сохранения ID пользователя при любом сообщении
    application.add_handler(MessageHandler(filters.ALL, save_user_id_handler), group=-1)
    
    # Создаем обработчик разговора
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECT_DAY: [
                MessageHandler(filters.Regex("^(📅 Расписание на сегодня|📅 Расписание на завтра|🔄 Обновить расписания|🔄 Перезапуск)$"), select_day),
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_day)
            ],
            SELECT_PARALLEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_parallel)],
            SELECT_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_class)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("help", help_command))
    
    # Запускаем бота
    logger.info("Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    main()
