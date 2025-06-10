import os
import telebot
from telebot import types
import logging
from datetime import datetime, timedelta, timezone
import re
import json
import requests
from dotenv import load_dotenv

# Імпорти для Webhook (Flask)
from flask import Flask, request

# Імпорти для PostgreSQL (замість sqlite3)
import psycopg2
from psycopg2 import sql as pg_sql
from psycopg2 import extras

# Завантажуємо змінні оточення з файлу .env
load_dotenv()

# --- 1. Конфігурація Бота ---
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
MONOBANK_CARD_NUMBER = os.getenv('MONOBANK_CARD_NUMBER')
RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

WEBHOOK_URL = os.getenv('WEBHOOK_URL')
DATABASE_URL = os.getenv('DATABASE_URL')

# Базова перевірка наявності основних змінних
if not TOKEN:
    print("Помилка: TELEGRAM_BOT_TOKEN не встановлено у змінних оточення. Вихід.")
    exit(1)
if not RAPIDAPI_KEY:
    print("Помилка: RAPIDAPI_KEY не встановлено у змінних оточення. Вихід.")
    exit(1)
if not DATABASE_URL:
    print("Помилка: DATABASE_URL не встановлено у змінних оточення. База даних не працюватиме. Вихід.")
    exit(1)

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- 2. Конфігурація логування ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- 3. Підключення та ініціалізація Бази Даних (PostgreSQL) ---
def get_db_connection():
    """Встановлює з'єднання з базою даних PostgreSQL."""
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
        return conn
    except Exception as e:
        logger.error(f"Помилка підключення до бази даних: {e}", exc_info=True)
        return None

