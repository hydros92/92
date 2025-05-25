import sqlite3
import telebot
import logging
import os
from datetime import datetime, timedelta
import re
import threading
import time

# --- 1. Ваш токен бота ---
# Рекомендується використовувати змінні середовища для токена
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') # Залиште пустим, якщо встановлено через Heroku config vars

# Якщо ви запускаєте локально і не використовуєте config vars, розкоментуйте наступний рядок
# TOKEN = '8039977178:AAGS-GbH-lhljGGG6OgJ2iMU_ncB-JzeOvU' # Замініть на ваш реальний токен

# --- 2. ID адміністратора ---
# Рекомендується використовувати змінні середовища
admin_chat_id_str = os.getenv('ADMIN_CHAT_ID') # Залиште пустим, якщо встановлено через Heroku config vars

# Якщо ви запускаєте локально і не використовуєте config vars, розкоментуйте наступний рядок
# admin_chat_id_str = '8184456641' # Замініть на ваш реальний ID адміністратора

ADMIN_CHAT_ID = int(admin_chat_id_str) if admin_chat_id_str else None
if ADMIN_CHAT_ID is None:
    logging.error("ADMIN_CHAT_ID не встановлено. Бот може працювати некоректно без ID адміністратора.")
    # Якщо ADMIN_CHAT_ID не встановлено, можна встановити значення за замовчуванням
    # або викликати виняток для зупинки бота
    # ADMIN_CHAT_ID = 8184456641 # Приклад ID за замовчуванням
    # raise ValueError("ADMIN_CHAT_ID не встановлено в змінних середовища.")


# --- 3. ID каналу для публікацій ---
# Рекомендується використовувати змінні середовища
channel_id_str = os.getenv('CHANNEL_ID') # Залиште пустим, якщо встановлено через Heroku config vars

# Якщо ви запускаєте локально і не використовуєте config vars, розкоментуйте наступний рядок
# channel_id_str = '-1002535586055' # Замініть на ID вашого каналу (з мінусом для приватних)

CHANNEL_ID = int(channel_id_str) if channel_id_str else None
if CHANNEL_ID is None:
    logging.error("CHANNEL_ID не встановлено. Бот може працювати некоректно без ID каналу.")
    # Якщо CHANNEL_ID не встановлено, можна встановити значення за замовчуванням
    # або викликати виняток для зупинки бота
    # CHANNEL_ID = -1002535586055 # Приклад ID за замовчуванням
    # raise ValueError("CHANNEL_ID не встановлено в змінних середовища.")


# --- 4. Номер картки Monobank для оплати комісії ---
# Рекомендується використовувати змінні середовища
MONOBANK_CARD_NUMBER = os.getenv('MONOBANK_CARD_NUMBER')

# Якщо ви запускаєте локально і не використовуєте config vars, розкоментуйте наступний рядок
# MONOBANK_CARD_NUMBER = '4441 1111 5302 1484' # Замініть на ваш номер картки


# --- 5. Налаштування логування ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("bot.log"),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN)

# Словник для зберігання стану користувачів (для багатоступеневих діалогів)
user_states = {} # {user_id: 'awaiting_title', 'awaiting_description', etc.}
user_data = {}   # {user_id: {title: '', description: '', price: '', photo: ''}}

