import sqlite3
import telebot
import logging
import os
from datetime import datetime, timedelta
import re # Для обробки тексту та хештегів

# --- 1. Ваш токен бота ---
# Рекомендується використовувати змінні середовища для безпеки
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8039977178:AAGS-GbH-lhljGGG6OgJ2iMU_ncB-JzeOvU')

# --- 2. ID адміністратора ---
# Знайдіть свій chat_id через @userinfobot у Telegram і вставте його сюди.
admin_chat_id_str = os.getenv('ADMIN_CHAT_ID', '8184456641').strip("'\"")
ADMIN_CHAT_ID = int(admin_chat_id_str)

# --- 3. ID каналу для публікацій ---
# ID каналу починається з '-100'
channel_id_str = os.getenv('CHANNEL_ID', '-1002535586055').strip("'\"")
CHANNEL_ID = int(channel_id_str)

# --- 4. Номер картки Monobank для оплати комісії ---
MONOBANK_CARD_NUMBER = '4441 1111 5302 1484'

# --- 5. Налаштування логування ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("bot.log"),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger(__name__)

# --- 6. Ініціалізація бота ---
bot = telebot.TeleBot(TOKEN)

# --- 7. Словник для зберігання даних користувачів під час процесу завантаження товару ---
# user_data[chat_id] = {'step': 'waiting_name', 'name': None, 'price': None, 'description': None, 'photos': []}
user_data = {}

# --- Функція для ініціалізації бази даних SQLite ---
def init_db():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_chat_id INTEGER NOT NULL,
            seller_username TEXT, -- Додано для зручності
            product_name TEXT NOT NULL,
            price TEXT NOT NULL,
            description TEXT NOT NULL,
            photos TEXT, -- Зберігатимемо список photo_file_id через кому
            status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected', 'sold'
            admin_message_id INTEGER, -- ID повідомлення адміністратору для подальшої зміни
            channel_message_id INTEGER, -- ID повідомлення в каналі після публікації
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Додаємо стовпець seller_chat_id, якщо його не існує (для існуючих баз даних)
    try:
        cursor.execute("SELECT seller_chat_id FROM products LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE products ADD COLUMN seller_chat_id INTEGER")
        logging.info("Додано стовпець 'seller_chat_id' до таблиці 'products'.")

    # Додаємо стовпець channel_message_id, якщо його не існує (для існуючих баз даних)
    try:
        cursor.execute("SELECT channel_message_id FROM products LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE products ADD COLUMN channel_message_id INTEGER")
        logging.info("Додано стовпець 'channel_message_id' до таблиці 'products'.")

    # Створюємо таблицю для користувачів
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_blocked INTEGER DEFAULT 0, -- 0 - не заблокований, 1 - заблокований
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Створюємо таблицю для статистики дій
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL, -- наприклад, 'start', 'product_submitted', 'product_approved', 'product_sold'
            user_id INTEGER NOT NULL,
            product_id INTEGER, -- може бути NULL, якщо дія не стосується товару
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База даних ініціалізована або вже існує.")

# --- Функція для збереження/оновлення інформації про користувача ---
def save_user(message):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (chat_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    ''', (message.chat.id, message.from_user.username, 
          message.from_user.first_name, message.from_user.last_name))
    conn.commit()
    conn.close()

# --- Функція для перевірки, чи заблокований користувач ---
def is_user_blocked(chat_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM users WHERE chat_id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

# --- Функція для блокування/розблокування користувача ---
def set_user_block_status(chat_id, status):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_blocked = ? WHERE chat_id = ?", (status, chat_id))
    conn.commit()
    conn.close()

# --- Функція для генерації хештегів ---
def generate_hashtags(description, num_hashtags=5):
    # Прибираємо пунктуацію та розбиваємо на слова
    words = re.findall(r'\b\w+\b', description.lower())
    
    # Фільтруємо короткі та поширені слова, щоб отримати більш значущі
    stopwords = set(['я', 'ми', 'ти', 'ви', 'він', 'вона', 'воно', 'вони', 'це', 'що', 'як', 'де', 'коли', 'а', 'і', 'та', 'або', 'чи', 'для', 'з', 'на', 'у', 'в', 'до', 'від', 'по', 'за', 'при', 'про', 'між', 'під', 'над', 'без', 'через', 'дуже', 'цей', 'той', 'мій', 'твій', 'наш', 'ваш'])
    filtered_words = [word for word in words if len(word) > 2 and word not in stopwords]
    
    # Вибираємо унікальні слова
    unique_words = list(set(filtered_words))
    
    # Беремо перші `num_hashtags` унікальних слів або менше, якщо їх недостатньо
    hashtags = ['#' + word for word in unique_words[:num_hashtags]]
    
    return " ".join(hashtags) if hashtags else ""

# --- Функція для логування статистики дій ---
def log_statistics(action, user_id, product_id=None):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO statistics (action, user_id, product_id)
        VALUES (?, ?, ?)
    ''', (action, user_id, product_id))
    conn.commit()
    conn.close()

