import sqlite3
import os
import telebot
from telebot import types
import logging # Import logging first
from datetime import datetime, timedelta
import re
import json
import requests
from dotenv import load_dotenv
from flask import Flask, request # Імпортуємо Flask
import time # Додано для time.sleep

# Імпортуємо Base та User з users.py
# Переконайтесь, що users.py знаходиться в тій же директорії, що й bot.py
from users import Base, User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

# --- 2. Налаштування логування (ПЕРЕМІЩЕНО ВГОРУ ДЛЯ РАННЬОЇ ІНІЦІАЛІЗАЦІЇ) ---
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

# --- 1. Конфігурація Бота ---
# Рекомендується використовувати змінні середовища для безпеки та легкості конфігурації.
# Якщо змінні середовища не встановлені, використовуються значення за замовчуванням (тільки для розробки!).
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8039977178:AAGS-GbH-lhljGGG6OgJ2iMU_ncB-JzeOvU') # ЗАМІНІТЬ ЦЕЙ ТОКЕН НА ВАШ АКТУАЛЬНИЙ!
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '8184456641')) # ЗАМІНІТЬ НА ВАШ CHAT_ID АДМІНІСТРАТОРА!
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '-1002535586055')) # ЗАМІНІТЬ НА ID ВАШОГО КАНАЛУ!
MONOBANK_CARD_NUMBER = os.getenv('MONOBANK_CARD_NUMBER', '4441 1111 5302 1484') # ЗАМІНІТЬ НА НОМЕР КАРТКИ!

# XAI (Grok) API налаштування
XAI_API_KEY = os.getenv('XAI_API_KEY', 'YOUR_XAI_API_KEY_HERE') # ЗАМІНІТЬ НА ВАШ КЛЮЧ XAI API!
XAI_API_URL = os.getenv('XAI_API_URL', 'https://api.x.ai/v1/chat/completions') # ЗАМІНІТЬ НА ВАШ URL XAI API, ЯКЩО ВІН ВІДРІЗНЯЄТЬСЯ!

# Heroku Webhook налаштування
heroku_app_name_raw = os.getenv('HEROKU_APP_NAME')
if heroku_app_name_raw:
    # Видаляємо зайві пробіли або лапки, якщо вони випадково потрапили у змінну
    HEROKU_APP_NAME = heroku_app_name_raw.strip("'\" ").lower()
else:
    logger.warning("Змінна середовища 'HEROKU_APP_NAME' не встановлена. Вебхук може не працювати коректно. Використовуйте заглушку для локального тестування.")
    HEROKU_APP_NAME = 'your-app-name' # Заглушка для локального тестування

WEBHOOK_URL_BASE = "https://" + HEROKU_APP_NAME + ".herokuapp.com"
WEBHOOK_URL_PATH = f"/webhook/{TOKEN}" # Шлях, на який Telegram надсилатиме оновлення. Використання TOKEN як частини шляху робить його унікальним.

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__) # Ініціалізуємо Flask додаток ПІСЛЯ імпортів Flask та конфігурації

# ===================
# 📦 Конфігурація Бази Даних (SQLAlchemy)
# ===================
DATABASE_URL_RAW = os.getenv("DATABASE_URL")
if DATABASE_URL_RAW:
    DATABASE_URL = DATABASE_URL_RAW.strip() # Видаляємо зайві пробіли
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL задано, але порожнє після обробки!")
else:
    raise ValueError("❌ DATABASE_URL не задано!")

try:
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(engine) # Створюємо таблиці, якщо їх немає
    logger.info("База даних успішно підключена та ініціалізована.")
except Exception as e:
    logger.error(f"Помилка підключення або ініціалізації бази даних: {e}", exc_info=True)
    # Важливо: якщо БД не працює, бот не зможе функціонувати.
    # Можна вийти або спробувати продовжити з обмеженим функціоналом.
    # Наразі, ми дозволимо йому впасти, щоб помилка була очевидною.
    raise

# --- 3. Змінні станів для багатошагових процесів ---
# Використовується для зберігання тимчасових даних під час додавання товару.
# Формат: {chat_id: {'step_number': 1, 'data': {'product_name': '', ...}}}
user_data = {}

# --- 4. Управління базою даних (SQLite - ТЕПЕР SQLAlchemy) ---
# DB_NAME = 'seller_bot.db' # Більше не використовується для SQLite

def get_db_connection():
    """Повертає з'єднання з базою даних SQLAlchemy."""
    return Session() # Повертаємо нову сесію

def init_db():
    """Ініціалізує базу даних, створюючи необхідні таблиці та оновлюючи схему."""
    # Ця функція тепер в основному виконується через Base.metadata.create_all(engine)
    # Але ми можемо додати логіку для оновлення схеми, якщо потрібно.
    session = Session()
    try:
        # Перевірка та додавання колонки user_status, якщо її немає
        # Для SQLAlchemy це робиться складніше, ніж для чистого SQLite.
        # Зазвичай для міграцій використовують Alembic.
        # Для простоти, ми можемо спробувати додати колонку, якщо її немає,
        # але це не є ідеальним рішенням для production.
        conn = engine.raw_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN user_status TEXT DEFAULT 'idle'")
            logger.info("Колонка 'user_status' додана до таблиці 'users'.")
        except Exception as e:
            # Перевіряємо, чи помилка пов'язана з тим, що колонка вже існує
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                logger.info("Колонка 'user_status' вже існує в таблиці 'users'.")
            else:
                logger.error(f"Помилка при додаванні колонки 'user_status': {e}")
        finally:
            cursor.close()
            conn.close()

        # Таблиця для переписок з AI - перевірка наявності та створення, якщо немає
        # Це вже робиться через Base.metadata.create_all(engine)
        # Але якщо ви хочете додати інші таблиці, які не є частиною Base,
        # або перевірити їх, це місце для цього.

        logger.info("База даних ініціалізована або вже існує.")
    except Exception as e:
        logger.error(f"Помилка при ініціалізації БД (init_db): {e}", exc_info=True)
    finally:
        session.close()


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

    session = get_db_connection()
    try:
        existing_user = session.query(User).filter_by(chat_id=chat_id).first()
        if existing_user:
            existing_user.username = user.username
            existing_user.first_name = user.first_name
            existing_user.last_name = user.last_name
            existing_user.last_activity = datetime.now()
        else:
            new_user = User(
                chat_id=chat_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                joined_at=datetime.now(),
                last_activity=datetime.now()
            )
            session.add(new_user)
        session.commit()
        logger.info(f"Користувача {chat_id} збережено/оновлено.")
    except Exception as e:
        session.rollback()
        logger.error(f"Помилка при збереженні користувача {chat_id}: {e}")
    finally:
        session.close()

@error_handler
def is_user_blocked(chat_id):
    """Перевіряє, чи заблокований користувач."""
    session = get_db_connection()
    try:
        user = session.query(User).filter_by(chat_id=chat_id).first()
        return user and user.is_blocked
    except Exception as e:
        logger.error(f"Помилка перевірки блокування для {chat_id}: {e}")
        return True # Вважаємо заблокованим у разі помилки для безпеки
    finally:
        session.close()

@error_handler
def set_user_block_status(admin_id, chat_id, status):
    """Встановлює статус блокування для користувача."""
    session = get_db_connection()
    try:
        user = session.query(User).filter_by(chat_id=chat_id).first()
        if user:
            user.is_blocked = status
            user.blocked_by = admin_id if status else None
            user.blocked_at = datetime.now() if status else None
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        logger.error(f"Помилка при встановленні статусу блокування для користувача {chat_id}: {e}")
        return False
    finally:
        session.close()

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
    # TODO: Реалізувати логування статистики в БД, якщо це потрібно.
    # Наразі, просто логуємо в консоль/файл.
    logger.info(f"STATISTIC: Action={action}, User={user_id}, Product={product_id}, Details={details}")


# --- Функції для управління статусом користувача ---
def get_user_current_status(chat_id):
    session = get_db_connection()
    try:
        user = session.query(User).filter_by(chat_id=chat_id).first()
        return user.user_status if user else 'idle'
    except Exception as e:
        logger.error(f"Помилка отримання статусу користувача {chat_id}: {e}")
        return 'idle' # Повертаємо 'idle' у разі помилки
    finally:
        session.close()

def set_user_status(chat_id, status):
    session = get_db_connection()
    try:
        user = session.query(User).filter_by(chat_id=chat_id).first()
        if user:
            user.user_status = status
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Помилка встановлення статусу користувача {chat_id} на {status}: {e}")
    finally:
        session.close()

# --- Функції для роботи з FAQ ---
def add_faq_entry(question, answer):
    # TODO: Реалізувати таблицю FAQ та функції для неї
    logger.warning("Функція add_faq_entry не реалізована в БД.")
    return False

def get_faq_answer(question_text):
    # TODO: Реалізувати таблицю FAQ та функції для неї
    logger.warning("Функція get_faq_answer не реалізована в БД.")
    return None

def delete_faq_entry(faq_id):
    # TODO: Реалізувати таблицю FAQ та функції для неї
    logger.warning("Функція delete_faq_entry не реалізована в БД.")
    return False

