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
from flask import Flask, request
import time

# --- Завантажуємо змінні середовища НАЙПЕРШЕ ---
load_dotenv()

# --- 1. Конфігурація Бота (Змінні середовища) ---
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8039977178:AAGS-GbH-lhljGGG6OgJ2iMU_ncB-JzeOvU')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '8184456641'))
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST', 'https://telegram-ad-bot-2025.herokuapp.com')
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# --- Імпортуємо Base та User з users.py ---
from users import Base, User
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

# --- Ініціалізуємо Flask додаток та TeleBot ПІСЛЯ визначення TOKEN ---
app = Flask(__name__)
bot = telebot.TeleBot(TOKEN)

# --- 2. Налаштування логування ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 3. Налаштування Бази Даних ---
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///database.db')

def init_db():
    try:
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()

        inspector = inspect(engine)
        if not inspector.has_table('users'):
            logger.info("Таблиця 'users' не знайдена, створюю...")
            Base.metadata.create_all(engine)
            logger.info("Таблиця 'users' успішно створена.")
        else:
            logger.info("База даних ініціалізована або вже існує.")
            existing_columns = [col['name'] for col in inspector.get_columns('users')]
            if 'user_status' not in existing_columns:
                logger.info("Колонка 'user_status' не знайдена, додаю...")
                with engine.connect() as connection:
                    connection.execute(text('ALTER TABLE users ADD COLUMN user_status VARCHAR DEFAULT \'idle\''))
                    connection.commit()
                logger.info("Колонка 'user_status' додана до таблиці 'users'.")
            else:
                logger.info("Колонка 'user_status' вже існує.")

        session.close()
        logger.info("База даних успішно підключена та ініціалізована.")
    except Exception as e:
        logger.error(f"Помилка при ініціалізації бази даних: {e}", exc_info=True)
        if "postgresql" in DATABASE_URL and "sqlite" in str(e):
             logger.error("Схоже, ви намагаєтесь використовувати SQLite з PostgreSQL DATABASE_URL. Переконайтеся, що ваш DATABASE_URL правильний.")
        raise

# --- 4. Обробник вебхука Flask ---
@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    logger.info("Webhook endpoint hit!") # Додано логування
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        logger.info(f"Received JSON update: {json_string[:200]}...") # Логуємо частину вхідного JSON
        try:
            update = telebot.types.Update.de_json(json_string)
            bot.process_new_updates([update])
            logger.info("Successfully processed Telegram update.") # Логуємо успішну обробку
            return '!', 200
        except Exception as e:
            logger.error(f"Error processing Telegram update: {e}", exc_info=True) # Логуємо помилки при обробці
            return 'Error processing update', 500 # Повертаємо 500 у разі внутрішньої помилки
    else:
        logger.warning(f"Received non-JSON request: {request.headers.get('content-type')}") # Логуємо не-JSON запити
        return "Forbidden", 403


# --- 5. Обробники Бота ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()

        user = session.query(User).filter_by(chat_id=message.chat.id).first()

        if not user:
            new_user = User(
                chat_id=message.chat.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            session.add(new_user)
            session.commit()
            logger.info(f"Новий користувач доданий: {message.chat.id}")
            bot.send_message(message.chat.id, "Привіт! Ласкаво просимо до бота. Ви зареєстровані.")
        else:
            user.last_activity = datetime.now()
            user.user_status = 'active'
            session.commit()
            logger.info(f"Користувач {message.chat.id} вже існує. Оновлено.")
            bot.send_message(message.chat.id, "З поверненням! Я вже вас знаю.")

        session.close()

    except Exception as e:
        logger.error(f"Помилка при обробці команди /start: {e}", exc_info=True)
        bot.send_message(message.chat.id, "Виникла помилка при реєстрації. Спробуйте пізніше.")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.chat.id == ADMIN_CHAT_ID:
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("👥 Користувачі", callback_data="admin_users"),
            types.InlineKeyboardButton("⚙️ Налаштування", callback_data="admin_settings"),
            types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
            types.InlineKeyboardButton("📚 Керування FAQ", callback_data="admin_faq_menu")
        )

        bot.send_message(message.chat.id, "🔧 *Адмін-панель*\n\nОберіть дію:",
                               reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, "У вас немає доступу до адмін-панелі.")


@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callback_handler(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "У вас немає доступу.")
        return

    if call.data == "admin_users":
        bot.edit_message_text("🚧 *Керування користувачами* 🚧\n\nФункціонал в розробці.",
                              chat_id=call.message.chat.id, message_id=call.message.message_id,
                              parse_mode='Markdown', reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")))
    elif call.data == "admin_panel":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("👥 Користувачі", callback_data="admin_users"),
            types.InlineKeyboardButton("⚙️ Налаштування", callback_data="admin_settings"),
            types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
            types.InlineKeyboardButton("📚 Керування FAQ", callback_data="admin_faq_menu")
        )

        bot.edit_message_text("🔧 *Адмін-панель*\n\nОберіть дію:",
                              chat_id=call.message.chat.id, message_id=call.message.message_id,
                              reply_markup=markup, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

# --- КОД ЗАПУСКУ БОТА (виконується Gunicorn'ом) ---
logger.info("Запуск ініціалізації БД...")
init_db()

logger.info("Видалення попереднього вебхука...")
bot.remove_webhook()
time.sleep(0.1)

logger.info(f"Встановлення вебхука на: {WEBHOOK_URL}")
bot.set_webhook(url=WEBHOOK_URL)

logger.info("Бот запускається...")
