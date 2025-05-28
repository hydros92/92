import sqlite3
import os
import telebot
from telebot import types
import logging
from datetime import datetime, timedelta
import re
import json
import requests
from dotenv import load_dotenv
from flask import Flask, request # Імпортуємо Flask
import time # Додано для time.sleep

load_dotenv()

# --- 1. Конфігурація Бота ---
# Рекомендується використовувати змінні середовища для безпеки та легкості конфігурації.
# Якщо змінні середовища не встановлені, використовуються значення за замовчуванням (тільки для розробки!).
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8039977178:AAGS-GbH-lhljGGG6OgJ2iMU_ncB-JzeOvU') # ЗАМІНІТЬ ЦЕЙ ТОКЕН НА ВАШ АКТУАЛЬНИЙ!
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '8184456641')) # ЗАМІНІТЬ НА ВАШ CHAT_ID АДМІНІСТРАТОРА!
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '-1002535586055')) # ЗАМІНІТЬ НА ID ВАШОГО КАНАЛУ!
MONOBANK_CARD_NUMBER = os.getenv('MONOBANK_CARD_NUMBER', '4441 1111 5302 1484') # ЗАМІНІТЬ НА НОМЕР КАРТКИ!

# XAI (Grok) API налаштування
XAI_API_KEY = os.getenv('XAI_API_KEY', 'xai-ZxqajHNVS3wMUbbsxJvJAXrRuv13bd6O3Imdl5S1bfAjBQD7qrlio2kEltsg5E3mSJByGoSgq1vJgQgk')
XAI_API_URL = os.getenv('XAI_API_URL', 'https://api.x.ai/v1/chat/completions')

# Heroku Webhook налаштування
HEROKU_APP_NAME = os.getenv('NaProDash') # Назва вашого додатку на Heroku
if not HEROKU_APP_NAME:
    logger.warning("Змінна середовища 'HEROKU_APP_NAME' не встановлена. Вебхук може не працювати коректно.")
    # Використовуємо заглушку для локального тестування, якщо HEROKU_APP_NAME не встановлено
    WEBHOOK_URL_BASE = "https://NaProDash.herokuapp.com" # Замініть на реальний URL для локального тестування
else:
    WEBHOOK_URL_BASE = f"https://NaProDash.herokuapp.com"

WEBHOOK_URL_PATH = f"/{https://NaProDash.herokuapp.com}/" # Шлях, на який Telegram надсилатиме оновлення

# --- 2. Налаштування логування ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler() # Додано для виводу логів в консоль Heroku
    ]
)
# Ініціалізуємо об'єкт logger після налаштування basicConfig
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__) # Ініціалізуємо Flask додаток

# --- 3. Змінні станів для багатошагових процесів ---
# Використовується для зберігання тимчасових даних під час додавання товару.
# Формат: {chat_id: {'step_number': 1, 'data': {'product_name': '', ...}}}
user_data = {}

# --- 4. Управління базою даних (SQLite) ---
DB_NAME = 'seller_bot.db'

