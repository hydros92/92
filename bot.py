import sqlite3
import telebot
import logging
import os
from datetime import datetime, timedelta
import re
import threading
import time

# --- 1. Ваш токен бота ---
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8039977178:AAGS-GbH-lhljGGG6OgJ2iMU_ncB-JzeOvU')

# --- 2. ID адміністратора ---
admin_chat_id_str = os.getenv('ADMIN_CHAT_ID', '8184456641').strip("'\"")
ADMIN_CHAT_ID = int(admin_chat_id_str)

# --- 3. ID каналу для публікацій ---
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

# --- 7. Словник для зберігання даних користувачів ---
user_data = {}

# --- 8. Блокування користувачів (адмін функція) ---
blocked_users = set()

# --- 9. Статистика ---
stats = {
    'total_products': 0,
    'approved_products': 0,
    'rejected_products': 0,
    'sold_products': 0
}

# --- Функція для ініціалізації бази даних SQLite ---
def init_db():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_chat_id INTEGER NOT NULL,
            seller_username TEXT,
            product_name TEXT NOT NULL,
            price TEXT NOT NULL,
            description TEXT NOT NULL,
            photos TEXT,
            status TEXT DEFAULT 'pending',
            admin_message_id INTEGER,
            channel_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Створюємо таблицю для користувачів
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_blocked INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Створюємо таблицю для статистики
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            user_id INTEGER,
            product_id INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База даних ініціалізована або вже існує.")

# --- Функція для збереження користувача ---
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