def init_db():
    """Ініціалізує таблиці бази даних, якщо вони не існують."""
    conn = None
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            # Таблиця users
            cur.execute(pg_sql.SQL("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    is_blocked BOOLEAN DEFAULT FALSE,
                    blocked_by BIGINT,
                    blocked_at TIMESTAMP WITH TIME ZONE,
                    commission_paid REAL DEFAULT 0,
                    commission_due REAL DEFAULT 0,
                    last_activity TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    joined_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
            # Таблиця products
            cur.execute(pg_sql.SQL("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    seller_chat_id BIGINT NOT NULL,
                    seller_username TEXT,
                    product_name TEXT NOT NULL,
                    price TEXT NOT NULL,
                    description TEXT NOT NULL,
                    photos TEXT, -- Зберігатиметься як JSON рядок
                    geolocation TEXT, -- Зберігатиметься як JSON рядок
                    status TEXT DEFAULT 'pending', -- pending, approved, rejected, sold, expired
                    commission_rate REAL DEFAULT 0.10,
                    commission_amount REAL DEFAULT 0,
                    moderator_id BIGINT,
                    moderated_at TIMESTAMP WITH TIME ZONE,
                    admin_message_id BIGINT,
                    channel_message_id BIGINT,
                    views INTEGER DEFAULT 0,
                    promotion_ends_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (seller_chat_id) REFERENCES users (chat_id)
                );
            """))
            # Таблиця conversations (для AI)
            cur.execute(pg_sql.SQL("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id SERIAL PRIMARY KEY,
                    user_chat_id BIGINT NOT NULL,
                    product_id INTEGER,
                    message_text TEXT,
                    sender_type TEXT, -- 'user' або 'ai'
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_chat_id) REFERENCES users (chat_id),
                    FOREIGN KEY (product_id) REFERENCES products (id)
                );
            """))
            # Таблиця commission_transactions
            cur.execute(pg_sql.SQL("""
                CREATE TABLE IF NOT EXISTS commission_transactions (
                    id SERIAL PRIMARY KEY,
                    product_id INTEGER NOT NULL,
                    seller_chat_id BIGINT NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT DEFAULT 'pending_payment', -- pending_payment, paid, cancelled
                    payment_details TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    paid_at TIMESTAMP WITH TIME ZONE,
                    FOREIGN KEY (product_id) REFERENCES products (id),
                    FOREIGN KEY (seller_chat_id) REFERENCES users (chat_id)
                );
            """))
            # Таблиця statistics
            cur.execute(pg_sql.SQL("""
                CREATE TABLE IF NOT EXISTS statistics (
                    id SERIAL PRIMARY KEY,
                    action TEXT NOT NULL,
                    user_id BIGINT,
                    product_id INTEGER,
                    details TEXT,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
            conn.commit()
            logger.info("Таблиці бази даних успішно ініціалізовано або вже існують.")
    except Exception as e:
        logger.critical(f"Критична помилка ініціалізації бази даних: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --- 4. Зберігання даних користувача для багатошагових процесів (залишаємо для потенційного використання) ---
user_data = {}

# --- 5. Декоратор для обробки помилок (залишаємо) ---
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
                bot.send_message(ADMIN_CHAT_ID, f"🚨 Критична помилка в боті!\nФункція: {func.__name__}\nПомилка: {e}\nДивіться деталі в логах Render.")
                if chat_id_to_notify != ADMIN_CHAT_ID:
                    bot.send_message(chat_id_to_notify, "😔 Вибачте, сталася внутрішня помилка. Адміністратор вже сповіщений.")
            except Exception as e_notify:
                logger.error(f"Не вдалося надіслати повідомлення про помилку: {e_notify}")
    return wrapper

# --- 6. Мінімальні заглушки для функцій, які можуть викликатися з обробників ---
@error_handler
def save_user(message_or_user):
    logger.info(f"DEBUG_STUB: save_user called for {message_or_user.from_user.id if hasattr(message_or_user, 'from_user') else message_or_user.id}")
    pass # Реальна логіка save_user поки відключена

@error_handler
def is_user_blocked(chat_id):
    logger.info(f"DEBUG_STUB: is_user_blocked called for {chat_id}")
    return False # Завжди повертаємо False для тестування

@error_handler
def log_statistics(action, user_id=None, product_id=None, details=None):
    logger.info(f"DEBUG_STUB: log_statistics called for action: {action}")
    pass # Реальна логіка log_statistics поки відключена

# --- 9. Обробники команд (мінімальні для діагностики) ---
@bot.message_handler(commands=['start'])
@error_handler
def send_welcome(message):
    logger.info(f"DEBUG: send_welcome handler CALLED for chat_id: {message.chat.id}, message_text: '{message.text}'")
    chat_id = message.chat.id
    
    # Викликаємо заглушку save_user
    save_user(message)
    log_statistics('start', chat_id)

    welcome_text = "Привіт! Я ваш SellerBot. Ви надіслали /start."
    
    logger.info(f"DEBUG: Attempting to send welcome message to chat_id: {chat_id}")
    bot.send_message(chat_id, welcome_text)
    logger.info(f"DEBUG: Welcome message sent (or attempted) to chat_id: {chat_id}")

@bot.message_handler(commands=['test'])
@error_handler
def send_test_message(message):
    logger.info(f"DEBUG: send_test_message handler CALLED for chat_id: {message.chat.id}, message_text: '{message.text}'")
    chat_id = message.chat.id
    test_text = "Це тестове повідомлення. Бот працює!"
    logger.info(f"DEBUG: Attempting to send test message to chat_id: {chat_id}")
    bot.send_message(chat_id, test_text)
    logger.info(f"DEBUG: Test message sent (or attempted) to chat_id: {chat_id}")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'location'])
@error_handler
def handle_all_messages(message):
    logger.info(f"DEBUG: handle_all_messages handler CALLED for chat_id: {message.chat.id}, type: {message.content_type}, text: '{message.text[:50] if message.text else 'N/A'}'")
    chat_id = message.chat.id
    
    # Викликаємо заглушку save_user
    save_user(message)

    if message.content_type == 'text':
        response_text = f"Я отримав ваше повідомлення: '{message.text}'. Дякую!"
    elif message.content_type == 'photo':
        response_text = "Я отримав ваше фото. Поки що не знаю, що з ним робити."
    elif message.content_type == 'location':
        response_text = f"Я отримав вашу геолокацію: {message.location.latitude}, {message.location.longitude}."
    else:
        response_text = "Я отримав невідомий тип повідомлення."
        
    logger.info(f"DEBUG: Attempting to send response to chat_id: {chat_id}")
    bot.send_message(chat_id, response_text)
    logger.info(f"DEBUG: Response sent (or attempted) to chat_id: {chat_id}")


# --- 18. Запуск бота та налаштування вебхука для Render ---

logger.info("Запуск ініціалізації БД...")
init_db()

# Цей блок повинен виконуватися тільки один раз при старті додатка на Render
if WEBHOOK_URL and TOKEN:
    try:
        # Важливо: видаляємо попередній вебхук перед встановленням нового
        bot.remove_webhook()
        full_webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
        bot.set_webhook(url=full_webhook_url)
        logger.info(f"Webhook встановлено на: {full_webhook_url}")
    except Exception as e:
        logger.critical(f"Критична помилка встановлення webhook: {e}", exc_info=True)
        logger.error("Бот не буде отримувати оновлення від Telegram через помилку вебхука.")
        # Якщо вебхук не встановлено, бот не може працювати, тому краще вийти
        exit(1)
else:
    logger.critical("WEBHOOK_URL або TELEGRAM_BOT_TOKEN не встановлено. Бот не може працювати в режимі webhook. Вихід.")
    exit(1)

# Це основна точка входу для Flask-додатка на Render
if __name__ == '__main__':
    logger.info("Запуск Flask-додатка локально...")
    port = int(os.environ.get("PORT", 5000))
    # Flask app.run - для локального запуску та дебагу
    # На Render це буде gunicorn bot:app
    app.run(host="0.0.0.0", port=port, debug=True)

# Обробник вебхуків для Flask
@app.route(f'/{TOKEN}', methods=['POST'])
@error_handler
def webhook_receiver():
    """Обробляє вхідні оновлення від Telegram."""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        # Логування отриманого оновлення для детальної діагностики
        if update.message:
            logger.info(f"DEBUG: Webhook received message update from {update.message.chat.id}, text: '{update.message.text[:50] if update.message.text else 'N/A'}'")
        elif update.callback_query:
            logger.info(f"DEBUG: Webhook received callback query update from {update.callback_query.message.chat.id}, data: '{update.callback_query.data}'")
        else:
            logger.info(f"DEBUG: Webhook received unknown update type: {update}")

        # Основна функція pyTelegramBotAPI для обробки оновлень
        bot.process_new_updates([update])
        logger.info(f"DEBUG: pyTelegramBotAPI finished processing update. Returning 200 OK.")
        return '!', 200
    else:
        logger.warning("Received non-JSON request on webhook path. Ignoring.")
        return 'Hello from bot webhook!', 200

