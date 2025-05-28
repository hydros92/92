import sqlite3
import os
import telebot
from telebot import types # Явно імпортуємо types для зручності
import logging
from datetime import datetime, timedelta
import re
import json # Для зберігання фото як JSON рядка
import requests # Для HTTP запитів до AI API
from dotenv import load_dotenv
import os

load_dotenv()  # Завантажує змінні з .env

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
CHANNEL_ID = os.getenv('CHANNEL_ID')
MONOBANK_CARD_NUMBER = os.getenv('MONOBANK_CARD_NUMBER')
GROK_API_KEY = os.getenv('GROK_API_KEY')
GROK_API_URL = os.getenv('GROK_API_URL')

print("Token:", TOKEN)
print("Admin Chat ID:", ADMIN_CHAT_ID)
print("Channel ID:", CHANNEL_ID)
print("Monobank Card Number:", MONOBANK_CARD_NUMBER)
print("Grok API Key:", GROK_API_KEY)
print("Grok API URL:", GROK_API_URL)






# --- 1. Конфігурація Бота ---
# !!! ВАЖЛИВО: Зберігайте ці дані як змінні середовища !!!
# TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'ВАШ_НОВИЙ_АКТУАЛЬНИЙ_ТОКЕН')
# ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', 'ВАШ_ADMIN_CHAT_ID'))
# CHANNEL_ID = int(os.getenv('CHANNEL_ID', 'ВАШ_CHANNEL_ID'))
# MONOBANK_CARD_NUMBER = os.getenv('MONOBANK_CARD_NUMBER', 'ВАША_КАРТКА_MONOBANK')
# GROK_API_KEY = os.getenv('GROK_API_KEY', 'ВАШ_GROK_API_KEY') # NEW: API ключ для Grok
# GROK_API_URL = os.getenv('GROK_API_URL', 'URL_ДЛЯ_GROK_API') # NEW: URL для Grok API

# ЗАГЛУШКИ НА ЧАС РОЗРОБКИ (замініть на змінні середовища в реальному проекті!)
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8039977178:AAGS-GbH-lhljGGG6OgJ2iMU_ncB-JzeOvU') # ЗАМІНІТЬ ЦЕЙ ТОКЕН НЕГАЙНО!
admin_chat_id_str = os.getenv('ADMIN_CHAT_ID', '8184456641').strip("'\"")
ADMIN_CHAT_ID = int(admin_chat_id_str)
channel_id_str = os.getenv('CHANNEL_ID', '-1002535586055').strip("'\"")
CHANNEL_ID = int(channel_id_str)
MONOBANK_CARD_NUMBER = '4441 1111 5302 1484' # Замініть на реальну або змінну середовища
GROK_API_KEY = 'YOUR_GROK_API_KEY_HERE' # NEW: Замініть або використовуйте env
GROK_API_URL = 'YOUR_GROK_API_ENDPOINT_HERE' # NEW: Замініть або використовуйте env


# --- 2. Налаштування логування ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("bot.log", encoding='utf-8'), # Додано encoding
                        logging.StreamHandler()
                    ])
logger = logging.getLogger(__name__)

# --- 3. Ініціалізація бота ---
bot = telebot.TeleBot(TOKEN)

# --- 4. Зберігання даних користувача для багатошагових процесів ---
user_data = {} # {chat_id: {'step': '...', 'data': {...}}}