# --- Функція для перевірки блокування ---
def is_user_blocked(chat_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM users WHERE chat_id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

# --- Функція для генерації хештегів ---
def generate_hashtags(description, num_hashtags=5):
    words = re.findall(r'\b\w+\b', description.lower())
    
    stopwords = set(['я', 'ми', 'ти', 'ви', 'він', 'вона', 'воно', 'вони', 'це', 'що', 
                     'як', 'де', 'коли', 'а', 'і', 'та', 'або', 'чи', 'для', 'з', 'на', 
                     'у', 'в', 'до', 'від', 'по', 'за', 'при', 'про', 'між', 'під', 'над', 
                     'без', 'через', 'дуже', 'цей', 'той', 'мій', 'твій', 'наш', 'ваш'])
    
    filtered_words = [word for word in words if len(word) > 2 and word not in stopwords]
    unique_words = list(set(filtered_words))
    hashtags = ['#' + word for word in unique_words[:num_hashtags]]
    
    return " ".join(hashtags) if hashtags else ""

# --- Функція для логування статистики ---
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
    
    save_user(message)
    log_statistics('start', message.chat.id)
    
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
        "❓ Питання? Зверніться до @admin"
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
            response += f"   📅 {created_at[:10]}\n"
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
        
        if chat_info.invite_link:
            channel_link = chat_info.invite_link
        elif chat_info.username:
            channel_link = f"https://t.me/{chat_info.username}"
        else:
            # Створюємо запрошення
            invite_link = bot.create_chat_invite_link(CHANNEL_ID, member_limit=1)
            channel_link = invite_link.invite_link
            
        invite_text = (
            f"📺 *Наш канал з оголошеннями*\n\n"
            f"Приєднуйтесь до нашого каналу, щоб не пропустити нові товари!\n\n"
            f"👉 [Перейти до каналу]({channel_link})\n\n"
            f"💡 У каналі публікуються тільки перевірені оголошення"
        )
        
        bot.send_message(chat_id, invite_text, parse_mode='Markdown', disable_web_page_preview=True)
        log_statistics('channel_visit', chat_id)
        
    except Exception as e:
        logger.error(f"Помилка при отриманні посилання на канал: {e}")
        bot.send_message(chat_id, "❌ На жаль, посилання на канал тимчасово недоступне. Зверніться до адміністратора.")

# --- Початок процесу додавання товару ---
def start_add_product(message):
    chat_id = message.chat.id
    
    # Перевіряємо ліміти користувача
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM products 
        WHERE seller_chat_id = ? AND status = 'pending'
    """, (chat_id,))
    pending_count = cursor.fetchone()[0]
    conn.close()
    
    if pending_count >= 3:  # Ліміт на кількість товарів на модерації
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
def process_product_input(message):
    chat_id = message.chat.id
    
    if chat_id not in user_data:
        return 

    current_step = user_data[chat_id]['step']

    if current_step == 'waiting_name':
        if len(message.text) > 100:
            bot.send_message(chat_id, "❌ Назва товару занадто довга. Максимум 100 символів.")
            return
        if len(message.text) < 3:
            bot.send_message(chat_id, "❌ Назва товару занадто коротка. Мінімум 3 символи.")
            return
            
        user_data[chat_id]['name'] = message.text
        user_data[chat_id]['step'] = 'waiting_price'
        bot.send_message(chat_id, 
                        "💰 *Крок 2/4: Ціна*\n\n"
                        "Введіть ціну товару:\n"
                        "• 100 грн\n"
                        "• 50$\n"
                        "• Договірна\n"
                        "• Обмін",
                        parse_mode='Markdown')
        
    elif current_step == 'waiting_price':
        if len(message.text) > 50:
            bot.send_message(chat_id, "❌ Ціна занадто довга. Максимум 50 символів.")
            return
            
        user_data[chat_id]['price'] = message.text
        user_data[chat_id]['step'] = 'waiting_photos'
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(telebot.types.KeyboardButton("⏭️ Пропустити фото"))
        bot.send_message(chat_id, 
                        "📸 *Крок 3/4: Фотографії*\n\n"
                        "Надішліть фотографії товару (до 10 фото).\n"
                        "Кожне фото - окремим повідомленням.\n\n"
                        "💡 Якісні фото збільшують шанси продажу!\n\n"
                        "Коли закінчите, натисніть '⏭️ Пропустити фото'", 
                        reply_markup=markup, parse_mode='Markdown')
        
    elif current_step == 'waiting_description':
        if len(message.text) > 1000:
            bot.send_message(chat_id, "❌ Опис занадто довгий. Максимум 1000 символів.")
            return
        if len(message.text) < 10:
            bot.send_message(chat_id, "❌ Опис занадто короткий. Мінімум 10 символів.")
            return
            
        user_data[chat_id]['description'] = message.text
        hashtags = generate_hashtags(message.text)
        user_data[chat_id]['hashtags'] = hashtags
        send_for_moderation(chat_id)
        
    elif current_step == 'waiting_photos':
        if message.text == "⏭️ Пропустити фото":
            user_data[chat_id]['step'] = 'waiting_description'
            bot.send_message(chat_id, 
                            "📝 *Крок 4/4: Опис*\n\n"
                            "Надайте детальний опис товару:\n"
                            "• Стан товару\n"
                            "• Особливості\n"
                            "• Причина продажу\n"
                            "• Умови передачі\n\n"
                            "💡 Детальний опис допомагає швидше знайти покупця!",
                            reply_markup=telebot.types.ReplyKeyboardRemove(),
                            parse_mode='Markdown')
        else:
            bot.send_message(chat_id, "📸 Будь ласка, надішліть фотографії або натисніть '⏭️ Пропустити фото'.")

# --- Обробка фотографій ---
@bot.message_handler(content_types=['photo'])
def handle_photos(message):
    if is_user_blocked(message.chat.id):
        return
        
    chat_id = message.chat.id
    if chat_id in user_data and user_data[chat_id]['step'] == 'waiting_photos':
        photo_file_id = message.photo[-1].file_id
        if len(user_data[chat_id]['photos']) < 10:
            user_data[chat_id]['photos'].append(photo_file_id)
            bot.send_message(chat_id, f"✅ Фото {len(user_data[chat_id]['photos'])}/10 додано.")
        else:
            bot.send_message(chat_id, "⚠️ Максимум 10 фото. Натисніть '⏭️ Пропустити фото' для продовження.")
    else:
        bot.send_message(chat_id, "❌ Зараз не час для завантаження фото. Натисніть '📦 Додати товар' для початку.")

# --- Відправка товару на модерацію ---
def send_for_moderation(chat_id):
    data = user_data[chat_id]
    name = data['name']
    price = data['price']
    description = data['description']
    photos = data['photos']
    hashtags = data.get('hashtags', '')

    # Отримуємо інформацію про користувача
    try:
        user_info = bot.get_chat(chat_id)
        username = user_info.username or "Немає"
    except:
        username = "Невідомо"

    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO products 
        (seller_chat_id, seller_username, product_name, price, description, photos, status) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (chat_id, username, name, price, description, ",".join(photos), 'pending'))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    log_statistics('product_submitted', chat_id, product_id)

    admin_message_text = (
        f"📩 *Новий товар на модерацію!*\n\n"
        f"🆔 *ID:* {product_id}\n"
        f"📦 *Назва:* {name}\n"
        f"💰 *Ціна:* {price}\n"
        f"📝 *Опис:* {description}\n"
        f"🏷️ *Хештеги:* {hashtags}\n\n"
        f"👤 *Продавець:* [{'@' + username if username != 'Немає' else 'Користувач'}](tg://user?id={chat_id})\n"
        f"📸 *Фото:* {len(photos)} шт.\n"
        f"📅 *Дата:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    markup_admin = telebot.types.InlineKeyboardMarkup()
    markup_admin.add(
        telebot.types.InlineKeyboardButton("✅ Опублікувати", callback_data=f"approve_{product_id}"),
        telebot.types.InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{product_id}")
    )

    try:
        if photos:
            # Відправляємо перше фото з описом
            admin_msg = bot.send_photo(ADMIN_CHAT_ID, photos[0], 
                                     caption=admin_message_text, 
                                     parse_mode='Markdown', 
                                     reply_markup=markup_admin)
            
            # Якщо є додаткові фото, відправляємо їх альбомом
            if len(photos) > 1:
                media = [telebot.types.InputMediaPhoto(photo_id) for photo_id in photos[1:]]
                bot.send_media_group(ADMIN_CHAT_ID, media)
        else:
            admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_message_text, 
                                       parse_mode='Markdown', 
                                       reply_markup=markup_admin)
            
        # Оновлюємо admin_message_id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET admin_message_id = ? WHERE id = ?", 
                      (admin_msg.message_id, product_id))
        conn.commit()
        conn.close()

        # Відправляємо підтвердження користувачу
        confirmation_text = (
            f"✅ *Товар відправлено на модерацію!*\n\n"
            f"📦 *Назва:* {name}\n"
            f"💰 *Ціна:* {price}\n\n"
            f"⏳ Очікуйте розгляд адміністратором протягом 24 годин.\n"
            f"📬 Ви отримаете повідомлення про результат модерації."
        )
        
        bot.send_message(chat_id, confirmation_text, 
                        parse_mode='Markdown',
                        reply_markup=telebot.types.ReplyKeyboardRemove())

    except Exception as e:
        logger.error(f"Помилка відправки адміністратору: {e}")
        bot.send_message(chat_id, 
                        "❌ Виникла помилка при відправці. Спробуйте пізніше або зв'яжіться з підтримкою.",
                        reply_markup=telebot.types.ReplyKeyboardRemove())
    finally:
        if chat_id in user_data:
            del user_data[chat_id]

# --- Обробка callback запитів ---
@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    try:
        if call.data.startswith('admin_'):
            handle_admin_callbacks(call)
        else:
            handle_product_callbacks(call)
    except Exception as e:
        logger.error(f"Помилка обробки callback: {e}")
        bot.answer_callback_query(call.id, "❌ Виникла помилка. Спробуйте пізніше.")

# --- Обробка адмін колбеків ---
def handle_admin_callbacks(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "❌ Доступ заборонено.")
        return

    if call.data == "admin_stats":
        send_admin_statistics(call)
    elif call.data == "admin_users":
        send_users_list(call)
    elif call.data == "admin_blocked":
        send_blocked_users(call)

# --- Статистика для адміна ---
def send_admin_statistics(call):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    
    # Статистика товарів
    cursor.execute("SELECT status, COUNT(*) FROM products GROUP BY status")
    product_stats = dict(cursor.fetchall())
    
    # Статистика користувачів
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
    blocked_users_count = cursor.fetchone()[0]
    
    # Статистика за сьогодні
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) FROM products WHERE DATE(created_at) = ?", (today,))
    today_products = cursor.fetchone()[0]
    
    conn.close()
    
    stats_text = (
        f"📊 *Статистика бота*\n\n"
        f"👥 *Користувачі:*\n"
        f"• Всього: {total_users}\n"
        f"• Заблоковані: {blocked_users_count}\n\n"
        f"📦 *Товари:*\n"
        f"• На модерації: {product_stats.get('pending', 0)}\n"
        f"• Опубліковано: {product_stats.get('approved', 0)}\n"
        f"• Відхилено: {product_stats.get('rejected', 0)}\n"
        f"• Продано: {product_stats.get('sold', 0)}\n\n"
        f"📅 *Сьогодні додано:* {today_products}\n"
        f"📈 *Всього товарів:* {sum(product_stats.values())}"
    )
    
    bot.edit_message_text(stats_text, call.message.chat.id, call.message.message_id, 
                         parse_mode='Markdown')
    bot.answer_callback_query(call.id)

# --- Обробка товарних колбеків ---
def handle_product_callbacks(call):
    data_parts = call.data.split('_')
    action = data_parts[0]
    product_id = int(data_parts[1])

    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT seller_chat_id, product_name, price, description, photos, status, channel_message_id 
        FROM products WHERE id = ?
    """, (product_id,))
    product_info