# --- Обробник команди /start ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "❌ Ваш акаунт заблоковано адміністратором.")
        return
    
    save_user(message) # Зберігаємо інформацію про користувача
    log_statistics('start', message.chat.id) # Логуємо дію
    
    logger.info(f"Користувач {message.chat.id} розпочав взаємодію з ботом.")
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    item_add = telebot.types.KeyboardButton("📦 Додати товар")
    item_my = telebot.types.KeyboardButton("📋 Мої товари")
    item_help = telebot.types.KeyboardButton("❓ Допомога")
    item_channel = telebot.types.KeyboardButton("📺 Наш канал")
    markup.add(item_add, item_my)
    markup.add(item_help, item_channel)
    
    welcome_text = (
        "🛍️ *Ласкаво просимо до бота оголошень!*\n\n"
        "Тут ви можете:\n"
        "📦 Додавати товари на продаж\n"
        "📋 Переглядати свої оголошення\n"
        "📺 Переходити до нашого каналу\n\n"
        "Оберіть дію з меню нижче:"
    )
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode='Markdown')

# --- Команди для адміністратора ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "❌ У вас немає прав доступу до адмін-панелі.")
        return
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"))
    markup.add(telebot.types.InlineKeyboardButton("👥 Користувачі", callback_data="admin_users"))
    markup.add(telebot.types.InlineKeyboardButton("🚫 Заблоковані", callback_data="admin_blocked"))
    
    bot.send_message(message.chat.id, "🔧 *Адмін-панель*\n\nОберіть дію:", 
                     reply_markup=markup, parse_mode='Markdown')

# --- Обробник текстових повідомлень ---
@bot.message_handler(content_types=['text'])
def handle_text(message):
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "❌ Ваш акаунт заблоковано адміністратором.")
        return
    
    chat_id = message.chat.id
    
    if message.text in ["📦 Додати товар", "Додати товар"]:
        start_add_product(message)
    elif message.text in ["📋 Мої товари", "Мої товари"]:
        send_my_products(message)
    elif message.text in ["❓ Допомога", "Допомога"]:
        send_help(message)
    elif message.text in ["📺 Наш канал", "Наш канал"]:
        send_channel_link(message)
    elif chat_id in user_data:
        process_product_input(message)
    else:
        bot.send_message(chat_id, "🤖 Будь ласка, скористайтеся кнопками меню або командою /start.")

# --- Функція допомоги ---
def send_help(message):
    help_text = (
        "🆘 *Довідка по використанню бота*\n\n"
        "📦 *Додати товар* - створити нове оголошення\n"
        "📋 *Мої товари* - переглянути ваші оголошення\n"
        "📺 *Наш канал* - посилання на канал з оголошеннями\n\n"
        "📝 *Процес додавання товару:*\n"
        "1️⃣ Введіть назву товару\n"
        "2️⃣ Вкажіть ціну\n"
        "3️⃣ Додайте фото (до 10 штук)\n"
        "4️⃣ Напишіть опис\n"
        "5️⃣ Очікуйте модерацію\n\n"
        "💰 *Комісія:* 10% з продажу\n"
        "⏱️ *Модерація:* зазвичай до 24 годин\n\n"
        "❓ Питання? Зверніться до @admin" # Замініть на реальний юзернейм адміністратора
    )
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')