def get_db_connection():
    """Повертає з'єднання з базою даних SQLite."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # Дозволяє отримувати доступ до стовпців за назвою
    return conn

def init_db():
    """Ініціалізує базу даних, створюючи необхідні таблиці та оновлюючи схему."""
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
            blocked_by INTEGER,
            blocked_at TIMESTAMP,
            commission_paid REAL DEFAULT 0,
            commission_due REAL DEFAULT 0,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            -- user_status TEXT DEFAULT 'idle' -- Це поле буде додано окремо, якщо його немає
        )
    ''')

    # Додаємо колонку user_status, якщо її немає
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN user_status TEXT DEFAULT 'idle'")
        logger.info("Колонка 'user_status' додана до таблиці 'users'.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            logger.info("Колонка 'user_status' вже існує в таблиці 'users'.")
        else:
            logger.error(f"Помилка при додаванні колонки 'user_status': {e}")


    # Таблиця товарів
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_chat_id INTEGER NOT NULL,
            seller_username TEXT,
            product_name TEXT NOT NULL,
            price TEXT NOT NULL,
            description TEXT NOT NULL,
            photos TEXT,
            geolocation TEXT,
            status TEXT DEFAULT 'pending', -- pending, approved, rejected, sold, expired
            commission_rate REAL DEFAULT 0.10,
            commission_amount REAL DEFAULT 0,
            moderator_id INTEGER,
            moderated_at TIMESTAMP,
            admin_message_id INTEGER,
            channel_message_id INTEGER,
            views INTEGER DEFAULT 0,
            promotion_ends_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_chat_id) REFERENCES users (chat_id)
        )
    ''')

    # Таблиця для переписок з AI
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_chat_id INTEGER NOT NULL,
            product_id INTEGER,
            message_text TEXT,
            sender_type TEXT, -- 'user' або 'ai'
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_chat_id) REFERENCES users (chat_id),
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    ''')
    
    # Таблиця для транзакцій комісій
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commission_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            seller_chat_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending_payment', -- pending_payment, paid, cancelled
            payment_details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products (id),
            FOREIGN KEY (seller_chat_id) REFERENCES users (chat_id)
        )
    ''')

    # Таблиця статистики
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            user_id INTEGER,
            product_id INTEGER,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблиця для FAQ (Бази знань)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS faq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT UNIQUE,
            answer TEXT
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("База даних ініціалізована або вже існує.")

# --- 5. Декоратор для обробки помилок ---
def error_handler(func):
    """Декоратор для централізованої обробки помилок у функціях бота."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Помилка в {func.__name__}: {e}", exc_info=True)
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

# --- 6. Функції роботи з користувачами та загальні допоміжні функції ---
@error_handler
def save_user(message_or_user):
    """Зберігає або оновлює інформацію про користувача в базі даних."""
    user = None
    chat_id = None

    if isinstance(message_or_user, types.Message):
        user = message_or_user.from_user
        chat_id = message_or_user.chat.id
    elif isinstance(message_or_user, types.User):
        user = message_or_user
        chat_id = user.id
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
    """Перевіряє, чи заблокований користувач."""
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