# --- Функції бази даних ---
def init_db():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()

    # Створення таблиці products, якщо її не існує
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            description TEXT,
            price REAL,
            photo_file_id TEXT,
            status TEXT DEFAULT 'pending', -- pending, approved, rejected, sold
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            moderator_message_id INTEGER,
            channel_message_id INTEGER -- Новий стовпець для зберігання ID повідомлення в каналі
        )
    ''')

    # Додаємо стовпець channel_message_id, якщо його не існує (для існуючих баз даних)
    try:
        cursor.execute("SELECT channel_message_id FROM products LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE products ADD COLUMN channel_message_id INTEGER")
        logging.info("Додано стовпець 'channel_message_id' до таблиці 'products'.")

    # Створення таблиці users, якщо її не існує
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            blocked BOOLEAN DEFAULT 0
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("База даних ініціалізована або вже існує.")


def add_product(user_id, title, description, price, photo_file_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (user_id, title, description, price, photo_file_id) VALUES (?, ?, ?, ?, ?)",
                   (user_id, title, description, price, photo_file_id))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return product_id

def get_product(product_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()
    return product

def update_product_status(product_id, status, moderator_message_id=None, channel_message_id=None):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    if moderator_message_id and channel_message_id:
        cursor.execute("UPDATE products SET status = ?, moderator_message_id = ?, channel_message_id = ? WHERE id = ?",
                       (status, moderator_message_id, channel_message_id, product_id))
    elif moderator_message_id:
        cursor.execute("UPDATE products SET status = ?, moderator_message_id = ? WHERE id = ?",
                       (status, moderator_message_id, product_id))
    else:
        cursor.execute("UPDATE products SET status = ? WHERE id = ?",
                       (status, product_id))
    conn.commit()
    conn.close()

def get_all_products_by_status(status=None):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM products WHERE status = ?", (status,))
    else:
        cursor.execute("SELECT * FROM products")
    products = cursor.fetchall()
    conn.close()
    return products

def add_user(user_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def is_user_blocked(user_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

def block_user(user_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def unblock_user(user_id):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET blocked = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, blocked FROM users")
    users = cursor.fetchall()
    conn.close()
    return users

# --- Функції бота ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    add_user(message.chat.id)
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "Ви були заблоковані адміністратором і не можете користуватися ботом.")
        return

    logger.info(f"Користувач {message.chat.id} розпочав взаємодію з ботом.")
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    itembtn1 = telebot.types.KeyboardButton('➕ Додати товар')
    itembtn2 = telebot.types.KeyboardButton('📦 Мої товари')
    markup.add(itembtn1, itembtn2)
    bot.send_message(message.chat.id, "Привіт! Я допоможу тобі продати товар.\nОбери дію:", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == '➕ Додати товар')
def add_item_start(message):
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "Ви були заблоковані адміністратором і не можете користуватися ботом.")
        return

    user_states[message.chat.id] = 'awaiting_title'
    user_data[message.chat.id] = {}
    bot.send_message(message.chat.id, "Введіть назву товару:")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == 'awaiting_title')
def process_title(message):
    user_data[message.chat.id]['title'] = message.text
    user_states[message.chat.id] = 'awaiting_description'
    bot.send_message(message.chat.id, "Введіть опис товару:")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == 'awaiting_description')
def process_description(message):
    user_data[message.chat.id]['description'] = message.text
    user_states[message.chat.id] = 'awaiting_price'
    bot.send_message(message.chat.id, "Введіть ціну товару (тільки число):")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == 'awaiting_price')
def process_price(message):
    try:
        price = float(message.text.replace(',', '.'))
        if price <= 0:
            raise ValueError
        user_data[message.chat.id]['price'] = price
        user_states[message.chat.id] = 'awaiting_photo'
        bot.send_message(message.chat.id, "Надішліть фото товару:")
    except ValueError:
        bot.send_message(message.chat.id, "Будь ласка, введіть коректну ціну (наприклад, 100 або 100.50).")

@bot.message_handler(content_types=['photo'], func=lambda message: user_states.get(message.chat.id) == 'awaiting_photo')
def process_photo(message):
    photo_file_id = message.photo[-1].file_id
    user_data[message.chat.id]['photo'] = photo_file_id

    product_id = add_product(
        message.chat.id,
        user_data[message.chat.id]['title'],
        user_data[message.chat.id]['description'],
        user_data[message.chat.id]['price'],
        user_data[message.chat.id]['photo']
    )
    logger.info(f"Товар ID:{product_id} від користувача {message.chat.id} збережено в БД.")

    # Скидаємо стан і дані користувача
    del user_states[message.chat.id]
    del user_data[message.chat.id]

    bot.send_message(message.chat.id, "Ваш товар успішно додано! Він буде відправлений на модерацію.")

    # Відправляємо товар адміністратору для модерації
    send_product_to_admin_for_moderation(product_id, message.chat.id)
    logger.info(f"Товар ID:{product_id} відправлено адміністратору {ADMIN_CHAT_ID} для модерації.")

def send_product_to_admin_for_moderation(product_id, user_id):
    product = get_product(product_id)
    if product:
        title, description, price, photo_file_id = product[2], product[3], product[4], product[5]
        product_owner_link = f"tg://user?id={user_id}" # Створення посилання на користувача

        message_text = (
            f"❗️ *Новий товар на модерацію* ❗️\n\n"
            f"Назва: *{title}*\n"
            f"Опис: _{description}_\n"
            f"Ціна: *{price} грн*\n"
            f"Продавець: [Користувач](tg://user?id={user_id}) (ID: `{user_id}`)\n"
            f"ID товару: `{product_id}`"
        )

        markup = telebot.types.InlineKeyboardMarkup()
        approve_button = telebot.types.InlineKeyboardButton("✅ Опублікувати", callback_data=f"approve_{product_id}")
        reject_button = telebot.types.InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{product_id}")
        markup.add(approve_button, reject_button)

        sent_message = bot.send_photo(ADMIN_CHAT_ID, photo_file_id, caption=message_text,
                                    parse_mode='Markdown', reply_markup=markup)
        update_product_status(product_id, 'pending', moderator_message_id=sent_message.message_id)
    else:
        logger.error(f"Не вдалося знайти товар ID: {product_id} для модерації.")


@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_', 'reject_')))
def callback_inline(call):
    if call.from_user.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "У вас немає прав для цієї дії.")
        return

    action = call.data.split('_')[0]
    product_id = int(call.data.split('_')[1])
    product = get_product(product_id)

    if not product:
        bot.edit_message_text("Товар не знайдено або вже видалено.", call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)
        return

    # Перевіряємо, чи статус товару не змінився з моменту відправлення на модерацію
    current_status = product[6] # status is at index 6
    if current_status != 'pending':
        bot.answer_callback_query(call.id, f"Цей товар вже був {current_status}.", show_alert=True)
        return

    user_id = product[1] # user_id is at index 1
    title = product[2]
    description = product[3]
    price = product[4]
    photo_file_id = product[5]
    moderator_message_id = call.message.message_id

    try:
        if action == 'approve':
            # Публікуємо товар у каналі
            product_message_text = (
                f"✨ *НОВИЙ ТОВАР!* ✨\n\n"
                f"Назва: *{title}*\n"
                f"Опис: _{description}_\n"
                f"Ціна: *{price} грн*\n\n"
                f"Для покупки звертайтеся до продавця: [Написати продавцю](tg://user?id={user_id})\n"
                f"Або за ID: `{user_id}`\n\n"
                f"Оголошення #{product_id}" # Додаємо ID товару до повідомлення
            )
            sent_to_channel_message = bot.send_photo(CHANNEL_ID, photo_file_id, caption=product_message_text,
                                                     parse_mode='Markdown')
            channel_message_id = sent_to_channel_message.message_id # Зберігаємо ID повідомлення в каналі

            # Оновлюємо статус товару та зберігаємо ID повідомлення в каналі
            update_product_status(product_id, 'approved', moderator_message_id, channel_message_id)
            bot.edit_message_text(f"✅ Товар ID:{product_id} опубліковано.", call.message.chat.id, call.message.message_id)
            bot.send_message(user_id, f"🎉 Ваш товар '{title}' був успішно опублікований у каналі!")
            logger.info(f"Товар ID:{product_id} схвалено та опубліковано в каналі.")

        elif action == 'reject':
            update_product_status(product_id, 'rejected', moderator_message_id)
            bot.edit_message_text(f"❌ Товар ID:{product_id} відхилено.", call.message.chat.id, call.message.message_id)
            bot.send_message(user_id, f"😔 Ваш товар '{title}' був відхилений адміністратором.")
            logger.info(f"Товар ID:{product_id} відхилено.")

    except telebot.apihelper.ApiTelegramException as e:
        logger.error(f"Помилка Telegram API при публікації/відхиленні товару ID:{product_id}: {e}")
        bot.answer_callback_query(call.id, f"Помилка Telegram API: {e}", show_alert=True)
    except Exception as e:
        logger.error(f"Невідома помилка при публікації товару ID:{product_id}: {e}", exc_info=True)
        bot.answer_callback_query(call.id, f"Виникла невідома помилка: {e}", show_alert=True)
    finally:
        bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda message: message.text == '📦 Мої товари')
def my_items(message):
    if is_user_blocked(message.chat.id):
        bot.send_message(message.chat.id, "Ви були заблоковані адміністратором і не можете користуватися ботом.")
        return

    user_id = message.chat.id
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, status FROM products WHERE user_id = ?", (user_id,))
    user_products = cursor.fetchall()
    conn.close()

    if not user_products:
        bot.send_message(user_id, "У вас ще немає доданих товарів.")
        return

    response_text = "Ваші товари:\n\n"
    for product_id, title, status in user_products:
        response_text += f"ID: {product_id}\nНазва: {title}\nСтатус: {status}\n\n"

    bot.send_message(user_id, response_text)

# --- Адмін панель ---

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.send_message(message.chat.id, "У вас немає доступу до адмін панелі.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"))
    markup.add(telebot.types.InlineKeyboardButton("⚙️ Управління користувачами", callback_data="admin_users"))
    markup.add(telebot.types.InlineKeyboardButton("📦 Модерація товарів", callback_data="admin_moderation"))
    markup.add(telebot.types.InlineKeyboardButton("💰 Комісія Monobank", callback_data="admin_monobank"))
    markup.add(telebot.types.InlineKeyboardButton("🗑️ Очистити старі товари", callback_data="admin_clean_old_products"))

    bot.send_message(ADMIN_CHAT_ID, "Панель адміністратора:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callbacks(call):
    if call.from_user.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "У вас немає прав для цієї дії.")
        return

    if call.data == "admin_stats":
        send_stats_to_admin(call)
    elif call.data == "admin_users":
        send_user_management_panel(call.message.chat.id)
        bot.edit_message_text("Управління користувачами:", call.message.chat.id, call.message.message_id, reply_markup=None)
    elif call.data == "admin_moderation":
        send_moderation_queue(call)
    elif call.data == "admin_monobank":
        send_monobank_info(call)
    elif call.data == "admin_clean_old_products":
        confirm_clean_old_products(call)
    
    bot.answer_callback_query(call.id) # Закриття сповіщення про натискання кнопки


def send_stats_to_admin(call):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()

    # Загальна кількість користувачів
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    # Статистика по товарах за статусами
    cursor.execute("SELECT status, COUNT(*) FROM products GROUP BY status")
    product_stats_raw = cursor.fetchall()
    product_stats = {status: count for status, count in product_stats_raw}

    # Кількість заблокованих користувачів
    cursor.execute("SELECT COUNT(*) FROM users WHERE blocked = 1")
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


def send_user_management_panel(chat_id):
    users = get_all_users()
    if not users:
        bot.send_message(chat_id, "Користувачів не знайдено.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    for user_id, blocked_status in users:
        status_text = "🔒 Заблоковано" if blocked_status else "✅ Активний"
        button_text = f"ID: {user_id} - {status_text}"
        callback_data = f"toggle_block_{user_id}"
        markup.add(telebot.types.InlineKeyboardButton(button_text, callback_data=callback_data))

    bot.send_message(chat_id, "Оберіть користувача для зміни статусу блокування:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_block_'))
def toggle_block_user(call):
    if call.from_user.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "У вас немає прав для цієї дії.")
        return

    user_id_to_toggle = int(call.data.split('_')[2])

    if is_user_blocked(user_id_to_toggle):
        unblock_user(user_id_to_toggle)
        bot.send_message(user_id_to_toggle, "Адміністратор розблокував ваш акаунт. Тепер ви можете користуватися ботом.")
        logger.info(f"Користувач {user_id_to_toggle} розблокований.")
    else:
        block_user(user_id_to_toggle)
        bot.send_message(user_id_to_toggle, "Ваш акаунт був заблокований адміністратором.")
        logger.info(f"Користувач {user_id_to_toggle} заблокований.")

    # Оновлюємо панель управління користувачами
    bot.edit_message_text("Оновлення статусу користувача...", call.message.chat.id, call.message.message_id)
    send_user_management_panel(call.message.chat.id)
    bot.answer_callback_query(call.id)

def send_moderation_queue(call):
    pending_products = get_all_products_by_status('pending')
    if not pending_products:
        bot.edit_message_text("Немає товарів на модерації.", call.message.chat.id, call.message.message_id)
        return

    for product_info in pending_products:
        product_id = product_info[0]
        user_id = product_info[1]
        title = product_info[2]
        description = product_info[3]
        price = product_info[4]
        photo_file_id = product_info[5]

        message_text = (
            f"❗️ *Новий товар на модерацію* ❗️\n\n"
            f"Назва: *{title}*\n"
            f"Опис: _{description}_\n"
            f"Ціна: *{price} грн*\n"
            f"Продавець: [Користувач](tg://user?id={user_id}) (ID: `{user_id}`)\n"
            f"ID товару: `{product_id}`"
        )

        markup = telebot.types.InlineKeyboardMarkup()
        approve_button = telebot.types.InlineKeyboardButton("✅ Опублікувати", callback_data=f"approve_{product_id}")
        reject_button = telebot.types.InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{product_id}")
        markup.add(approve_button, reject_button)

        try:
            bot.send_photo(call.message.chat.id, photo_file_id, caption=message_text,
                           parse_mode='Markdown', reply_markup=markup)
            # Оновлюємо moderator_message_id, якщо воно не було встановлено раніше
            # або якщо адміністратор повторно переглядає чергу.
            # На цьому етапі, ми просто надсилаємо нове повідомлення.
            # Якщо потрібно оновити існуюче повідомлення, потрібно зберігати moderator_message_id
            # у базі даних і використовувати його тут.
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Помилка відправлення товару ID:{product_id} на модерацію адміну: {e}")
            bot.send_message(call.message.chat.id, f"Помилка відправлення товару ID:{product_id} на модерацію: {e}")

    bot.answer_callback_query(call.id, "Товари на модерації відправлено.")


def send_monobank_info(call):
    if MONOBANK_CARD_NUMBER:
        message_text = (
            f"💳 *Інформація про Monobank для комісії:*\n\n"
            f"Номер картки: `{MONOBANK_CARD_NUMBER}`\n\n"
            f"Цей номер буде надано користувачам для оплати комісії за публікацію, якщо ви впровадите таку функціональність."
        )
    else:
        message_text = "Номер картки Monobank не встановлено. Будь ласка, встановіть змінну середовища `MONOBANK_CARD_NUMBER`."

    bot.edit_message_text(message_text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

def confirm_clean_old_products(call):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("Так, очистити (старші 30 днів)", callback_data="confirm_clean_old_products_yes"))
    markup.add(telebot.types.InlineKeyboardButton("Ні, скасувати", callback_data="confirm_clean_old_products_no"))
    bot.edit_message_text("Ви впевнені, що хочете видалити всі товари, опубліковані більше 30 днів тому? Ця дія незворотна.",
                          call.message.chat.id, call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_clean_old_products_'))
def handle_clean_old_products_confirmation(call):
    if call.from_user.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "У вас немає прав для цієї дії.")
        return

    if call.data == "confirm_clean_old_products_yes":
        deleted_count = clean_old_products()
        bot.edit_message_text(f"🗑️ Видалено {deleted_count} старих товарів (старше 30 днів).",
                              call.message.chat.id, call.message.message_id)
        logger.info(f"Адміністратор видалив {deleted_count} старих товарів.")
    else: # confirm_clean_old_products_no
        bot.edit_message_text("Очищення старих товарів скасовано.", call.message.chat.id, call.message.message_id)
    
    bot.answer_callback_query(call.id)


def clean_old_products():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    # Видаляємо товари, які були опубліковані (approved) більше 30 днів тому
    # Або ж всі статуси? Залежить від вашої логіки.
    # Наприклад, можна видаляти всі товари старші 30 днів, які не 'sold'
    # Або тільки ті, які були 'approved' і 'created_at' більше 30 днів тому.
    # Давайте зробимо видалення всіх товарів, які НЕ 'pending' і НЕ 'sold' та старші 30 днів.
    # Якщо товар 'sold', можливо, його варто залишити для історії.
    # Якщо 'pending', то він ще на модерації.
    
    thirty_days_ago = datetime.now() - timedelta(days=30)
    cursor.execute("DELETE FROM products WHERE created_at < ? AND status NOT IN ('pending', 'sold')", (thirty_days_ago,))
    deleted_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_rows


# --- Запуск бота ---
if __name__ == '__main__':
    # Ініціалізація бази даних при запуску бота
    init_db()
    
    logger.info("Бот запущено. Початок polling...")
    # Додаємо обробник для невідомих команд або тексту, який не обробляється
    @bot.message_handler(func=lambda message: True, content_types=['text'])
    def echo_all(message):
        if message.chat.id == ADMIN_CHAT_ID:
            # Для адміністратора не відправляємо "Не розумію..."
            return
        if user_states.get(message.chat.id):
            # Якщо користувач у стані очікування введення, але не для цього типу контенту,
            # або якщо це просто невідомий текст у середині діалогу
            bot.send_message(message.chat.id, "Будь ласка, дотримуйтесь інструкцій. Я очікую на інший тип даних.")
        else:
            bot.send_message(message.chat.id, "Вибачте, я не розумію цієї команди або повідомлення. Скористайтесь кнопками або /start.")


    bot.infinity_polling()