# --- Функція для обробки кнопки "Мої товари" ---
def send_my_products(message):
    chat_id = message.chat.id
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT product_name, status, price, created_at 
        FROM products 
        WHERE seller_chat_id = ? 
        ORDER BY created_at DESC
    """, (chat_id,))
    user_products = cursor.fetchall()
    conn.close()

    if user_products:
        response = "📋 *Ваші товари:*\n\n"
        for i, (name, status, price, created_at) in enumerate(user_products, 1):
            status_emoji = {
                'pending': '⏳',
                'approved': '✅',
                'rejected': '❌',
                'sold': '💰'
            }
            status_ukr = {
                'pending': 'на розгляді',
                'approved': 'опубліковано',
                'rejected': 'відхилено',
                'sold': 'продано'
            }.get(status, status)
            
            response += f"{i}. {status_emoji.get(status, '❓')} *{name}*\n"
            response += f"   💰 {price}\n"
            response += f"   📅 {created_at[:10]}\n" # Показуємо тільки дату
            response += f"   📊 Статус: {status_ukr}\n\n"
            
        bot.send_message(chat_id, response, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "📭 Ви ще не додавали жодних товарів.\n\nНатисніть '📦 Додати товар' щоб створити своє перше оголошення!")

# --- Функція для обробки кнопки "Наш канал" ---
def send_channel_link(message):
    chat_id = message.chat.id
    
    try:
        # Спробуємо отримати інформацію про канал
        chat_info = bot.get_chat(CHANNEL_ID)
        
        channel_link = ""
        if chat_info.invite_link:
            channel_link = chat_info.invite_link
        elif chat_info.username:
            channel_link = f"https://t.me/{chat_info.username}"
        else:
            # Якщо немає ні invite_link, ні username, спробуємо створити запрошення
            # Це може вимагати прав "Створювати посилання-запрошення" у бота
            try:
                invite_link_obj = bot.create_chat_invite_link(CHANNEL_ID, member_limit=1)
                channel_link = invite_link_obj.invite_link
                logger.info(f"Згенеровано нове посилання на запрошення для каналу: {channel_link}")
            except telebot.apihelper.ApiTelegramException as e:
                logger.warning(f"Не вдалося створити посилання-запрошення для каналу {CHANNEL_ID}: {e}")
                # Fallback до статичного посилання, якщо створення не вдалося
                if str(CHANNEL_ID).startswith('-100'):
                    channel_link = f"https://t.me/c/{str(CHANNEL_ID)[4:]}"
                else:
                    channel_link = f"https://t.me/your_channel_username_here" # Якщо CHANNEL_ID - це username
                
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

# --- Початок процесу додавання товару ---
def start_add_product(message):
    chat_id = message.chat.id
    
    # Перевіряємо ліміти користувача на кількість товарів на модерації
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM products 
        WHERE seller_chat_id = ? AND status = 'pending'
    """, (chat_id,))
    pending_count = cursor.fetchone()[0]
    conn.close()
    
    if pending_count >= 3:  # Ліміт на 3 товари на модерації
        bot.send_message(chat_id, 
                        "⚠️ У вас вже є 3 товари на модерації.\n"
                        "Дочекайтеся розгляду поточних оголошень перед додаванням нових.")
        return
    
    user_data[chat_id] = {'step': 'waiting_name', 'photos': []}
    bot.send_message(chat_id, 
                    "📝 *Додавання нового товару*\n\n"
                    "Крок 1/4: Введіть назву вашого товару\n\n"
                    "💡 Назва має бути зрозумілою та конкретною",
                    parse_mode='Markdown')
    log_statistics('start_add_product', chat_id)

# --- Обробка введених даних про товар ---
@bot.message_handler(content_types=['text']) # Цей обробник потрібен для текстових відповідей
def process_product_input(message):
    chat_id = message.chat.id
    
    # Перевіряємо, чи користувач знаходиться в процесі додавання товару
    if chat_id not in user_data:
        # Якщо ні, це текстове повідомлення, яке не є частиною процесу додавання товару.
        # Загальний handle_text вже обробляє кнопки, тому тут можемо просто вийти.
        return 

    current_step = user_data[chat_id]['step']

    if current_step == 'waiting_name':
        if not (3 <= len(message.text) <= 100):
            bot.send_message(chat_id, "❌ Назва товару повинна бути від 3 до 100 символів.")
            return
            
        user_data[chat_id]['name'] = message.text
        user_data[chat_id]['step'] = 'waiting_price'
        bot.send_message(chat_id, 
                        "💰 *Крок 2/4: Ціна*\n\n"
                        "Введіть ціну товару (наприклад, '100 грн', '50$', 'Договірна' або 'Обмін'):",
                        parse_mode='Markdown')
        
    elif current_step == 'waiting_price':
        if len(message.text) > 50:
            bot.send_message(chat_id, "❌ Ціна занадто 