def get_all_faq_entries():
    # TODO: Реалізувати таблицю FAQ та функції для неї
    logger.warning("Функція get_all_faq_entries не реалізована в БД.")
    return []

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
    # TODO: Реалізувати збереження розмов в БД
    logger.warning("Функція save_conversation не реалізована в БД.")
    pass

@error_handler
def get_conversation_history(chat_id, limit=5):
    """Отримує історію розмов для контексту AI."""
    # TODO: Реалізувати отримання історії розмов з БД
    logger.warning("Функція get_conversation_history не реалізована в БД.")
    return []

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
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("❌ Завершити чат з адміном"))
    bot.send_message(message.chat.id, "Ваш запит передано адміністратору. Будь ласка, очікуйте відповіді. Для завершення чату натисніть кнопку.", reply_markup=markup)
    
    user = message.from_user
    username_info = f"@{user.username}" if user.username else "без нікнейму"
    user_link = f"tg://user?id={user.id}"

    admin_message_text = (
        f"🚨 *НОВИЙ ЗАПИТ: Чат з людиною!* 🚨\n\n"
        f"Користувач: [{user.first_name} {user.last_name}]({user_link}) ({username_info})\n"
        f"ID: `{user.id}`\n\n"
        f"**Остання історія розмови (AI):**\n"
    )
    
    history = get_conversation_history(message.chat.id, limit=5) # Отримуємо останні 5 повідомлень AI
    if history:
        for entry in history:
            role = "Користувач" if entry['role'] == 'user' else "Бот (AI)"
            admin_message_text += f"*{role}*: {entry['content']}\n"
    else:
        admin_message_text += "Історія розмови відсутня."
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Прийняти запит", callback_data=f"accept_human_chat_{message.chat.id}"))
    
    bot.send_message(ADMIN_CHAT_ID, admin_message_text, parse_mode='Markdown') # Без reply_markup, бо це для адміна
    bot.send_message(ADMIN_CHAT_ID, "Натисніть 'Прийняти запит', щоб почати відповідати користувачу.", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "❌ Завершити чат з адміном")
@error_handler
def stop_human_chat_command(message):
    if get_user_current_status(message.chat.id) == 'waiting_human_operator':
        set_user_status(message.chat.id, 'idle')
        bot.send_message(message.chat.id, "Ви завершили чат з адміністратором. Якщо виникнуть питання, звертайтесь знову.",
                         reply_markup=main_menu_markup)
        bot.send_message(ADMIN_CHAT_ID, f"Користувач {message.from_user.first_name} ({message.chat.id}) завершив чат з оператором.")
    else:
        bot.send_message(message.chat.id, "Ви зараз не перебуваєте в чаті з оператором.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('accept_human_chat_'))
@error_handler
def accept_human_chat_callback(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "❌ Доступ заборонено.")
        return

    user_id_to_chat = int(call.data.split('_')[3])
    
    # Перевіряємо, чи користувач все ще очікує
    if get_user_current_status(user_id_to_chat) == 'waiting_human_operator':
        set_user_status(ADMIN_CHAT_ID, f'chatting_with_user_{user_id_to_chat}') # Адмін починає чат
        bot.edit_message_text(f"Ви прийняли запит від користувача `{user_id_to_chat}`. Тепер ви можете спілкуватися з ним.",
                              chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')
        bot.send_message(user_id_to_chat, "✅ Адміністратор приєднався до чату! Будь ласка, напишіть ваше питання.")
    else:
        bot.edit_message_text(f"Запит від користувача `{user_id_to_chat}` вже неактуальний або був оброблений.",
                              chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda message: message.text == "🎁 Персональна пропозиція")
@error_handler
def personal_offer_command(message):
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "❌ Ви заблоковані і не можете використовувати цю функцію.")
        return
    set_user_status(message.chat.id, 'awaiting_personal_offer_details')
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("❌ Скасувати пропозицію"))
    bot.send_message(message.chat.id, "Будь ласка, детально опишіть ваше ексклюзивне замовлення або персональну пропозицію. Що ви хочете отримати?", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "❌ Скасувати пропозицію")
@error_handler
def cancel_personal_offer(message):
    set_user_status(message.chat.id, 'idle')
    bot.send_message(message.chat.id, "Створення персональної пропозиції скасовано.", reply_markup=main_menu_markup)

# --- 10. Потік додавання товару ---
ADD_PRODUCT_STEPS = {
    1: {'name': 'waiting_name', 'prompt': "📝 *Крок 1/5: Назва товару*\n\nВведіть назву товару:", 'next_step': 2},
    2: {'name': 'waiting_price', 'prompt': "💰 *Крок 2/5: Ціна*\n\nВведіть ціну (наприклад, 100.50) або 'Договірна':", 'next_step': 3},
    3: {'name': 'waiting_photos', 'prompt': "📸 *Крок 3/5: Фотографії*\n\nНадішліть до 5 фото (по одному). Коли закінчите - 'Далі':", 'next_step': 4, 'allow_skip': True, 'skip_button': 'Пропустити фото'},
    4: {'name': 'waiting_location', 'prompt': "📍 *Крок 4/5: Геолокація*\n\nНадішліть геолокацію або натисніть 'Пропустити':", 'next_step': 5, 'allow_skip': True, 'skip_button': 'Пропустити геолокацію'},
    # ЗМІНИ ТУТ: Додано allow_skip та skip_button для кроку 5
    5: {'name': 'waiting_description', 'prompt': "✍️ *Крок 5/5: Опис*\n\nНапишіть детальний опис товару (мінімум 10 символів) або натисніть 'Пропустити':", 'next_step': 'confirm', 'allow_skip': True, 'skip_button': 'Пропустити опис'}
}

@bot.message_handler(func=lambda message: message.text == "🔥 Продати товар")
@error_handler
def start_add_product_flow(message):
    """Починає процес додавання нового товару."""
    chat_id = message.chat.id
    session = get_db_connection()
    try:
        # TODO: Замінити на реальну модель Product, коли вона буде реалізована
        # from your_product_model import Product # Уявімо, що Product імпортується
        # pending_count = session.query(Product).filter_by(seller_chat_id=chat_id, status='pending').count()
        
        # Тимчасова заглушка, поки модель Product не реалізована
        pending_count = 0 # Припускаємо 0 товарів на модерації для тестування
        logger.warning("Перевірка кількості товарів на модерації тимчасово відключена (немає моделі Product).")

        if pending_count >= 3: # Обмеження на кількість товарів на модерації
            bot.send_message(chat_id,
                            "⚠️ У вас вже є 3 товари на модерації.\n"
                            "Дочекайтеся розгляду поточних оголошень перед додаванням нових.",
                            reply_markup=main_menu_markup)
            return
    except Exception as e:
        logger.error(f"Помилка при перевірці товарів на модерації для {chat_id}: {e}")
        bot.send_message(chat_id, "Виникла помилка. Спробуйте пізніше.", reply_markup=main_menu_markup)
        return
    finally:
        session.close()

    user_data[chat_id] = {
        'step_number': 1, 
        'data': {
            'photos': [], 
            'geolocation': None,
            'product_name': '',
            'price': '',
            'description': ''
        }
    }
    set_user_status(chat_id, 'adding_product_step_1') # Встановлюємо початковий статус
    send_product_step_message(chat_id)
    log_statistics('start_add_product', chat_id)

@error_handler
def send_product_step_message(chat_id):
    """Надсилає користувачу повідомлення для поточного кроку додавання товару."""
    current_step_number = user_data[chat_id]['step_number']
    step_config = ADD_PRODUCT_STEPS[current_step_number]
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    
    if step_config['name'] == 'waiting_photos':
        markup.add(types.KeyboardButton("Далі"), types.KeyboardButton(step_config['skip_button']))
    elif step_config['name'] == 'waiting_location':
        markup.add(types.KeyboardButton("📍 Надіслати геолокацію", request_location=True))
        markup.add(types.KeyboardButton(step_config['skip_button']))
    # ЗМІНИ ТУТ: Додано кнопки для кроку з описом
    elif step_config['name'] == 'waiting_description':
        markup.add(types.KeyboardButton("Далі"), types.KeyboardButton(step_config['skip_button']))
    
    markup.add(types.KeyboardButton("❌ Скасувати додавання"))
    
    bot.send_message(chat_id, step_config['prompt'], parse_mode='Markdown', reply_markup=markup)