@error_handler
def set_user_block_status(admin_id, chat_id, status):
    """Встановлює статус блокування для користувача."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if status: # Блокування
            cursor.execute("UPDATE users SET is_blocked = TRUE, blocked_by = ?, blocked_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
                           (admin_id, chat_id))
        else: # Розблокування
            cursor.execute("UPDATE users SET is_blocked = FALSE, blocked_by = NULL, blocked_at = NULL WHERE chat_id = ?",
                           (chat_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Помилка при встановленні статусу блокування для користувача {chat_id}: {e}")
        return False
    finally:
        conn.close()

@error_handler
def generate_hashtags(description, num_hashtags=5):
    """Генерує хештеги з опису товару."""
    words = re.findall(r'\b\w+\b', description.lower())
    stopwords = set(['я', 'ми', 'ти', 'ви', 'він', 'вона', 'воно', 'вони', 'це', 'що',
                     'як', 'де', 'коли', 'а', 'і', 'та', 'або', 'чи', 'для', 'з', 'на',
                     'у', 'в', 'до', 'від', 'по', 'за', 'при', 'про', 'між', 'під', 'над',
                     'без', 'через', 'дуже', 'цей', 'той', 'мій', 'твій', 'наш', 'ваш',
                     'продам', 'продамся', 'продати', 'продаю', 'продаж', 'купити', 'куплю',
                     'бу', 'новий', 'стан', 'модель', 'см', 'кг', 'грн', 'uah', 'usd', 'eur', 'один', 'два', 'три', 'чотири', 'пять', 'шість', 'сім', 'вісім', 'девять', 'десять'])
    filtered_words = [word for word in words if len(word) > 2 and word not in stopwords]
    unique_words = list(set(filtered_words))
    hashtags = ['#' + word for word in unique_words[:num_hashtags]]
    return " ".join(hashtags) if hashtags else ""

@error_handler
def log_statistics(action, user_id=None, product_id=None, details=None):
    """Логує дії користувачів та адміністраторів для статистики."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO statistics (action, user_id, product_id, details)
            VALUES (?, ?, ?, ?)
        ''', (action, user_id, product_id, details))
        conn.commit()
    except Exception as e:
        logger.error(f"Помилка логування статистики: {e}")
    finally:
        conn.close()

# --- Функції для управління статусом користувача ---
def get_user_current_status(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_status FROM users WHERE chat_id = ?", (chat_id,))
    status = cursor.fetchone()
    conn.close()
    return status[0] if status else 'idle'

def set_user_status(chat_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET user_status = ? WHERE chat_id = ?", (status, chat_id))
    conn.commit()
    conn.close()

# --- Функції для роботи з FAQ ---
def add_faq_entry(question, answer):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO faq (question, answer) VALUES (?, ?)", (question, answer))
        conn.commit()
        return True
    except sqlite3.IntegrityError: # Якщо питання вже існує
        return False
    finally:
        conn.close()

def get_faq_answer(question_text):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Пошук за підстрінгом, щоб знайти релевантні питання
    # Використовуємо LOWER() для регістронезалежного пошуку
    cursor.execute("SELECT answer FROM faq WHERE LOWER(question) LIKE ?", (f'%{question_text.lower()}%',))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def delete_faq_entry(faq_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM faq WHERE id = ?", (faq_id,))
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0

def get_all_faq_entries():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, question, answer FROM faq")
    entries = cursor.fetchall()
    conn.close()
    return entries

# --- 7. Grok AI інтеграція ---
@error_handler
def get_grok_response(prompt, conversation_history=None):
    """
    Отримання відповіді від Grok AI.
    Якщо API ключ або URL не встановлені/некоректні, генерує заглушку.
    """
    if not XAI_API_KEY or XAI_API_KEY == 'YOUR_XAI_API_KEY_HERE':
        logger.warning("XAI API ключ не налаштований. Використовується заглушка.")
        return generate_elon_style_response(prompt)
    
    # FIX: Validate XAI_API_URL
    if not XAI_API_URL or not XAI_API_URL.startswith('http'):
        logger.error(f"XAI API URL некоректний: '{XAI_API_URL}'. Він повинен починатися з 'http://' або 'https://'. Використовується заглушка.")
        return generate_elon_style_response(prompt)

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }

    # Покращений системний промпт для AI
    system_prompt = {
        "role": "system",
        "content": (
            "Ти — клієнторієнтований AI-асистент для Telegram бота продажу товарів. "
            "Твоя мета — допомагати користувачам знаходити інформацію про товари, "
            "відповідати на їхні запитання, заохочувати до покупки, "
            "і, за потреби, допомогти у переговорах щодо ціни. "
            "Завжди будь ввічливим, інформативним, та намагайся завершити розмову продажем. "
            "Якщо запитання стосується чогось, крім продажу, або виходить за рамки твоїх можливостей, "
            "запропонуй звернутися до 'живого' оператора, використовуючи фразу 'Зв'язатися з адміном'. "
            "Не вигадуй інформацію про товари, якої немає. "
            "Якщо користувач хоче обговорити ціну, можеш запропонувати 'зробити пропозицію продавцю' або 'зв'язатися з продавцем для обговорення'. "
            "Використовуй позитивний та привітний тон. Відповідай українською мовою."
        )
    }

    messages = [system_prompt]
    if conversation_history:
        messages.extend(conversation_history)
    
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": "grok-1", # Використовуємо grok-1, якщо доступно, інакше можна спробувати інші
        "messages": messages,
        "max_tokens": 300,
        "temperature": 0.7
    }

    try:
        response = requests.post(XAI_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        if data.get("choices") and len(data["choices"]) > 0:
            content = data["choices"][0]["message"]["content"]
            logger.info(f"Grok відповідь отримана: {content[:100]}...")
            return content.strip()
        else:
            logger.error(f"Неочікувана структура відповіді від Grok: {data}")
            return generate_elon_style_response(prompt)

    except requests.exceptions.RequestException as e:
        logger.error(f"Помилка HTTP запиту до Grok API: {e}")
        return generate_elon_style_response(prompt)
    except Exception as e:
        logger.error(f"Загальна помилка при отриманні відповіді від Grok: {e}")
        return generate_elon_style_response(prompt)

def generate_elon_style_response(prompt):
    """
    Генерує відповіді в стилі Ілона Маска як заглушка, коли AI API недоступне.
    """
    responses = [
        "🚀 Гм, цікаве питання! Як і з SpaceX, тут потрібен системний підхід. Що саме вас цікавить?",
        "⚡ Очевидно! Як кажуть в Tesla - простота це вершина складності. Давайте розберемося.",
        "🤖 *думає як Neuralink* Ваше питання активувало мої нейрони! Ось що я думаю...",
        "🎯 Як і з X (колишній Twitter), іноді краще бути прямолінійним. Скажіть конкретніше?",
        "🔥 Хмм, це нагадує мені час, коли ми запускали Falcon Heavy. Складно, але можливо!",
        "💡 Ах, класика! Як і з Hyperloop - спочатку здається неможливим, потім очевидним.",
        "🌟 Цікаво! У Boring Company ми б просто прокопали тунель під проблемою. А тут...",
        "⚡ Логічно! Як завжди кажу - якщо щось не вибухає, значить недостатньо намагаєшся 😄"
    ]
    
    import random
    base_response = random.choice(responses)
    
    prompt_lower = prompt.lower()
    if any(word in prompt_lower for word in ['ціна', 'вартість', 'гроші']):
        return f"{base_response}\n\n💰 Щодо ціни - як в Tesla, важлива якість, а не тільки вартість! Можливо, варто 'Зв'язатися з адміном' для обговорення?"
    elif any(word in prompt_lower for word in ['фото', 'картинка', 'зображення']):
        return f"{base_response}\n\n📸 Фото - це як перший етап ракети, без них нікуди!"
    elif any(word in prompt_lower for word in ['доставка', 'відправка']):
        return f"{base_response}\n\n🚚 Доставка? Якби у нас був Hyperloop, це б зайняло хвилини! 😉"
    elif any(word in prompt_lower for word in ['продаж', 'купівля']):
        return f"{base_response}\n\n🤝 Продаж - це як запуск ракети: підготовка, виконання, успіх!"
    
    return base_response

@error_handler
def save_conversation(chat_id, message_text, sender_type, product_id=None):
    """Зберігає повідомлення в історії розмов для контексту AI."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO conversations (user_chat_id, product_id, message_text, sender_type)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, product_id, message_text, sender_type))
        conn.commit()
    except Exception as e:
        logger.error(f"Помилка збереження розмови: {e}")
    finally:
        conn.close()

@error_handler
def get_conversation_history(chat_id, limit=5):
    """Отримує історію розмов для контексту AI."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT message_text, sender_type FROM conversations 
            WHERE user_chat_id = ? 
            ORDER BY timestamp DESC LIMIT ?
        ''', (chat_id, limit))
        results = cursor.fetchall()
        
        history = []
        for row in reversed(results):  # Реверс для хронологічного порядку
            role = "user" if row['sender_type'] == 'user' else "assistant"
            history.append({"role": role, "content": row['message_text']})
        
        return history
    except Exception as e:
        logger.error(f"Помилка отримання історії розмов: {e}")
        return []
    finally:
        conn.close()

