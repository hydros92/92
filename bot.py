import os
import telebot
from telebot import types
import logging
from datetime import datetime, timedelta, timezone # Keep datetime, timedelta, timezone for completeness if needed later
import re # Keep re for completeness if needed later
import json # Keep json for completeness if needed later
import requests # Keep requests for completeness if needed later
from flask import Flask, request
import psycopg2
from psycopg2 import sql as pg_sql
from psycopg2 import extras
from dotenv import load_dotenv

# Завантажуємо змінні оточення з файлу .env
load_dotenv()

# --- 1. Конфігурація Бота ---
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
# Додаємо захист від ValueError, якщо змінна оточення відсутня
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID')) if os.getenv('ADMIN_CHAT_ID') else 0
CHANNEL_ID = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else 0
DATABASE_URL = os.getenv('DATABASE_URL')
MONOBANK_CARD_NUMBER = os.getenv('MONOBANK_CARD_NUMBER')
RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# --- 2. Конфігурація логування (Визначено раніше для діагностичних логів) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- ДІАГНОСТИЧНІ ЛОГИ ДЛЯ ЗМІННИХ ОТОЧЕННЯ ---
logger.info(f"DIAGNOSTIC: TOKEN loaded: {'<set>' if TOKEN else '<not set>'} (length: {len(TOKEN) if TOKEN else 0})")
logger.info(f"DIAGNOSTIC: WEBHOOK_URL loaded: {'<set>' if WEBHOOK_URL else '<not set>'} (value: {WEBHOOK_URL})")
logger.info(f"DIAGNOSTIC: DATABASE_URL loaded: {'<set>' if DATABASE_URL else '<not set>'}")
logger.info(f"DIAGNOSTIC: ADMIN_CHAT_ID loaded: {ADMIN_CHAT_ID}")
logger.info(f"DIAGNOSTIC: CHANNEL_ID loaded: {CHANNEL_ID}")
logger.info(f"DIAGNOSTIC: RAPIDAPI_KEY loaded: {'<set>' if RAPIDAPI_KEY else '<not set>'}")
logger.info(f"DIAGNOSTIC: GEMINI_API_KEY loaded: {'<set>' if GEMINI_API_KEY else '<not set>'}")
# --- КІНЕЦЬ ДІАГНОСТИЧНИХ ЛОГІВ ---


# Базова перевірка наявності основних змінних
if not TOKEN:
    logger.critical("Помилка: TELEGRAM_BOT_TOKEN не встановлено у змінних оточення. Вихід.")
    exit(1)
if not DATABASE_URL:
    logger.critical("Помилка: DATABASE_URL не встановлено у змінних оточення. База даних не працюватиме. Вихід.")
    exit(1)

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)