@error_handler
def process_product_step(message):
    """Обробляє текстовий ввід користувача під час додавання товару."""
    chat_id = message.chat.id
    current_user_status = get_user_current_status(chat_id)

    # Визначаємо крок на основі user_data
    if chat_id not in user_data or 'step_number' not in user_data[chat_id]:
        # Це може статися, якщо бот перезавантажився або стан втрачено
        bot.send_message(chat_id, "Вибачте, ваш попередній процес додавання товару було скинуто. Будь ласка, почніть знову.", reply_markup=main_menu_markup)
        set_user_status(chat_id, 'idle')
        return

    current_step_number = user_data[chat_id]['step_number']
    step_config = ADD_PRODUCT_STEPS[current_step_number]
    user_text = message.text if message.content_type == 'text' else ""

    # Обробка скасування
    if user_text == "❌ Скасувати додавання":
        del user_data[chat_id]
        bot.send_message(chat_id, "Додавання товару скасовано.", reply_markup=main_menu_markup)
        set_user_status(chat_id, 'idle')
        return

    # Обробка пропуску кроку (для фото, геолокації та опису)
    if step_config.get('allow_skip') and user_text == step_config.get('skip_button'):
        # Для опису, якщо пропустили, встановлюємо порожній опис
        if step_config['name'] == 'waiting_description':
            user_data[chat_id]['data']['description'] = ""
        go_to_next_step(chat_id)
        return

    # Валідація та збереження даних для кожного кроку
    if step_config['name'] == 'waiting_name':
        if user_text and 3 <= len(user_text) <= 100:
            user_data[chat_id]['data']['product_name'] = user_text
            user_data[chat_id]['step_number'] = 2 # Переходимо до наступного кроку
            set_user_status(chat_id, 'adding_product_step_2')
            send_product_step_message(chat_id)
        else:
            bot.send_message(chat_id, "Назва товару повинна бути від 3 до 100 символів. Спробуйте ще раз:")

    elif step_config['name'] == 'waiting_price':
        if user_text and len(user_text) <= 50: # Дозволяємо "Договірна" або число
            user_data[chat_id]['data']['price'] = user_text
            user_data[chat_id]['step_number'] = 3
            set_user_status(chat_id, 'adding_product_step_3')
            send_product_step_message(chat_id)
        else:
            bot.send_message(chat_id, "Будь ласка, вкажіть ціну (до 50 символів) або 'Договірна':")

    elif step_config['name'] == 'waiting_photos':
        if user_text == "Далі": # Якщо користувач натиснув "Далі" після додавання фото
            user_data[chat_id]['step_number'] = 4
            set_user_status(chat_id, 'adding_product_step_4')
            send_product_step_message(chat_id)
        else:
            # Цей блок буде викликаний, якщо message.content_type НЕ 'text' (наприклад, 'photo')
            # або якщо 'text' не є "Далі" чи "Пропустити фото".
            # Оскільки фото обробляються окремо в handle_messages, тут це повідомлення не потрібне.
            pass 

    elif step_config['name'] == 'waiting_location':
        # Цей крок обробляється окремо через content_types=['location']
        bot.send_message(chat_id, "Надішліть геолокацію або натисніть 'Пропустити геолокацію'.")

    elif step_config['name'] == 'waiting_description':
        # ЗМІНИ ТУТ: Обробка "Далі" для опису та валідація
        if user_text == "Далі":
            # Якщо користувач натиснув "Далі", але не ввів опис, він буде порожнім
            if not user_data[chat_id]['data']['description']:
                user_data[chat_id]['data']['description'] = "" # Забезпечуємо, що опис не None
            set_user_status(chat_id, 'confirm_product')
            confirm_and_send_for_moderation(chat_id)
        elif user_text and 10 <= len(user_text) <= 1000:
            user_data[chat_id]['data']['description'] = user_text
            set_user_status(chat_id, 'confirm_product') # Переходимо до підтвердження
            confirm_and_send_for_moderation(chat_id)
        else:
            bot.send_message(chat_id, "Опис занадто короткий (мінімум 10 символів) або занадто довгий (максимум 1000 символів). Напишіть детальніше або натисніть 'Пропустити'/'Далі':")

@error_handler
def go_to_next_step(chat_id):
    """Переводить користувача до наступного кроку в процесі додавання товару."""
    current_step_number = user_data[chat_id]['step_number']
    next_step_info = ADD_PRODUCT_STEPS[current_step_number]
    next_step_number = next_step_info['next_step']
    
    if next_step_number == 'confirm':
        set_user_status(chat_id, 'confirm_product')
        confirm_and_send_for_moderation(chat_id)
    else:
        user_data[chat_id]['step_number'] = next_step_number
        set_user_status(chat_id, f'adding_product_step_{next_step_number}')
        send_product_step_message(chat_id)

@bot.message_handler(content_types=['photo'], func=lambda message: get_user_current_status(message.chat.id) == 'adding_product_step_3')
@error_handler
def process_product_photo(message):
    """Обробляє завантаження фотографій товару."""
    chat_id = message.chat.id
    if len(user_data[chat_id]['data']['photos']) < 5:
        file_id = message.photo[-1].file_id
        user_data[chat_id]['data']['photos'].append(file_id)
        photos_count = len(user_data[chat_id]['data']['photos'])
        bot.send_message(chat_id, f"✅ Фото {photos_count}/5 додано. Надішліть ще або натисніть 'Далі'")
    else:
        bot.send_message(chat_id, "Максимум 5 фото. Натисніть 'Далі' для продовження.")

@bot.message_handler(content_types=['location'], func=lambda message: get_user_current_status(message.chat.id) == 'adding_product_step_4')
@error_handler
def process_product_location(message):
    """Обробляє надсилання геолокації для товару."""
    chat_id = message.chat.id
    user_data[chat_id]['data']['geolocation'] = {
        'latitude': message.location.latitude,
        'longitude': message.location.longitude
    }
    bot.send_message(chat_id, "✅ Геолокацію додано!")
    user_data[chat_id]['step_number'] = 5 # Перехід до опису
    set_user_status(chat_id, 'adding_product_step_5')
    send_product_step_message(chat_id)

@error_handler
def confirm_and_send_for_moderation(chat_id):
    """Зберігає товар у БД, сповіщає користувача та адміністратора про новий товар на модерації."""
    data = user_data[chat_id]['data']
    
    session = get_db_connection()
    product_id = None
    try:
        user_info = bot.get_chat(chat_id)
        seller_username = user_info.username if user_info.username else None

        # TODO: Реалізувати модель Product для збереження товару
        # new_product = Product(
        #     seller_chat_id=chat_id,
        #     seller_username=seller_username,
        #     product_name=data['product_name'],
        #     price=data['price'],
        #     description=data['description'],
        #     photos=json.dumps(data['photos']) if data['photos'] else None,
        #     geolocation=json.dumps(data['geolocation']) if data['geolocation'] else None,
        #     status='pending'
        # )
        # session.add(new_product)
        # session.commit()
        # product_id = new_product.id

        logger.warning("Збереження товару в БД тимчасово відключено (немає моделі Product).")
        product_id = 99999 # Заглушка для ID товару

        # Сповіщення користувача
        bot.send_message(chat_id, 
            f"✅ Товар '{data['product_name']}' відправлено на модерацію! (ID: {product_id})\n"
            f"Ви отримаєте сповіщення після перевірки.",
            reply_markup=main_menu_markup)
        
        # Сповіщення адміністратора
        send_product_for_admin_review(product_id, data, seller_chat_id=chat_id, seller_username=seller_username)
        
        # Очищуємо дані користувача
        del user_data[chat_id]
        
        log_statistics('product_added', chat_id, product_id)
        set_user_status(chat_id, 'idle') # Повертаємо статус "вільний"
        
    except Exception as e:
        session.rollback()
        logger.error(f"Помилка збереження товару: {e}")
        bot.send_message(chat_id, "Помилка збереження товару. Спробуйте пізніше.")
    finally:
        session.close()

@error_handler
def send_product_for_admin_review(product_id, data, seller_chat_id, seller_username):
    """Формує та надсилає повідомлення адміністратору для модерації нового товару."""
    hashtags = generate_hashtags(data['description'])
    review_text = (
        f"📦 *Новий товар на модерацію*\n\n"
        f"🆔 ID: {product_id}\n"
        f"📝 Назва: {data['product_name']}\n"
        f"💰 Ціна: {data['price']}\n"
        f"📄 Опис: {data['description'][:500]}...\n" # Обрізаємо опис для адмін-панелі
        f"📸 Фото: {len(data['photos'])} шт.\n"
        f"📍 Геолокація: {'Так' if data['geolocation'] else 'Ні'}\n"
        f"🏷️ Хештеги: {hashtags}\n\n"
        f"👤 Продавець: [{'@' + seller_username if seller_username else 'Користувач'}](tg://user?id={seller_chat_id})"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Схвалити", callback_data=f"approve_{product_id}"),
        types.InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{product_id}")
    )
    
    try:
        admin_msg = None
        if data['photos']:
            media_group = []
            # Додаємо перше фото з підписом
            media_group.append(types.InputMediaPhoto(data['photos'][0], caption=review_text, parse_mode='Markdown'))
            # Додаємо решту фото без підпису
            for photo_id in data['photos'][1:]:
                media_group.append(types.InputMediaPhoto(photo_id))
            
            # Надсилаємо медіа-групу адміністратору
            sent_admin_messages = bot.send_media_group(ADMIN_CHAT_ID, media_group)
            admin_msg = sent_admin_messages[0] # Зберігаємо перше повідомлення групи для отримання message_id
        else:
            admin_msg = bot.send_message(ADMIN_CHAT_ID, review_text,
                                       parse_mode='Markdown',
                                       reply_markup=markup)
        
        if admin_msg:
            # TODO: Зберігати admin_message_id в БД для моделі Product
            logger.warning(f"admin_message_id для товару {product_id} не збережено (немає моделі Product).")

            # Якщо це медіа-група, ми не можемо додати reply_markup до всієї групи.
            # Натомість, ми можемо відправити окреме повідомлення з кнопками.
            # Або, якщо це одне фото, то reply_markup вже додано.
            if data['photos'] and len(data['photos']) > 0: # Якщо є фото, відправляємо кнопки окремо
                bot.send_message(ADMIN_CHAT_ID, "Оберіть дію:", reply_markup=markup,
                                 reply_to_message_id=admin_msg.message_id) # Відповідаємо на перше фото
            elif not data['photos']: # Якщо фото немає, кнопки вже в admin_msg
                pass # Нічого не робимо, бо кнопки вже були додані до текстового повідомлення
            
    except Exception as e:
        logger.error(f"Помилка при відправці товару {product_id} адміністратору: {e}", exc_info=True)