# --- 8. Клавіатури ---
# Оновлена головна клавіатура
main_menu_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
main_menu_markup.add(types.KeyboardButton("🔥 Продати товар"), types.KeyboardButton("🛒 Мої товари"))
main_menu_markup.add(types.KeyboardButton("❓ Допомога"), types.KeyboardButton("🤖 Запитати AI"))
main_menu_markup.add(types.KeyboardButton("🎁 Персональна пропозиція"), types.KeyboardButton("👨‍💻 Зв'язатися з адміном"))


# --- 9. Обробники команд ---
@bot.message_handler(commands=['start'])
@error_handler
def send_welcome(message):
    """Обробник команди /start."""
    chat_id = message.chat.id
    if is_user_blocked(chat_id):
        bot.send_message(chat_id, "❌ Ваш акаунт заблоковано.")
        return

    save_user(message)
    set_user_status(chat_id, 'idle') # Встановлюємо статус "вільний" при старті
    log_statistics('start', chat_id)

    welcome_text = (
        "🛍️ *Ласкаво просимо до SellerBot!*\n\n"
        "Я ваш розумний помічник для продажу та купівлі товарів. "
        "Мене підтримує Grok AI в стилі Ілона Маска! 🚀\n\n"
        "Що я вмію:\n"
        "📦 Допомагаю створювати оголошення\n"
        "🤝 Веду переговори та домовленості\n"
        "📍 Обробляю геолокацію та фото\n"
        "💰 Слідкую за комісіями\n"
        "🎯 Аналізую ринок та ціни\n\n"
        "Оберіть дію з меню або просто напишіть мені!"
    )
    bot.send_message(chat_id, welcome_text, reply_markup=main_menu_markup, parse_mode='Markdown')