# --- 5. Управління базою даних (SQLite) ---
DB_NAME = 'seller_bot.db'

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Таблиця користувачів
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_blocked BOOLEAN DEFAULT FALSE,
            blocked_by INTEGER, -- ID адміна, який заблокував
            blocked_at TIMESTAMP,
            commission_paid REAL DEFAULT 0, -- NEW: Сплачена комісія
            commission_due REAL DEFAULT 0,   -- NEW: Належна комісія
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблиця товарів
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_chat_id INTEGER NOT NULL,
            seller_username TEXT,
            product_name TEXT NOT NULL,
            price TEXT NOT NULL, -- Може бути числом або "Договірна"
            description TEXT NOT NULL,
            photos TEXT, -- JSON рядок file_id
            geolocation TEXT, -- NEW: JSON рядок {'latitude': ..., 'longitude': ...}
            status TEXT DEFAULT 'pending', -- pending, approved, rejected, sold, expired
            commission_rate REAL DEFAULT 0.10, -- NEW: Ставка комісії (наприклад, 10%)
            commission_amount REAL DEFAULT 0,  -- NEW: Розрахована сума комісії
            moderator_id INTEGER,
            moderated_at TIMESTAMP,
            admin_message_id INTEGER,
            channel_message_id INTEGER,
            views INTEGER DEFAULT 0, -- NEW: Кількість переглядів (якщо реалізовувати)
            promotion_ends_at TIMESTAMP, -- NEW: Для "підняття" оголошень
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_chat_id) REFERENCES users (chat_id)
        )
    ''')

    # Таблиця для переписок (якщо AI буде вести діалоги)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations ( -- NEW:
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_chat_id INTEGER NOT NULL,
            product_id INTEGER, -- Якщо розмова стосується товару
            message_text TEXT,
            sender_type TEXT, -- 'user' або 'ai'
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_chat_id) REFERENCES users (chat_id),
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    ''')
    
    # Таблиця для транзакцій комісій
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commission_transactions ( -- NEW:
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            seller_chat_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending_payment', -- pending_payment, paid, cancelled
            payment_details TEXT, -- Наприклад, скріншот або ID транзакції
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products (id),
            FOREIGN KEY (seller_chat_id) REFERENCES users (chat_id)
        )
    ''')


    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            user_id INTEGER, -- Може бути NULL для системних дій
            product_id INTEGER,
            details TEXT, -- Додаткова інформація (наприклад, сума комісії)
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("База даних ініціалізована або вже існує.")

# --- 6. Декоратор для обробки помилок ---
def error_handler(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Помилка в {func.__name__}: {e}", exc_info=True)
            # Спроба отримати chat_id з аргументів
            chat_id_to_notify = ADMIN_CHAT_ID
            if args:
                first_arg = args[0]
                if isinstance(first_arg, types.Message):
                    chat_id_to_notify = first_arg.chat.id
                elif isinstance(first_arg, types.CallbackQuery):
                    chat_id_to_notify = first_arg.message.chat.id
            
            try:
                bot.send_message(ADMIN_CHAT_ID, f"🚨 Критична помилка в боті!\nФункція: {func.__name__}\nПомилка: {e}\nДивіться деталі в bot.log")
                if chat_id_to_notify != ADMIN_CHAT_ID:
                    bot.send_message(chat_id_to_notify, "😔 Вибачте, сталася внутрішня помилка. Адміністратор вже сповіщений.")
            except Exception as e_notify:
                logger.error(f"Не вдалося надіслати повідомлення про помилку: {e_notify}")
    return wrapper

# --- 7. Допоміжні функції ---
@error_handler
def save_user(message_or_user):
    """Зберігає або оновлює інформацію про користувача."""
    user = None
    chat_id = None

    if isinstance(message_or_user, types.Message):
        user = message_or_user.from_user
        chat_id = message_or_user.chat.id
    elif isinstance(message_or_user, types.User):
        user = message_or_user
        chat_id = user.id # Припускаємо, що chat_id = user.id
    else:
        logger.warning(f"save_user отримав невідомий тип: {type(message_or_user)}")
        return

    if not user or not chat_id:
        logger.warning("save_user: user або chat_id не визначено.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT chat_id FROM users WHERE chat_id = ?", (chat_id,))
        exists = cursor.fetchone()
        if exists:
            cursor.execute('''
                UPDATE users SET username = ?, first_name = ?, last_name = ?, last_activity = CURRENT_TIMESTAMP
                WHERE chat_id = ?
            ''', (user.username, user.first_name, user.last_name, chat_id))
        else:
            cursor.execute('''
                INSERT INTO users (chat_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            ''', (chat_id, user.username, user.first_name, user.last_name))
        conn.commit()
        logger.info(f"Користувача {chat_id} збережено/оновлено.")
    except Exception as e:
        logger.error(f"Помилка при збереженні користувача {chat_id}: {e}")
    finally:
        conn.close()

@error_handler
def is_user_blocked(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_blocked FROM users WHERE chat_id = ?", (chat_id,))
        result = cursor.fetchone()
        return result and result['is_blocked']
    except Exception as e:
        logger.error(f"Помилка перевірки блокування для {chat_id}: {e}")
        return True # Вважаємо заблокованим у разі помилки для безпеки
    finally:
        conn.close()

# TODO: Додати функції set_user_block_status, generate_hashtags, log_statistics (вони є у вашому коді)

@error_handler
def get_ai_response(prompt, conversation_history=None): # NEW: Функція для взаємодії з AI
    """
    Надсилає запит до AI (Grok або іншого LLM) та повертає відповідь.
    `conversation_history` - це список попередніх повідомлень для контексту.
    Приклад формату для conversation_history:
    [
        {"role": "user", "content": "Привіт"},
        {"role": "assistant", "content": "Вітаю! Чим можу допомогти?"}
    ]
    """
    if not GROK_API_KEY or not GROK_API_URL:
        logger.warning("GROK_API_KEY або GROK_API_URL не налаштовані. AI відповідь неможлива.")
        return "На жаль, мій штучний інтелект зараз недоступний."

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }
    # Формат payload може відрізнятися залежно від конкретного API
    payload = {
        "model": "grok-1", # Або інша модель
        "messages": [],
        "prompt": prompt, # Деякі API можуть використовувати 'prompt' замість 'messages'
        # "max_tokens": 150 # Обмеження довжини відповіді
    }
    if conversation_history:
         payload["messages"].extend(conversation_history)
    payload["messages"].append({"role": "user", "content": prompt})


    try:
        logger.info(f"Надсилання запиту до AI: {prompt[:100]}...")
        response = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=30) # timeout 30 секунд
        response.raise_for_status() # Генерує помилку для поганих статусів (4xx або 5xx)
        
        ai_data = response.json()
        # Обробка відповіді залежить від структури JSON, яку повертає API
        # Це лише приклад:
        if ai_data.get("choices") and len(ai_data["choices"]) > 0:
            content = ai_data["choices"][0].get("message", {}).get("content")
            if not content and ai_data["choices"][0].get("text"): # для деяких API
                 content = ai_data["choices"][0].get("text")
            
            if content:
                logger.info(f"AI відповідь отримана: {content[:100]}...")
                return content.strip()
            else:
                logger.error(f"Не вдалося отримати текст з відповіді AI: {ai_data}")
                return "Вибачте, я не зміг обробити ваш запит."
        else:
            logger.error(f"Неочікувана структура відповіді від AI: {ai_data}")
            return "Сталася помилка при спілкуванні зі штучним інтелектом."

    except requests.exceptions.RequestException as e:
        logger.error(f"Помилка HTTP запиту до AI API: {e}")
        return "Проблема зі з'єднанням до мого помічника. Спробуйте пізніше."
    except Exception as e:
        logger.error(f"Загальна помилка при отриманні відповіді від AI: {e}")
        return "Виникла непередбачена помилка з AI."

# --- 8. Розмітки клавіатур ---
main_menu_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
main_menu_markup.add(types.KeyboardButton("📦 Додати товар"), types.KeyboardButton("📋 Мої товари"))
main_menu_markup.add(types.KeyboardButton("❓ Допомога"), types.KeyboardButton("💰 Комісія")) # NEW: Кнопка Комісія
main_menu_markup.add(types.KeyboardButton("📺 Наш канал"))

# --- 9. Обробники команд ---
@bot.message_handler(commands=['start'])
@error_handler
def send_welcome(message):
    chat_id = message.chat.id
    if is_user_blocked(chat_id):
        bot.send_message(chat_id, "❌ Ваш акаунт заблоковано.")
        return

    save_user(message)
    # log_statistics('start', chat_id) # TODO: Розкоментувати та реалізувати log_statistics

    logger.info(f"Користувач {chat_id} запустив бота.")
    welcome_text = (
        "🛍️ *Ласкаво просимо до SellerBot!*\n\n"
        "Я ваш помічник для продажу та купівлі товарів. "
        "Мене підтримує передовий AI для допомоги у спілкуванні та угодах!\n\n" # NEW: Згадка про AI
        "Оберіть дію з меню:"
    )
    bot.send_message(chat_id, welcome_text, reply_markup=main_menu_markup, parse_mode='Markdown')

@bot.message_handler(commands=['admin'])
@error_handler
def admin_panel(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "❌ У вас немає прав доступу.")
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        types.InlineKeyboardButton("⏳ Товари на модерації", callback_data="admin_pending"),
        types.InlineKeyboardButton("👥 Користувачі", callback_data="admin_users"),
        types.InlineKeyboardButton("🚫 Блокування", callback_data="admin_block_user"),
        types.InlineKeyboardButton("💰 Транзакції комісій", callback_data="admin_commissions") # NEW
    )
    bot.send_message(message.chat.id, "🔧 *Адмін-панель*", reply_markup=markup, parse_mode='Markdown')

# --- 10. Обробники текстових повідомлень та кнопок меню ---
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'location']) # NEW: Додано location
@error_handler
def handle_text_messages(message):
    chat_id = message.chat.id
    user_text = message.text if message.content_type == 'text' else ""

    if is_user_blocked(chat_id):
        bot.send_message(chat_id, "❌ Ваш акаунт заблоковано.")
        return
    
    save_user(message) # Оновлюємо активність користувача

    # Обробка стану додавання товару
    if chat_id in user_data and user_data[chat_id].get('step'):
        current_step = user_data[chat_id]['step']
        if message.content_type == 'text':
            if user_text == "❌ Скасувати додавання":
                del user_data[chat_id]
                bot.send_message(chat_id, "Додавання товару скасовано.", reply_markup=main_menu_markup)
                return
            # TODO: Перенести логіку process_product_input сюди або викликати її
            # Наприклад: process_product_step(message)
            bot.send_message(chat_id, f"Обробка кроку: {current_step} для тексту: {user_text}") # Заглушка
            return # Важливо, щоб не перейти до AI обробки
        elif message.content_type == 'photo' and current_step == 'waiting_photos':
            # TODO: Перенести логіку process_product_photo сюди
            bot.send_message(chat_id, f"Обробка фото для кроку: {current_step}") # Заглушка
            return
        elif message.content_type == 'location' and current_step == 'waiting_location': # NEW
            # TODO: Обробка геолокації для товару
            process_product_location(message)
            return
        # Якщо тип контенту не відповідає очікуваному на поточному кроці
        bot.send_message(chat_id, "Будь ласка, дотримуйтесь інструкцій для поточного кроку або натисніть '❌ Скасувати додавання'.")
        return


    # Обробка кнопок головного меню
    if user_text == "📦 Додати товар":
        start_add_product_flow(message)
    elif user_text == "📋 Мої товари":
        # TODO: Реалізувати функцію send_my_products(message)
        bot.send_message(chat_id, "Функція 'Мої товари' в розробці.")
    elif user_text == "❓ Допомога":
        send_help_message(message)
    elif user_text == "💰 Комісія": # NEW
        send_commission_info(message)
    elif user_text == "📺 Наш канал":
        # TODO: Реалізувати функцію send_channel_link(message)
        bot.send_message(chat_id, f"Посилання на наш канал: [Канал](https://t.me/c/{str(CHANNEL_ID)[4:]})", parse_mode='Markdown') # Приклад
    # NEW: Якщо це не команда і не кнопка меню, і користувач не в процесі додавання товару - передаємо AI
    elif user_text: # Тільки якщо є текст
        # TODO: Зберігати історію переписки для кращого контексту AI
        # conversation_history = get_conversation_history(chat_id)
        # ai_reply = get_ai_response(user_text, conversation_history)
        ai_reply = get_ai_response(user_text) # Поки без історії
        bot.send_message(chat_id, f"🤖 Ілон думає...\n{ai_reply}")
        # TODO: Зберегти відповідь AI в conversations
    elif message.content_type == 'photo':
        bot.send_message(chat_id, "Я отримав ваше фото, але не знаю, що з ним робити поза процесом додавання товару. 🤔")
    elif message.content_type == 'location':
        bot.send_message(chat_id, f"Я бачу вашу геоточку: {message.location.latitude}, {message.location.longitude}. Як я можу її використати?")
    # else: # Якщо не текстове повідомлення і не оброблено вище
        # bot.send_message(chat_id, "Я не зрозумів ваш запит. Спробуйте використати кнопки меню.")


# --- 11. Потік додавання товару ---
ADD_PRODUCT_STEPS = {
    1: {'name': 'waiting_name', 'prompt': "📝 *Крок 1/5: Назва товару*\n\nВведіть назву (наприклад, 'iPhone 13 Pro Max 256GB Sierra Blue'):", 'next_step': 2},
    2: {'name': 'waiting_price', 'prompt': "💰 *Крок 2/5: Ціна*\n\nВведіть ціну (наприклад, '25000 грн', '700 USD', 'Договірна'):", 'next_step': 3},
    3: {'name': 'waiting_photos', 'prompt': "📸 *Крок 3/5: Фотографії*\n\nНадішліть до 5 фотографій товару (кожне окремим повідомленням). Коли закінчите, натисніть 'Далі'.", 'next_step': 4, 'allow_skip': True, 'skip_button': 'Пропустити фото'},
    4: {'name': 'waiting_location', 'prompt': "📍 *Крок 4/5: Геолокація (необов'язково)*\n\nНадішліть вашу геолокацію для зручності покупців або натисніть 'Пропустити'.", 'next_step': 5, 'content_type': 'location', 'allow_skip': True, 'skip_button': 'Пропустити геолокацію'}, # NEW
    5: {'name': 'waiting_description', 'prompt': "✍️ *Крок 5/5: Опис*\n\nНадайте детальний опис (стан, комплектація, особливості):", 'next_step': 'confirm'}
}

cancel_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
cancel_markup.add(types.KeyboardButton("❌ Скасувати додавання"))

@error_handler
def start_add_product_flow(message):
    chat_id = message.chat.id
    # TODO: Додати перевірку на кількість активних оголошень від користувача
    user_data[chat_id] = {'step_number': 1, 'data': {'photos': [], 'geolocation': None}} # Ініціалізація
    send_product_step_message(chat_id)

@error_handler
def send_product_step_message(chat_id):
    current_step_number = user_data[chat_id]['step_number']
    step_config = ADD_PRODUCT_STEPS[current_step_number]
    user_data[chat_id]['step'] = step_config['name'] # Встановлюємо поточний крок для обробника

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if step_config.get('skip_button'):
        markup.add(types.KeyboardButton(step_config['skip_button']))
    markup.add(types.KeyboardButton("❌ Скасувати додавання"))
    
    bot.send_message(chat_id, step_config['prompt'], parse_mode='Markdown', reply_markup=markup)

# TODO: Необхідно реалізувати process_product_step(message), який буде викликатися з handle_text_messages
# Ця функція має обробляти введення на кожному кроці, валідувати дані, зберігати їх в user_data[chat_id]['data']
# та переходити до наступного кроку через send_product_step_message або завершувати процес.

# Приклад обробки одного кроку (інші аналогічно)
# def process_product_step(message):
#     chat_id = message.chat.id
#     if chat_id not in user_data or 'step_number' not in user_data[chat_id]: return
#
#     current_step_number = user_data[chat_id]['step_number']
#     step_config = ADD_PRODUCT_STEPS[current_step_number]
#     current_data_key = step_config['name'].replace('waiting_', '') # 'name', 'price', etc.
#
#     if message.text == step_config.get('skip_button'):
#         # Обробка пропуску кроку
#         user_data[chat_id]['step_number'] = step_config['next_step']
#         send_product_step_message(chat_id)
#         return
#
#     # Валідація та збереження даних
#     if step_config['name'] == 'waiting_name':
#         user_data[chat_id]['data']['product_name'] = message.text # Приклад
#     # ... інші кроки ...
#
#     # Перехід до наступного кроку
#     if step_config['next_step'] == 'confirm':
#         # TODO: показати підтвердження та відправити на модерацію
#         confirm_and_send_for_moderation(chat_id)
#     else:
#         user_data[chat_id]['step_number'] = step_config['next_step']
#         send_product_step_message(chat_id)

@error_handler
def process_product_location(message): # NEW
    chat_id = message.chat.id
    if chat_id in user_data and user_data[chat_id]['step'] == 'waiting_location':
        user_data[chat_id]['data']['geolocation'] = {
            'latitude': message.location.latitude,
            'longitude': message.location.longitude
        }
        bot.send_message(chat_id, "✅ Геолокацію додано.")
        # Перехід до наступного кроку
        current_step_number = user_data[chat_id]['step_number']
        next_step_number = ADD_PRODUCT_STEPS[current_step_number]['next_step']
        user_data[chat_id]['step_number'] = next_step_number
        send_product_step_message(chat_id)
    else:
        bot.send_message(chat_id, "Будь ласка, надсилайте геолокацію тільки на відповідному кроці.")


# --- 12. Допоміжні повідомлення ---
@error_handler
def send_help_message(message):
    help_text = (
        "🆘 *Довідка*\n\n"
        "🤖 Я ваш AI-помічник для купівлі та продажу. Ви можете:\n"
        "📦 *Додати товар* - створити оголошення.\n"
        "📋 *Мої товари* - переглянути ваші активні та продані товари.\n"
        "💰 *Комісія* - інформація про комісійні збори.\n"
        "📺 *Наш канал* - переглянути всі актуальні пропозиції.\n\n"
        "🗣️ *Спілкування:* Просто пишіть мені ваші запитання або пропозиції, і мій вбудований AI спробує вам допомогти!\n\n"
        "Якщо виникли технічні проблеми, зверніться до адміністратора." # TODO: Додати контакт адміна
    )
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown', reply_markup=main_menu_markup)

@error_handler
def send_commission_info(message): # NEW
    # TODO: Отримувати актуальну ставку комісії з налаштувань або БД
    commission_rate_percent = 10 # Наприклад, 10%
    text = (
        f"💰 *Інформація про комісію*\n\n"
        f"За успішний продаж товару через нашого бота стягується комісія у розмірі **{commission_rate_percent}%** від кінцевої ціни продажу.\n\n"
        f"Після того, як ви позначите товар як 'Продано', система розрахує суму комісії, і ви отримаєте інструкції щодо її сплати.\n\n"
        f"Реквізити для сплати комісії (Monobank):\n`{MONOBANK_CARD_NUMBER}`\n\n"
        f"Будь ласка, сплачуйте комісію вчасно, щоб уникнути обмежень на використання бота.\n\n"
        f"Детальніше про ваші поточні нарахування та сплати можна буде дізнатися в розділі 'Мої товари' (в розробці)."
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=main_menu_markup)


# --- 13. Обробники Callback Query (для адмін-панелі та модерації) ---
# TODO: Реалізувати callback_inline, handle_admin_callbacks, handle_product_moderation_callbacks
# Вони мають бути схожими на ті, що у вашому попередньому коді, але адаптовані до нової структури БД.
# Основні дії: approve_PRODUCTID, reject_PRODUCTID, sold_PRODUCTID, admin_stats, admin_pending, etc.

# Приклад обробника для схвалення товару
@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_'))
@error_handler
def handle_approve_product(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "Тільки для адміністраторів.")
        return
    
    try:
        product_id = int(call.data.split('_')[1])
    except (IndexError, ValueError):
        logger.error(f"Неправильний формат callback_data для approve: {call.data}")
        bot.answer_callback_query(call.id, "Помилка ID товару.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM products WHERE id = ? AND status = 'pending'", (product_id,))
        product = cursor.fetchone()

        if not product:
            bot.answer_callback_query(call.id, "Товар не знайдено або вже оброблено.")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            return

        # TODO: Логіка публікації в канал
        # 1. Сформувати текст повідомлення для каналу (з фото, описом, ціною, геолокацією, контактом продавця)
        # 2. Надіслати в CHANNEL_ID
        # 3. Отримати channel_message_id
        # 4. Оновити статус товару в БД на 'approved', записати channel_message_id, moderator_id, moderated_at
        # 5. Сповістити продавця
        # 6. Оновити повідомлення адміністратора (видалити кнопки, написати "Схвалено")

        # Заглушка
        cursor.execute("UPDATE products SET status = 'approved', moderator_id = ?, moderated_at = CURRENT_TIMESTAMP WHERE id = ?",
                       (ADMIN_CHAT_ID, product_id))
        conn.commit()
        
        bot.answer_callback_query(call.id, f"Товар ID {product_id} схвалено (заглушка).")
        bot.edit_message_text(f"Товар ID {product_id} ({product['product_name']}) СХВАЛЕНО.",
                              call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(product['seller_chat_id'], f"Ваш товар '{product['product_name']}' схвалено та буде опубліковано!")
        # log_statistics('product_approved', ADMIN_CHAT_ID, product_id)

    except Exception as e:
        logger.error(f"Помилка при схваленні товару {product_id}: {e}")
        bot.answer_callback_query(call.id, "Помилка при схваленні.")
    finally:
        conn.close()


# --- XX. Функції, які потрібно буде реалізувати (TODO) ---
# def process_product_input(message): ... (обробка кроків додавання товару)
# def confirm_and_send_for_moderation(chat_id): ...
# def send_my_products(message): ...
# def send_channel_link(message): ...
# def handle_admin_callbacks(call): ...
# def handle_product_moderation_callbacks(call): ... (approve, reject, sold)
# def handle_user_block_callbacks(call): ...
# def calculate_and_record_commission(product_id, final_price): ...
# def notify_seller_about_commission(seller_chat_id, commission_amount, product_name): ...
# def get_conversation_history(chat_id, product_id=None, limit=10): ... (для AI)
# def save_ai_conversation_message(chat_id, text, sender_type, product_id=None): ... (для AI)


# --- Запуск бота ---
if __name__ == '__main__':
    logger.info("Запуск ініціалізації БД...")
    init_db()
    logger.info("Бот запускається...")
    try:
        bot.infinity_polling(logger_level=logging.DEBUG, skip_pending=True) # skip_pending - щоб не обробляти старі повідомлення при перезапуску
    except Exception as e:
        logger.critical(f"Критична помилка при запуску polling: {e}", exc_info=True)