# --- 11. Обробники текстових повідомлень та кнопок меню ---
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'location'])
@error_handler
def handle_messages(message):
    """Основний обробник для всіх вхідних повідомлень."""
    chat_id = message.chat.id
    user_text = message.text if message.content_type == 'text' else ""
    current_user_status = get_user_current_status(chat_id)

    if is_user_blocked(chat_id):
        bot.send_message(chat_id, "❌ Ваш акаунт заблоковано.")
        return
    
    save_user(message)

    # 1. Обробка фото та геолокації під час додавання товару (ПЕРШИМИ!)
    if message.content_type == 'photo' and current_user_status == 'adding_product_step_3':
        process_product_photo(message)
        return
    if message.content_type == 'location' and current_user_status == 'adding_product_step_4':
        process_product_location(message)
        return

    # 2. Обробка процесу додавання товару (текстовий ввід)
    if current_user_status.startswith('adding_product_step_') or current_user_status == 'confirm_product':
        process_product_step(message) # Ця функція тепер коректно обробляє лише ТЕКСТОВИЙ ввід для кроків
        return

    # 3. Обробка чату з людиною (для адміна)
    if str(chat_id) == str(ADMIN_CHAT_ID) and current_user_status.startswith('chatting_with_user_'):
        target_user_id = int(current_user_status.split('_')[3])
        try:
            bot.send_message(target_user_id, f"Відповідь адміністратора: {user_text}")
            bot.send_message(chat_id, "Повідомлення відправлено користувачу.")
        except Exception as e:
            logger.error(f"Помилка пересилання повідомлення від адміна до користувача {target_user_id}: {e}")
            bot.send_message(chat_id, "Не вдалося відправити повідомлення користувачу. Можливо, він заблокував бота.")
        return

    # 4. Обробка персональної пропозиції
    if current_user_status == 'awaiting_personal_offer_details':
        if user_text == "❌ Скасувати пропозицію":
            set_user_status(chat_id, 'idle')
            bot.send_message(chat_id, "Створення персональної пропозиції скасовано.", reply_markup=main_menu_markup)
            return
        
        # Пересилаємо деталі персональної пропозиції адміністратору
        user = message.from_user
        username_info = f"@{user.username}" if user.username else "без нікнейму"
        user_link = f"tg://user?id={user.id}"
        
        admin_offer_text = (
            f"🎁 *НОВА ПЕРСОНАЛЬНА ПРОПОЗИЦІЯ!* 🎁\n\n"
            f"Від користувача: [{user.first_name} {user.last_name}]({user_link}) ({username_info})\n"
            f"ID: `{user.id}`\n\n"
            f"**Деталі пропозиції:**\n{user_text}\n\n"
            f"Будь ласка, зв'яжіться з користувачем для обговорення."
        )
        bot.send_message(ADMIN_CHAT_ID, admin_offer_text, parse_mode='Markdown')
        bot.send_message(chat_id, "✅ Вашу персональну пропозицію надіслано адміністратору. Очікуйте зв'язку!", reply_markup=main_menu_markup)
        set_user_status(chat_id, 'idle')
        return

    # 5. Обробка AI-чату
    if current_user_status == 'ai_chat':
        if user_text == "❌ Вийти з AI чату":
            stop_ai_command(message)
        else:
            # Спочатку шукаємо відповідь у FAQ
            faq_answer = get_faq_answer(user_text)
            if faq_answer:
                bot.send_message(chat_id, f"📚 *Ось що я знайшов у нашій базі знань:*\n\n{faq_answer}", parse_mode='Markdown')
                save_conversation(chat_id, user_text, 'user')
                save_conversation(chat_id, faq_answer, 'ai')
            else:
                # Якщо в FAQ немає, звертаємося до Grok AI
                save_conversation(chat_id, user_text, 'user')
                ai_reply = get_grok_response(user_text, get_conversation_history(chat_id, limit=10))
                save_conversation(chat_id, ai_reply, 'ai')
                bot.send_message(chat_id, f"🤖 {ai_reply}")
        return

    # 6. Обробка команд адмін-панелі для FAQ (текстовий ввід)
    if chat_id == ADMIN_CHAT_ID:
        if current_user_status == 'awaiting_faq_question':
            user_data[ADMIN_CHAT_ID] = {'faq_question': user_text}
            set_user_status(ADMIN_CHAT_ID, 'awaiting_faq_answer')
            bot.send_message(ADMIN_CHAT_ID, "Тепер введіть відповідь на це питання:")
            return
        elif current_user_status == 'awaiting_faq_answer':
            question = user_data[ADMIN_CHAT_ID]['faq_question']
            answer = user_text
            
            if add_faq_entry(question, answer):
                bot.send_message(ADMIN_CHAT_ID, "✅ Питання та відповідь успішно додано до FAQ.")
            else:
                bot.send_message(ADMIN_CHAT_ID, "❌ Помилка: Таке питання вже існує в FAQ.")
            
            set_user_status(ADMIN_CHAT_ID, 'idle')
            send_admin_faq_menu_after_action(message) # Повертаємо до меню FAQ
            return
        elif current_user_status == 'awaiting_faq_delete_id':
            try:
                faq_id = int(user_text)
                if delete_faq_entry(faq_id):
                    bot.send_message(ADMIN_CHAT_ID, f"✅ Питання з ID {faq_id} успішно видалено з FAQ.")
                else:
                    bot.send_message(ADMIN_CHAT_ID, f"❌ Помилка: Питання з ID {faq_id} не знайдено.")
            except ValueError:
                bot.send_message(ADMIN_CHAT_ID, "Будь ласка, введіть дійсний числовий ID.")
            
            set_user_status(ADMIN_CHAT_ID, 'idle')
            send_admin_faq_menu_after_action(message) # Повертаємо до меню FAQ
            return
        elif current_user_status == 'awaiting_user_for_block_unblock': # Обробка вводу для блокування/розблокування
            process_user_for_block_unblock(message)
            return


    # 7. Обробка кнопок головного меню та інших неочікуваних повідомлень
    if user_text == "🔥 Продати товар":
        start_add_product_flow(message)
    elif user_text == "🛒 Мої товари":
        send_my_products(message)
    elif user_text == "❓ Допомога":
        send_help_message(message)
    elif user_text == "💰 Комісія":
        send_commission_info(message)
    elif user_text == "📺 Наш канал":
        send_channel_link(message)
    elif user_text == "🤖 Запитати AI":
        ask_ai_command(message)
    elif user_text == "👨‍💻 Зв'язатися з адміном":
        chat_with_human_command(message)
    elif user_text == "🎁 Персональна пропозиція":
        personal_offer_command(message)
    elif message.content_type == 'text': # Якщо це текстове повідомлення і не оброблено вище, і не в AI-чаті
        # Можна перенаправити до AI за замовчуванням або повідомити про незрозумілу команду
        bot.send_message(chat_id, "Я не зрозумів ваш запит. Будь ласка, скористайтеся кнопками меню або натисніть '🤖 Запитати AI', щоб поспілкуватися з моїм штучним інтелектом.", reply_markup=main_menu_markup)
    elif message.content_type == 'photo':
        bot.send_message(chat_id, "Я отримав ваше фото, але не знаю, що з ним робити поза процесом додавання товару. 🤔")
    elif message.content_type == 'location':
        bot.send_message(chat_id, f"Я бачу вашу геоточку: {message.location.latitude}, {message.location.longitude}. Як я можу її використати?")
    else:
        bot.send_message(chat_id, "Я не зрозумів ваш запит. Спробуйте використати кнопки меню.")