@bot.message_handler(commands=['admin'])
@error_handler
def admin_panel(message):
    """Обробник команди /admin для доступу до адмін-панелі."""
    if message.chat.id != ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "❌ У вас немає прав доступу.")
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        types.InlineKeyboardButton("⏳ На модерації", callback_data="admin_pending"),
        types.InlineKeyboardButton("👥 Користувачі", callback_data="admin_users"),
        types.InlineKeyboardButton("🚫 Блокування", callback_data="admin_block"),
        types.InlineKeyboardButton("💰 Комісії", callback_data="admin_commissions"),
        types.InlineKeyboardButton("🤖 AI Статистика", callback_data="admin_ai_stats"),
        types.InlineKeyboardButton("📚 Керування FAQ", callback_data="admin_faq_menu")
    )
    bot.send_message(message.chat.id, "🔧 *Адмін-панель*", reply_markup=markup, parse_mode='Markdown')

# --- Обробники для AI-чату, чату з людиною та персональної пропозиції ---
@bot.message_handler(func=lambda message: message.text == "🤖 Запитати AI")
@error_handler
def ask_ai_command(message):
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "❌ Ви заблоковані і не можете використовувати цю функцію.")
        return
    set_user_status(message.chat.id, 'ai_chat')
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("❌ Вийти з AI чату"))
    bot.send_message(message.chat.id, "Привіт! Я ваш AI помічник. Задайте мені будь-яке питання про товари, продажі, або просто поспілкуйтесь! Для виходу натисніть кнопку.", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "❌ Вийти з AI чату")
@error_handler
def stop_ai_command(message):
    set_user_status(message.chat.id, 'idle')
    bot.send_message(message.chat.id, "Ви вийшли з режиму AI-чату. Чим ще можу допомогти?",
                     reply_markup=main_menu_markup)

@bot.message_handler(func=lambda message: message.text == "👨‍💻 Зв'язатися з адміном")
@error_handler
def chat_with_human_command(message):
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "❌ Ви заблоковані і не можете використовувати цю функцію.")
        return
    
    set_user_status(message.chat.id, 'waiting_human_operator')
    markup = typ