# --- 3. Підключення та ініціалізація Бази Даних (Мінімальна заглушка) ---
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
        return conn
    except Exception as e:
        logger.error(f"Помилка підключення до бази даних: {e}", exc_info=True)
        return None

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute(pg_sql.SQL("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT
                );
            """)) # Спрощено, тільки users таблиця
            conn.commit()
            logger.info("Таблиці бази даних успішно ініціалізовано або вже існують.")
    except Exception as e:
        logger.critical(f"Критична помилка ініціалізації бази даних: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --- 4. Декоратор для обробки помилок ---
def error_handler(func):
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
                # Використовуємо ADMIN_CHAT_ID, лише якщо він коректно встановлений
                if ADMIN_CHAT_ID:
                    bot.send_message(ADMIN_CHAT_ID, f"🚨 Критична помилка в боті!\nФункція: {func.__name__}\nПомилка: {e}\nДивіться деталі в логах Render.")
                if chat_id_to_notify and chat_id_to_notify != ADMIN_CHAT_ID:
                    bot.send_message(chat_id_to_notify, "😔 Вибачте, сталася внутрішня помилка. Адміністратор вже сповіщений.")
            except Exception as e_notify:
                logger.error(f"Не вдалося надіслати повідомлення про помилку: {e_notify}")
    return wrapper

# --- 5. Заглушки для БД-операцій ---
@error_handler
def save_user(message_or_user):
    # Забезпечуємо отримання chat_id для коректного логування
    chat_id = None
    if isinstance(message_or_user, types.Message):
        chat_id = message_or_user.from_user.id
        username = message_or_user.from_user.username
        first_name = message_or_user.from_user.first_name
        last_name = message_or_user.from_user.last_name
    elif isinstance(message_or_user, types.User):
        chat_id = message_or_user.id
        username = message_or_user.username
        first_name = message_or_user.first_name
        last_name = message_or_user.last_name
    else:
        logger.warning(f"save_user отримав невідомий тип: {type(message_or_user)}")
        return

    logger.info(f"DEBUG_STUB: save_user called for {chat_id}")
    conn = get_db_connection()
    if not conn: return
    try:
        cur = conn.cursor()
        cur.execute(pg_sql.SQL("""
            INSERT INTO users (chat_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET username = EXCLUDED.username, first_name = EXCLUDED.first_name, 
            last_name = EXCLUDED.last_name;
        """), (chat_id, username, first_name, last_name))
        conn.commit()
        logger.info(f"DEBUG_STUB: User {chat_id} saved/updated in DB.")
    except Exception as e:
        logger.error(f"DEBUG_STUB: Error saving user {chat_id}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

@error_handler
def log_statistics(action, user_id=None, product_id=None, details=None):
    logger.info(f"DEBUG_STUB: log_statistics called for action: {action}, user: {user_id}")
    pass # В цій діагностичній версії не робить запис до БД

# --- 6. Обробники команд (мінімальні для діагностики) ---
@bot.message_handler(commands=['start'])
@error_handler
def send_welcome(message):
    logger.info(f"DEBUG: send_welcome handler CALLED for chat_id: {message.chat.id}, message_text: '{message.text}'")
    chat_id = message.chat.id
    
    save_user(message)
    log_statistics('start', chat_id)

    welcome_text = "Привіт! Я ваш SellerBot. Ви надіслали /start. Бачу, що ви тут!"
    
    logger.info(f"DEBUG: Attempting to send welcome message to chat_id: {chat_id}")
    bot.send_message(chat_id, welcome_text)
    logger.info(f"DEBUG: Welcome message sent (or attempted) to chat_id: {chat_id}")

@bot.message_handler(commands=['test'])
@error_handler
def send_test_message(message):
    logger.info(f"DEBUG: send_test_message handler CALLED for chat_id: {message.chat.id}, message_text: '{message.text}'")
    chat_id = message.chat.id
    test_text = "Це тестове повідомлення. Бот працює! 🎉"
    logger.info(f"DEBUG: Attempting to send test message to chat_id: {chat_id}")
    bot.send_message(chat_id, test_text)
    logger.info(f"DEBUG: Test message sent (or attempted) to chat_id: {chat_id}")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'location'])
@error_handler
def handle_all_messages(message):
    logger.info(f"DEBUG: handle_all_messages handler CALLED for chat_id: {message.chat.id}, type: {message.content_type}, text: '{message.text[:50] if message.text else 'N/A'}'")
    chat_id = message.chat.id
    
    save_user(message)

    if message.content_type == 'text':
        response_text = f"Я отримав ваше повідомлення: '{message.text}'. Дякую за розмову!"
    elif message.content_type == 'photo':
        response_text = "Я отримав ваше фото. Поки що не знаю, що з ним робити, крім як сказати 'дякую за фото!'."
    elif message.content_type == 'location':
        response_text = f"Я отримав вашу геолокацію: {message.location.latitude}, {message.location.longitude}. Десь тут!"
    else:
        response_text = "Я отримав невідомий тип повідомлення."
        
    logger.info(f"DEBUG: Attempting to send general response to chat_id: {chat_id}")
    bot.send_message(chat_id, response_text)
    logger.info(f"DEBUG: General response sent (or attempted) to chat_id: {chat_id}")


# --- 7. Запуск бота та налаштування вебхука для Render ---

logger.info("Запуск ініціалізації БД...")
init_db()

# Логування кількості зареєстрованих обробників
logger.info(f"DEBUG: Number of message handlers registered: {len(bot.message_handlers)}")
logger.info(f"DEBUG: Number of callback query handlers registered: {len(bot.callback_query_handlers)}")


# Цей блок повинен виконуватися тільки один раз при старті додатка на Render
if WEBHOOK_URL and TOKEN:
    logger.info(f"DEBUG: WEBHOOK_URL is set ({WEBHOOK_URL}), TOKEN is set.")
    try:
        bot.remove_webhook()
        full_webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
        bot.set_webhook(url=full_webhook_url)
        logger.info(f"Webhook встановлено на: {full_webhook_url}")
    except Exception as e:
        logger.critical(f"Критична помилка встановлення webhook: {e}", exc_info=True)
        logger.error("Бот не буде отримувати оновлення від Telegram через помилку вебхука.")
        exit(1)
else:
    logger.critical(f"WEBHOOK_URL ('{WEBHOOK_URL}') або TELEGRAM_BOT_TOKEN ('<set>' if TOKEN else '<not set>') не встановлено. Бот не може працювати в режимі webhook. Вихід.")
    exit(1)

# Це основна точка входу для Flask-додатка на Render
if __name__ == '__main__':
    logger.info("Запуск Flask-додатка локально...")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# Обробник вебхуків для Flask
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook_receiver():
    """Обробляє вхідні оновлення від Telegram."""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        logger.info(f"DEBUG: Raw incoming update JSON: {json_string[:200]}...")
        
        try:
            update = telebot.types.Update.de_json(json_string)
            
            if update.message:
                logger.info(f"DEBUG: Webhook received message update from {update.message.chat.id}, text: '{update.message.text[:50] if update.message.text else 'N/A'}'")
            elif update.callback_query:
                logger.info(f"DEBUG: Webhook received callback query update from {update.callback_query.message.chat.id}, data: '{update.callback_query.data}'")
            else:
                logger.info(f"DEBUG: Webhook received unknown update type: {update}")

            logger.info("DEBUG: Attempting to process update with bot.process_new_updates...")
            bot.process_new_updates([update])
            logger.info("DEBUG: bot.process_new_updates finished.")
            return '!', 200
        except Exception as e:
            logger.critical(f"FATAL ERROR during webhook processing or pyTelegramBotAPI dispatch: {e}", exc_info=True)
            return 'Error processing update', 500
    else:
        logger.warning("Received non-JSON request on webhook path. Ignoring.")
        return 'Hello from bot webhook!', 200