# --- 12. Список товарів користувача ---
@error_handler
def send_my_products(message):
    """Надсилає користувачу список його товарів."""
    chat_id = message.chat.id
    session = get_db_connection()
    try:
        # TODO: Реалізувати отримання товарів з БД за допомогою моделі Product
        # from your_product_model import Product
        # user_products = session.query(Product).filter_by(seller_chat_id=chat_id).order_by(Product.created_at.desc()).all()
        user_products = [] # Заглушка, поки модель Product не реалізована
        logger.warning("Отримання товарів користувача тимчасово відключено (немає моделі Product).")

    except Exception as e:
        logger.error(f"Помилка при отриманні товарів для користувача {chat_id}: {e}")
        bot.send_message(chat_id, "❌ Не вдалося отримати список ваших товарів.")
        return
    finally:
        session.close()

    if user_products:
        response_parts = ["📋 *Ваші товари:*\n\n"]
        for i, product in enumerate(user_products, 1):
            status_emoji = {
                'pending': '⏳',
                'approved': '✅',
                'rejected': '❌',
                'sold': '💰',
                'expired': '🗑️'
            }
            status_ukr = {
                'pending': 'на розгляді',
                'approved': 'опубліковано',
                'rejected': 'відхилено',
                'sold': 'продано',
                'expired': 'термін дії закінчився'
            }.get(product['status'], product['status']) # Припускаємо, що product є dict-подібним або має атрибути

            product_info = (
                f"{i}. {status_emoji.get(product.status, '❓')} *{product.product_name}*\n"
                f"   💰 {product.price}\n"
                f"   📅 {product.created_at.strftime('%d.%m.%Y %H:%M')}\n"
                f"   📊 Статус: {status_ukr}\n"
            )
            
            if product.status == 'approved' and product.channel_message_id:
                product_info += f"   🔗 [Переглянути в каналі](https://t.me/c/{str(CHANNEL_ID)[4:]}/{product.channel_message_id})\n"
            
            response_parts.append(product_info + "\n")
        
        # Розбиваємо повідомлення, якщо воно занадто довге
        full_response = "".join(response_parts)
        if len(full_response) > 4096: # Ліміт Telegram на довжину повідомлення
            for i in range(0, len(full_response), 4000):
                bot.send_message(chat_id, full_response[i:i+4000], parse_mode='Markdown', disable_web_page_preview=True)
        else:
            bot.send_message(chat_id, full_response, parse_mode='Markdown', disable_web_page_preview=True)

    else:
        bot.send_message(chat_id, "📭 Ви ще не додавали жодних товарів.\n\nНатисніть '🔥 Продати товар' щоб створити своє перше оголошення!")

# --- 13. Допомога та Канал ---
@error_handler
def send_help_message(message):
    """Надсилає користувачу довідкову інформацію."""
    help_text = (
        "🆘 *Довідка*\n\n"
        "🤖 Я ваш AI-помічник для купівлі та продажу. Ви можете:\n"
        "🔥 *Продати товар* - створити оголошення.\n"
        "🛒 *Мої товари* - переглянути ваші активні та продані товари.\n"
        "💰 *Комісія* - інформація про комісійні збори.\n"
        "📺 *Наш канал* - переглянути всі актуальні пропозиції.\n"
        "🤖 *Запитати AI* - поспілкуватися з Grok AI.\n"
        "🎁 *Персональна пропозиція* - для замовлення ексклюзивного товару або послуги.\n"
        "👨‍💻 *Зв'язатися з адміном* - якщо AI не може допомогти, або у вас є складні питання.\n\n"
        "🗣️ *Спілкування:* Просто пишіть мені ваші запитання або пропозиції, і мій вбудований AI спробує вам допомогти!\n\n"
        "Якщо виникли технічні проблеми, зверніться до адміністратора." # TODO: Додати контакт адміна
    )
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown', reply_markup=main_menu_markup)

@error_handler
def send_commission_info(message):
    """Надсилає користувачу інформацію про комісію."""
    commission_rate_percent = 10 # Наприклад, 10%
    text = (
        f"💰 *Інформація про комісію*\n\n"
        f"За успішний продаж товару через нашого бота стягується комісія у розмірі **{commission_rate_percent}%** від кінцевої ціни продажу.\n\n"
        f"Після того, як ви позначите товар як 'Продано', система розрахує суму комісії, і ви отримаєте інструкції щодо її сплати.\n\n"
        f"Реквізити для сплати комісії (Monobank):\n`{MONOBANK_CARD_NUMBER}`\n\n"
        f"Будь ласка, сплачуйте комісію вчасно, щоб уникнути обмежень на використання бота.\n\n"
        f"Детальніше про ваші поточні нарахування та сплати можна буде дізнатися в розділі 'Мої товари'."
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=main_menu_markup)

@error_handler
def send_channel_link(message):
    """Надсилає посилання на канал."""
    chat_id = message.chat.id
    try:
        chat_info = bot.get_chat(CHANNEL_ID)
        channel_link = ""
        if chat_info.invite_link:
            channel_link = chat_info.invite_link
        elif chat_info.username:
            channel_link = f"https://t.me/{chat_info.username}"
        else:
            try:
                invite_link_obj = bot.create_chat_invite_link(CHANNEL_ID, member_limit=1)
                channel_link = invite_link_obj.invite_link
                logger.info(f"Згенеровано нове посилання на запрошення для каналу: {channel_link}")
            except telebot.apihelper.ApiTelegramException as e:
                logger.warning(f"Не вдалося створити посилання на запрошення для каналу {CHANNEL_ID}: {e}")
                channel_link = f"https://t.me/c/{str(CHANNEL_ID)[4:]}"

        if not channel_link:
             raise Exception("Не вдалося сформувати посилання на канал.")

        invite_text = (
            f"📺 *Наш канал з оголошеннями*\n\n"
            f"Приєднуйтесь до нашого каналу, щоб не пропустити нові товари!\n\n"
            f"👉 [Перейти до каналу]({channel_link})\n\n"
            f"💡 У каналі публікуються тільки перевірені оголошення"
        )
        bot.send_message(chat_id, invite_text, parse_mode='Markdown', disable_web_page_preview=True)
        log_statistics('channel_visit', chat_id)

    except Exception as e:
        logger.error(f"Помилка при отриманні або формуванні посилання на канал: {e}", exc_info=True)
        bot.send_message(chat_id, "❌ На жаль, посилання на канал тимчасово недоступне. Зверніться до адміністратора.")


# --- 14. Обробники Callback Query ---
@bot.callback_query_handler(func=lambda call: True)
@error_handler
def callback_inline(call):
    """Обробляє всі інлайн-кнопки."""
    if call.data.startswith('admin_'):
        handle_admin_callbacks(call)
    elif call.data.startswith('approve_') or call.data.startswith('reject_') or call.data.startswith('sold_'):
        handle_product_moderation_callbacks(call)
    elif call.data.startswith('user_block_') or call.data.startswith('user_unblock_'):
        handle_user_block_callbacks(call)
    elif call.data.startswith('accept_human_chat_'):
        accept_human_chat_callback(call)
    elif call.data.startswith('confirm_send_moderation') or call.data.startswith('cancel_add_product'):
        # handle_add_product_callbacks(call) # Ця функція не визначена, тому закоментована
        bot.answer_callback_query(call.id, "Невідома дія.") # Додано відповідь, якщо функція не існує
    else:
        bot.answer_callback_query(call.id, "Невідома дія.")

# --- 15. Callbacks для Адмін-панелі ---
@error_handler
def handle_admin_callbacks(call):
    """Обробляє колбеки, пов'язані з адмін-панеллю."""
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "❌ Доступ заборонено.")
        return

    action = call.data.split('_')[1]

    if action == "stats":
        send_admin_statistics(call)
    elif action == "pending": # admin_pending
        send_pending_products_for_moderation(call)
    elif action == "users": # admin_users
        send_users_list(call)
    elif action == "block": # admin_block
        bot.edit_message_text("Введіть `chat_id` або `@username` користувача для блокування/розблокування:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id, parse_mode='Markdown')
        # Встановлюємо статус для адміна, щоб обробити наступне повідомлення
        set_user_status(ADMIN_CHAT_ID, 'awaiting_user_for_block_unblock')
    elif action == "commissions":
        send_admin_commissions_info(call)
    elif action == "ai_stats":
        send_admin_ai_statistics(call)
    elif action == "faq_menu":
        send_admin_faq_menu(call)

    bot.answer_callback_query(call.id)

@error_handler
def send_admin_statistics(call):
    """Надсилає адміністратору статистику бота."""
    session = get_db_connection()
    try:
        # Статистика по товарах - TODO: Замінити на модель Product
        # product_stats = session.query(Product.status, func.count(Product.id)).group_by(Product.status).all()
        # product_stats_dict = {status: count for status, count in product_stats}
        product_stats_dict = {'pending': 0, 'approved': 0, 'rejected': 0, 'sold': 0, 'expired': 0} # Заглушка
        logger.warning("Статистика товарів тимчасово відключена (немає моделі Product).")

        # Статистика по користувачах
        total_users = session.query(User).count()
        blocked_users_count = session.query(User).filter_by(is_blocked=True).count()

        # Статистика за сьогодні - TODO: Замінити на модель Product
        # today = datetime.now().date()
        # today_products = session.query(Product).filter(func.date(Product.created_at) == today).count()
        today_products = 0 # Заглушка
        logger.warning("Статистика товарів за сьогодні тимчасово відключена (немає моделі Product).")

    except Exception as e:
        logger.error(f"Помилка при отриманні адміністративної статистики: {e}")
        bot.edit_message_text("❌ Помилка при отриманні статистики.", call.message.chat.id, call.message.message_id)
        return
    finally:
        session.close()

    stats_text = (
        f"📊 *Статистика бота*\n\n"
        f"👥 *Користувачі:*\n"
        f"• Всього: {total_users}\n"
        f"• Заблоковані: {blocked_users_count}\n\n"
        f"📦 *Товари:*\n"
        f"• На модерації: {product_stats_dict.get('pending', 0)}\n"
        f"• Опубліковано: {product_stats_dict.get('approved', 0)}\n"
        f"• Відхилено: {product_stats_dict.get('rejected', 0)}\n"
        f"• Продано: {product_stats_dict.get('sold', 0)}\n"
        f"• Термін дії закінчився: {product_stats_dict.get('expired', 0)}\n\n"
        f"📅 *Сьогодні додано:* {today_products}\n"
        f"📈 *Всього товарів:* {sum(product_stats_dict.values())}"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main"))

    bot.edit_message_text(stats_text, call.message.chat.id, call.message.message_id,
                         parse_mode='Markdown', reply_markup=markup)

@error_handler
def send_users_list(call):
    """Надсилає адміністратору список користувачів."""
    session = get_db_connection()
    try:
        users = session.query(User).order_by(User.joined_at.desc()).limit(20).all()
    except Exception as e:
        logger.error(f"Помилка при отриманні списку користувачів: {e}")
        bot.edit_message_text("❌ Помилка при отриманні списку користувачів.", call.message.chat.id, call.message.message_id)
        return
    finally:
        session.close()

    if not users:
        response_text = "🤷‍♂️ Немає зареєстрованих користувачів."
    else:
        response_text = "👥 *Список останніх користувачів:*\n\n"
        for user in users:
            block_status = "🚫 Заблоковано" if user.is_blocked else "✅ Активний"
            username = f"@{user.username}" if user.username else "Немає юзернейму"
            first_name = user.first_name if user.first_name else "Невідоме ім'я"
            response_text += f"- {first_name} ({username}) [ID: `{user.chat_id}`] - {block_status}\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main"))

    bot.edit_message_text(response_text, call.message.chat.id, call.message.message_id,
                         parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(func=lambda message: get_user_current_status(message.chat.id) == 'awaiting_user_for_block_unblock' and message.chat.id == ADMIN_CHAT_ID)
@error_handler
def process_user_for_block_unblock(message):
    """Обробляє введення користувача для блокування/розблокування."""
    admin_chat_id = message.chat.id
    target_identifier = message.text.strip()
    target_chat_id = None

    session = get_db_connection()
    try:
        if target_identifier.startswith('@'):
            username = target_identifier[1:]
            user = session.query(User).filter_by(username=username).first()
            if user:
                target_chat_id = user.chat_id
            else:
                bot.send_message(admin_chat_id, f"Користувача з юзернеймом `{target_identifier}` не знайдено.")
                set_user_status(admin_chat_id, 'idle') # Скидаємо статус
                return
        else:
            try:
                target_chat_id = int(target_identifier)
            except ValueError:
                bot.send_message(admin_chat_id, "Будь ласка, введіть дійсний `chat_id` (число) або `@username`.")
                set_user_status(admin_chat_id, 'idle') # Скидаємо статус
                return
    finally:
        session.close()

    if target_chat_id == ADMIN_CHAT_ID:
        bot.send_message(admin_chat_id, "Ви не можете заблокувати/розблокувати себе.")
        set_user_status(admin_chat_id, 'idle') # Скидаємо статус
        return

    if target_chat_id:
        current_status = is_user_blocked(target_chat_id)
        action_text = "заблокувати" if not current_status else "розблокувати"
        confirmation_text = f"Ви впевнені, що хочете {action_text} користувача з ID `{target_chat_id}` (натисніть кнопку)?\n"

        markup = types.InlineKeyboardMarkup()
        if not current_status:
            markup.add(types.InlineKeyboardButton("🚫 Заблокувати", callback_data=f"user_block_{target_chat_id}"))
        else:
            markup.add(types.InlineKeyboardButton("✅ Розблокувати", callback_data=f"user_unblock_{target_chat_id}"))
        markup.add(types.InlineKeyboardButton("Скасувати", callback_data="admin_panel_main"))

        bot.send_message(admin_chat_id, confirmation_text, reply_markup=markup, parse_mode='Markdown')
        set_user_status(admin_chat_id, 'idle') # Скидаємо статус після відправки підтвердження
    else:
        bot.send_message(admin_chat_id, "Користувача не знайдено.")
        set_user_status(admin_chat_id, 'idle') # Скидаємо статус

@error_handler
def handle_user_block_callbacks(call):
    """Обробляє колбеки блокування/розблокування користувачів."""
    admin_chat_id = call.message.chat.id
    data_parts = call.data.split('_')
    action = data_parts[1]
    target_chat_id = int(data_parts[2])

    if action == 'block':
        success = set_user_block_status(admin_chat_id, target_chat_id, True)
        if success:
            bot.edit_message_text(f"Користувача з ID `{target_chat_id}` успішно заблоковано.",
                                  chat_id=admin_chat_id, message_id=call.message.message_id, parse_mode='Markdown')
            try:
                bot.send_message(target_chat_id, "❌ Ваш акаунт було заблоковано адміністратором.")
            except Exception as e:
                logger.warning(f"Не вдалося повідомити заблокованого користувача {target_chat_id}: {e}")
            log_statistics('user_blocked', admin_chat_id, target_chat_id)
        else:
            bot.edit_message_text(f"❌ Помилка при блокуванні користувача з ID `{target_chat_id}`.",
                                  chat_id=admin_chat_id, message_id=call.message.message_id, parse_mode='Markdown')
    elif action == 'unblock':
        success = set_user_block_status(admin_chat_id, target_chat_id, False)
        if success:
            bot.edit_message_text(f"Користувача з ID `{target_chat_id}` успішно розблоковано.",
                                  chat_id=admin_chat_id, message_id=call.message.message_id, parse_mode='Markdown')
            try:
                bot.send_message(target_chat_id, "✅ Ваш акаунт було розблоковано адміністратором. Тепер ви можете користуватися ботом.")
            except Exception as e:
                logger.warning(f"Не вдалося повідомити розблокованого користувача {target_chat_id}: {e}")
            log_statistics('user_unblocked', admin_chat_id, target_chat_id)
        else:
            bot.edit_message_text(f"❌ Помилка при розблокуванні користувача з ID `{target_chat_id}`.",
                                  chat_id=admin_chat_id, message_id=call.message.message_id, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

@error_handler
def send_pending_products_for_moderation(call):
    """Надсилає адміністратору товари, що очікують модерації."""
    session = get_db_connection()
    try:
        # TODO: Реалізувати отримання товарів з БД за допомогою моделі Product
        # from your_product_model import Product
        # pending_products = session.query(Product).filter_by(status='pending').order_by(Product.created_at.asc()).limit(5).all()
        pending_products = [] # Заглушка
        logger.warning("Отримання товарів на модерацію тимчасово відключено (немає моделі Product).")
    except Exception as e:
        logger.error(f"Помилка при отриманні товарів на модерацію: {e}")
        bot.edit_message_text("❌ Помилка при отриманні товарів на модерацію.", call.message.chat.id, call.message.message_id)
        return
    finally:
        session.close()

    if not pending_products:
        response_text = "🎉 Немає товарів на модерації."
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main"))
        bot.edit_message_text(response_text, call.message.chat.id, call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id) # Важливо відповісти на callback
        return

    # Відправляємо товари по одному, як це робить send_for_moderation
    bot.edit_message_text("⏳ *Товари на модерації:*\n\nНадсилаю...",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)

    for product_row in pending_products:
        product_data = dict(product_row) # Конвертуємо Row в dict
        send_product_for_admin_review(product_data['id'], product_data, product_data['seller_chat_id'], product_data['seller_username'])
        time.sleep(0.5) # Затримка, щоб уникнути FloodWait

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main"))
    bot.send_message(call.message.chat.id, "✅ Всі товари на модерації відправлено.", reply_markup=markup)
    bot.answer_callback_query(call.id) # Важливо відповісти на callback

@error_handler
def send_admin_commissions_info(call):
    """Надсилає адміністратору інформацію про комісії."""
    session = get_db_connection()
    try:
        # TODO: Реалізувати моделі для комісій та статистики
        # commission_summary = session.query(
        #     func.sum(case((CommissionTransaction.status == 'pending_payment', CommissionTransaction.amount), else_=0)).label('total_pending'),
        #     func.sum(case((CommissionTransaction.status == 'paid', CommissionTransaction.amount), else_=0)).label('total_paid')
        # ).first()
        # recent_transactions = session.query(
        #     CommissionTransaction.product_id, Product.product_name, Product.seller_chat_id, User.username,
        #     CommissionTransaction.amount, CommissionTransaction.status, CommissionTransaction.created_at
        # ).join(Product).join(User).order_by(CommissionTransaction.created_at.desc()).limit(10).all()

        commission_summary = {'total_pending': 0, 'total_paid': 0} # Заглушка
        recent_transactions = [] # Заглушка
        logger.warning("Статистика комісій тимчасово відключена (немає моделей).")

    except Exception as e:
        logger.error(f"Помилка при отриманні інформації про комісії: {e}")
        bot.edit_message_text("❌ Помилка при отриманні інформації про комісії.", call.message.chat.id, call.message.message_id)
        return
    finally:
        session.close()

    text = (
        f"💰 *Статистика комісій*\n\n"
        f"• Всього очікується: *{commission_summary['total_pending'] or 0:.2f} грн*\n"
        f"• Всього сплачено: *{commission_summary['total_paid'] or 0:.2f} грн*\n\n"
        f"📊 *Останні транзакції:*\n"
    )

    if recent_transactions:
        for tx in recent_transactions:
            username = f"@{tx.username}" if tx.username else f"ID: {tx.seller_chat_id}"
            text += (
                f"- Товар ID `{tx.product_id}` ({tx.product_name})\n"
                f"  Продавець: {username}\n"
                f"  Сума: {tx.amount:.2f} грн, Статус: {tx.status}\n"
                f"  Дата: {tx.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            )
    else:
        text += "  Немає транзакцій комісій.\n\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    bot.answer_callback_query(call.id) # Важливо відповісти на callback

@error_handler
def send_admin_ai_statistics(call):
    """Надсилає адміністратору статистику використання AI."""
    session = get_db_connection()
    try:
        # TODO: Реалізувати модель Conversation
        # total_user_queries = session.query(Conversation).filter_by(sender_type='user').count()
        # top_ai_users = session.query(Conversation.user_chat_id, func.count(Conversation.id).label('query_count')).filter_by(sender_type='user').group_by(Conversation.user_chat_id).order_by(text('query_count DESC')).limit(5).all()
        # daily_ai_queries = session.query(func.date(Conversation.timestamp).label('date'), func.count(Conversation.id).label('query_count')).filter_by(sender_type='user').group_by(text('date')).order_by(text('date DESC')).limit(7).all()

        total_user_queries = 0 # Заглушка
        top_ai_users = [] # Заглушка
        daily_ai_queries = [] # Заглушка
        logger.warning("Статистика AI тимчасово відключена (немає моделі Conversation).")

    except Exception as e:
        logger.error(f"Помилка при отриманні AI статистики: {e}")
        bot.edit_message_text("❌ Помилка при отриманні AI статистики.", call.message.chat.id, call.message.message_id)
        return
    finally:
        session.close()

    text = (
        f"🤖 *Статистика AI Помічника*\n\n"
        f"• Всього запитів користувачів до AI: *{total_user_queries}*\n\n"
        f"📊 *Найактивніші користувачі AI:*\n"
    )
    if top_ai_users:
        for user_data in top_ai_users:
            user_id = user_data.user_chat_id
            user_info = bot.get_chat(user_id)
            username = f"@{user_info.username}" if user_info.username else f"ID: {user_id}"
            text += f"- {username}: {user_data.query_count} запитів\n"
    else:
        text += "  Немає даних.\n"

    text += "\n📅 *Запити за останні 7 днів:*\n"
    if daily_ai_queries:
        for day_data in daily_ai_queries:
            text += f"- {day_data.date}: {day_data.query_count} запитів\n"
    else:
        text += "  Немає даних.\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    bot.answer_callback_query(call.id) # Важливо відповісти на callback

# --- Керування FAQ в адмін-панелі ---
@error_handler
def send_admin_faq_menu(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Додати питання/відповідь", callback_data="admin_faq_add"),
        types.InlineKeyboardButton("📋 Переглянути всі FAQ", callback_data="admin_faq_view_all"),
        types.InlineKeyboardButton("🗑️ Видалити питання/відповідь", callback_data="admin_faq_delete"),
        types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main")
    )
    bot.edit_message_text("📚 *Керування FAQ*\n\nОберіть дію:",
                          chat_id=call.message.chat.id, message_id=call.message.message_id,
                          reply_markup=markup, parse_mode='Markdown')
    bot.answer_callback_query(call.id) # Важливо відповісти на callback

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_faq_'))
@error_handler
def handle_admin_faq_callbacks(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "❌ Доступ заборонено.")
        return

    action = call.data.split('_')[2]

    if action == "add":
        bot.edit_message_text("➕ *Додавання FAQ*\n\nВведіть питання, яке ви хочете додати:",
                              chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')
        set_user_status(ADMIN_CHAT_ID, 'awaiting_faq_question')
    elif action == "view": # admin_faq_view_all
        all_faq = get_all_faq_entries()
        if not all_faq:
            response_text = "🤷‍♂️ База знань FAQ порожня."
        else:
            response_text = "📚 *Всі питання та відповіді (FAQ):*\n\n"
            for faq_id, question, answer in all_faq: # Припускаємо, що all_faq повертає кортежі
                response_text += f"*{faq_id}. Питання*: {question}\n"
                response_text += f"*Відповідь*: {answer}\n\n"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️ Назад до FAQ меню", callback_data="admin_faq_menu"))
        bot.edit_message_text(response_text, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    elif action == "delete":
        bot.edit_message_text("🗑️ *Видалення FAQ*\n\nВведіть ID питання, яке ви хочете видалити (ви можете переглянути всі ID, обравши 'Переглянути всі FAQ'):",
                              chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')
        set_user_status(ADMIN_CHAT_ID, 'awaiting_faq_delete_id')
    
    bot.answer_callback_query(call.id)

def send_admin_faq_menu_after_action(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Додати питання/відповідь", callback_data="admin_faq_add"),
        types.InlineKeyboardButton("📋 Переглянути всі FAQ", callback_data="admin_faq_view_all"),
        types.InlineKeyboardButton("🗑️ Видалити питання/відповідь", callback_data="admin_faq_delete"),
        types.InlineKeyboardButton("⬅️ Назад до Адмін-панелі", callback_data="admin_panel_main")
    )
    bot.send_message(ADMIN_CHAT_ID, "📚 *Керування FAQ*\n\nОберіть дію:",
                          reply_markup=markup, parse_mode='Markdown')


# --- 16. Callbacks для модерації товару ---
@error_handler
def handle_product_moderation_callbacks(call):
    """Обробляє колбеки схвалення/відхилення/продажу товару."""
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "❌ Доступ заборонено.")
        return

    data_parts = call.data.split('_')
    action = data_parts[0]
    product_id = int(data_parts[1])

    session = get_db_connection()
    product_info = None
    try:
        # TODO: Реалізувати отримання товару з БД за допомогою моделі Product
        # from your_product_model import Product
        # product_info = session.query(Product).filter_by(id=product_id).first()
        
        # Заглушка для product_info, якщо модель Product не реалізована
        product_info = {
            'id': product_id,
            'seller_chat_id': 12345, # Заглушка
            'product_name': 'Тестовий товар', # Заглушка
            'price': '100 грн', # Заглушка
            'description': 'Це тестовий опис товару для модерації.', # Заглушка
            'photos': '[]', # Заглушка
            'geolocation': 'null', # Заглушка
            'admin_message_id': call.message.message_id, # Використовуємо поточний message_id для оновлення
            'channel_message_id': None, # Заглушка
            'status': 'pending' # Заглушка
        }
        logger.warning(f"Отримання інформації про товар {product_id} тимчасово відключено (немає моделі Product).")

    except Exception as e:
        logger.error(f"Помилка при отриманні інформації про товар {product_id} для модерації: {e}")
        bot.answer_callback_query(call.id, "❌ Помилка при отриманні інформації про товар.")
        session.close()
        return

    if not product_info:
        bot.answer_callback_query(call.id, "Товар не знайдено.")
        session.close()
        return

    seller_chat_id = product_info['seller_chat_id']
    product_name = product_info['product_name']
    price = product_info['price']
    description = product_info['description']
    photos_str = product_info['photos']
    geolocation_str = product_info['geolocation']
    admin_message_id = product_info['admin_message_id']
    channel_message_id = product_info['channel_message_id']
    current_status = product_info['status']

    photos = json.loads(photos_str) if photos_str else []
    geolocation = json.loads(geolocation_str) if geolocation_str else None
    hashtags = generate_hashtags(description)

    # Визначаємо, чи було повідомлення, на яке відповів адмін, фотографією
    # Це можна перевірити, дивлячись на `message_id` в `call.message`
    # та порівнюючи його з `admin_message_id` з БД.
    # Якщо `call.message.photo` не порожній, то це фото.
    is_call_message_photo = bool(call.message.photo)


    try:
        if action == 'approve':
            if current_status != 'pending':
                bot.answer_callback_query(call.id, f"Товар вже має статус '{current_status}'.")
                return

            # Публікація в каналі
            channel_text = (
                f"🔥 *НОВИЙ ТОВАР!* 🔥\\n\\n"
                f"📝 *Назва:* {product_name}\\n"
                f"📄 *Опис:* {description}\\n"
                f"💰 *Ціна:* {price} UAH\\n"
                f"📍 *Геолокація:* {'Присутня' if geolocation else 'Відсутня'}\\n\\n"
                f"🆔 *ID товару:* #{product_id}\\n\n"
                f"📩 *Для зв'язку з продавцем:* @{bot.get_chat(seller_chat_id).username or 'користувач'}"
            )
            
            new_channel_message_id = None
            if photos:
                channel_media_group = []
                # Додаємо перше фото з підписом
                channel_media_group.append(types.InputMediaPhoto(photos[0], caption=channel_text, parse_mode='Markdown'))
                # Додаємо решту фото без підпису
                for photo_id in photos[1:]:
                    channel_media_group.append(types.InputMediaPhoto(photo_id))
                
                # Надсилаємо медіа-групу в канал
                sent_channel_messages = bot.send_media_group(CHANNEL_ID, channel_media_group)
                new_channel_message_id = sent_channel_messages[0].message_id # ID першого повідомлення в альбомі
            else:
                # Якщо фото немає, надсилаємо як звичайне текстове повідомлення
                published_message = bot.send_message(CHANNEL_ID, channel_text, parse_mode='Markdown')
                new_channel_message_id = published_message.message_id

            if new_channel_message_id: # Перевіряємо, чи повідомлення було успішно надіслано в канал
                # TODO: Оновити статус товару в БД на 'approved' та зберегти channel_message_id
                # product_info.status = 'approved'
                # product_info.moderator_id = call.message.chat.id
                # product_info.moderated_at = datetime.now()
                # product_info.channel_message_id = new_channel_message_id
                # session.commit()
                logger.warning(f"Статус товару {product_id} не оновлено в БД (немає моделі Product).")

                log_statistics('product_approved', call.message.chat.id, product_id)
                bot.send_message(seller_chat_id,
                                 f"✅ Ваш товар '{product_name}' успішно опубліковано в каналі! [Переглянути](https://t.me/c/{str(CHANNEL_ID)[4:]}/{new_channel_message_id})",
                                 parse_mode='Markdown', disable_web_page_preview=True)
                
                # Оновлюємо адмінське повідомлення (те, яке адмін натиснув)
                admin_update_text = f"✅ Товар *'{product_name}'* (ID: {product_id}) опубліковано."
                # Перевіряємо, чи є у вихідному повідомленні фотографії
                if is_call_message_photo: # Якщо оригінальне повідомлення адміну було фото з підписом
                    try:
                        bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                                 caption=admin_update_text, parse_mode='Markdown')
                    except telebot.apihelper.ApiTelegramException as e:
                        logger.error(f"Помилка при спробі edit_message_caption для адмін-повідомлення {call.message.message_id}: {e}")
                        # Fallback: якщо не вдалося відредагувати, відправляємо нове повідомлення
                        bot.send_message(call.message.chat.id, admin_update_text, parse_mode='Markdown') 
                else: # Оригінальне повідомлення було текстовим
                    bot.edit_message_text(admin_update_text,
                                          chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')
                
                # Додаємо кнопку "Продано" до оновленого адмінського повідомлення
                markup_sold = types.InlineKeyboardMarkup()
                markup_sold.add(types.InlineKeyboardButton("💰 Відмітити як продано", callback_data=f"sold_{product_id}"))
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup_sold)

            else:
                raise Exception("Не вдалося опублікувати повідомлення в канал.")

        elif action == 'reject':
            if current_status != 'pending':
                bot.answer_callback_query(call.id, f"Товар вже має статус '{current_status}'.")
                return

            # TODO: Оновити статус товару в БД на 'rejected'
            # product_info.status = 'rejected'
            # product_info.moderator_id = call.message.chat.id
            # product_info.moderated_at = datetime.now()
            # session.commit()
            logger.warning(f"Статус товару {product_id} не оновлено в БД (немає моделі Product).")

            log_statistics('product_rejected', call.message.chat.id, product_id)
            bot.send_message(seller_chat_id,
                             f"❌ Ваш товар '{product_name}' було відхилено адміністратором.\n\n"
                             "Можливі причини: невідповідність правилам, низька якість фото, неточний опис.\n"
                             "Будь ласка, перевірте оголошення та спробуйте додати знову.",
                             parse_mode='Markdown')
            # FIX: Оновлюємо адмінське повідомлення залежно від типу оригінального повідомлення
            admin_update_text = f"❌ Товар *'{product_name}'* (ID: {product_id}) відхилено."
            if is_call_message_photo: # Якщо оригінальне повідомлення адміну було фото з підписом
                try:
                    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                             caption=admin_update_text, parse_mode='Markdown')
                    # Також видаляємо кнопки з окремого повідомлення, якщо воно було
                    # (це може бути складно, якщо не зберігається message_id цього окремого повідомлення)
                    # Якщо кнопки були прикріплені до фото, то вони видаляться разом з edit_message_caption.
                except telebot.apihelper.ApiTelegramException as e:
                    logger.error(f"Помилка при спробі edit_message_caption для адмін-повідомлення {call.message.message_id}: {e}")
                    # Fallback: якщо не вдалося відредагувати, відправляємо нове повідомлення
                    bot.send_message(call.message.chat.id, admin_update_text, parse_mode='Markdown') 
            else: # Оригінальне повідомлення було текстовим
                bot.edit_message_text(admin_update_text,
                                      chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')
            # Видаляємо інлайн-клавіатуру після відхилення
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

        elif action == 'sold':
            if current_status != 'approved':
                bot.answer_callback_query(call.id, f"Товар не опублікований або вже проданий (поточний статус: '{current_status}').")
                return

            if channel_message_id:
                try:
                    # TODO: Оновити статус товару в БД на 'sold'
                    # product_info.status = 'sold'
                    # product_info.moderator_id = call.message.chat.id
                    # product_info.moderated_at = datetime.now()
                    # session.commit()
                    logger.warning(f"Статус товару {product_id} не оновлено в БД (немає моделі Product).")

                    log_statistics('product_sold', call.message.chat.id, product_id)

                    # Оновлюємо повідомлення в каналі, додаючи "ПРОДАНО!"
                    original_caption_for_channel = description # Використовуємо повний опис з БД
                    sold_text = (
                        f"📦 *ПРОДАНО!* {product_name}\n\n"
                        f"{original_caption_for_channel}\n\n"
                        f"*Цей товар вже продано.*"
                    )
                    
                    # Визначаємо, чи було оригінальне повідомлення в каналі фотографією
                    is_channel_original_message_photo = bool(photos) # Якщо в БД є фото, значить, в каналі було фото
                    if is_channel_original_message_photo: # Якщо оригінальне повідомлення в каналі було фото з підписом
                        bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=channel_message_id,
                                                 caption=sold_text, parse_mode='Markdown')
                    else: # Оригінальне повідомлення в каналі було текстовим
                        bot.edit_message_text(chat_id=CHANNEL_ID, message_id=channel_message_id,
                                              text=sold_text, parse_mode='Markdown')
                    
                    bot.send_message(seller_chat_id, f"✅ Ваш товар '{product_name}' відмічено як *'ПРОДАНО'*. Дякуємо за співпрацю!", parse_mode='Markdown')
                    
                    # FIX: Оновлюємо адмінське повідомлення після продажу
                    admin_sold_text = f"💰 Товар *'{product_name}'* (ID: {product_id}) відмічено як проданий."
                    if is_call_message_photo: # Якщо оригінальне адмін-повідомлення було фото
                        try:
                            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                                     caption=admin_sold_text, parse_mode='Markdown')
                        except telebot.apihelper.ApiTelegramException as e:
                            logger.error(f"Помилка при спробі edit_message_caption для адмін-повідомлення {call.message.message_id} (sold): {e}")
                            # Fallback: якщо не вдалося відредагувати, відправляємо нове повідомлення
                            bot.send_message(call.message.chat.id, admin_sold_text, parse_mode='Markdown') 
                    else: # Оригінальне адмін-повідомлення було текстовим
                        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                              text=admin_sold_text, parse_mode='Markdown')
                    
                    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)

                except telebot.apihelper.ApiTelegramException as e:
                    logger.error(f"Помилка при відмітці товару {product_id} як проданого в каналі: {e}")
                    bot.send_message(call.message.chat.id, f"❌ Не вдалося оновити статус продажу в каналі для товару {product_id}. Можливо, повідомлення було видалено.")
                    bot.answer_callback_query(call.id, "❌ Помилка оновлення в каналі.")
                    return
            else:
                bot.send_message(call.message.chat.id, "Цей товар ще не опубліковано в каналі, або повідомлення в каналі відсутнє. Не можна відмітити як проданий.")
                bot.answer_callback_query(call.id, "Товар не опубліковано в каналі.")
    except Exception as e:
        session.rollback() # Відкочуємо зміни у разі помилки
        logger.error(f"Помилка під час модерації товару {product_id}, дія {action}: {e}", exc_info=True)
        bot.send_message(call.message.chat.id, f"❌ Виникла помилка під час виконання дії '{action}' для товару {product_id}.")
    finally:
        session.close()
    bot.answer_callback_query(call.id)

# --- 17. Повернення до адмін-панелі після колбеку ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_panel_main")
@error_handler
def back_to_admin_panel(call):
    """Повертає адміністратора до головного меню адмін-панелі."""
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "❌ Доступ заборонено.")
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

    bot.edit_message_text("🔧 *Адмін-панель*\n\nОберіть дію:",
                          chat_id=call.message.chat.id, message_id=call.message.message_id,
                          reply_markup=markup, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

# --- Webhook обробник для Flask (ПЕРЕМІЩЕНО В КІНЕЦЬ ФАЙЛУ) ---
# Це важливо, щоб WEBHOOK_URL_PATH та app були визначені до використання.
@app.route(WEBHOOK_URL_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '!', 200
    else:
        return '<h1>Hi, this is your Telegram bot!</h1>', 200 # Для перевірки, що сервер працює

# --- Запуск бота ---
if __name__ == '__main__':
    logger.info("Запуск ініціалізації БД...")
    init_db() # Викликаємо оновлену функцію ініціалізації
    
    # Встановлюємо вебхук при запуску
    logger.info("Видалення попереднього вебхука...")
    bot.remove_webhook() # Видаляємо попередній вебхук, якщо він був
    time.sleep(0.1) # Невелика затримка

    logger.info(f"Встановлення вебхука на: {WEBHOOK_URL_BASE}{WEBHOOK_URL_PATH}")
    bot.set_webhook(url=WEBHOOK_URL_BASE + WEBHOOK_URL_PATH)
    
    logger.info("Бот запускається...")
    # Запускаємо Flask додаток
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
    logger.info("Бот зупинено.")
