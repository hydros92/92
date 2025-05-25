import sqlite3
import telebot
import logging
import os
from datetime import datetime, timedelta
import re # Для обробки тексту та хештегів

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
            product_name TEXT NOT NULL,
            price TEXT NOT NULL,
            description TEXT NOT NULL,
            photos TEXT, -- Зберігатимемо список photo_file_id через кому
            status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected', 'sold'
            admin_message_id INTEGER, -- ID повідомлення адміністратору для подальшої зміни
            channel_message_id INTEGER -- ID повідомлення в каналі після публікації
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("База даних ініціалізована або вже існує.")

# --- Функція для генерації хештегів ---
def generate_hashtags(description, num_hashtags=5):
    # Прибираємо пунктуацію та розбиваємо на слова
    words = re.findall(r'\b\w+\b', description.lower())
    
    # Фільтруємо короткі та поширені слова, щоб отримати більш значущі
    stopwords = set(['я', 'ми', 'ти', 'ви', 'він', 'вона', 'воно', 'вони', 'це', 'що', 'як', 'де', 'коли', 'а', 'і', 'та', 'або', 'чи', 'для', 'з', 'на', 'у', 'в', 'до', 'від', 'по', 'за', 'при', 'про', 'між', 'під', 'над', 'без', 'через', 'для'])
    filtered_words = [word for word in words if len(word) > 2 and word not in stopwords]
    
    # Вибираємо унікальні слова
    unique_words = list(set(filtered_words))
    
    # Беремо перші `num_hashtags` унікальних слів або менше, якщо їх недостатньо
    hashtags = ['#' + word for word in unique_words[:num_hashtags]]
    
    return " ".join(hashtags) if hashtags else ""

# --- Обробник команди /start ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    logger.info(f"Користувач {message.chat.id} розпочав взаємодію з ботом.")
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    item_add = telebot.types.KeyboardButton("Додати товар")
    item_my = telebot.types.KeyboardButton("Мої товари")
    item_help = telebot.types.KeyboardButton("Допомога")
    item_channel = telebot.types.KeyboardButton("Наш канал")
    markup.add(item_add, item_my, item_help, item_channel)
    bot.send_message(message.chat.id, "Привіт! Я бот для розміщення оголошень. Оберіть дію:", reply_markup=markup)

# --- Обробник текстових повідомлень (для кнопок) ---
@bot.message_handler(content_types=['text'])
def handle_text(message):
    chat_id = message.chat.id
    if message.text == "Додати товар":
        start_add_product(message)
    elif message.text == "Мої товари":
        send_my_products(message)
    elif message.text == "Допомога":
        bot.send_message(chat_id, "Я бот для розміщення оголошень. Ви можете додати свій товар, а адміністратор його перевірить і опублікує. Якщо у вас виникли питання, зверніться до адміністратора.")
    elif message.text == "Наш канал":
        send_channel_link(message)
    elif chat_id in user_data:
        process_product_input(message)
    else:
        bot.send_message(chat_id, "Будь ласка, скористайтеся кнопками або командою /start.")

# --- Функція для обробки кнопки "Мої товари" ---
def send_my_products(message):
    chat_id = message.chat.id
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT product_name, status FROM products WHERE seller_chat_id = ?", (chat_id,))
    user_products = cursor.fetchall()
    conn.close()

    if user_products:
        response = "Ваші товари:\n"
        for name, status in user_products:
            status_ukr = {
                'pending': 'на розгляді',
                'approved': 'опубліковано',
                'rejected': 'відхилено',
                'sold': 'продано'
            }.get(status, status)
            response += f"▫️ *{name}* (Статус: {status_ukr})\n"
        bot.send_message(chat_id, response, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "Ви ще не додавали жодних товарів.")

# --- Функція для обробки кнопки "Наш канал" ---
def send_channel_link(message):
    chat_id = message.chat.id
    if CHANNEL_ID == 0:
        bot.send_message(chat_id, "На жаль, посилання на канал не налаштовано адміністратором.")
        logger.warning(f"Користувач {chat_id} спробував отримати посилання на канал, але CHANNEL_ID не налаштований.")
        return

    channel_link = ""
    try:
        if str(CHANNEL_ID).startswith('-100'):
            # Для приватного каналу завжди генеруємо нове посилання
            invite_link = bot.create_chat_invite_link(CHANNEL_ID, member_limit=1).invite_link
            channel_link = invite_link
            logger.info(f"Згенеровано нове посилання на запрошення для приватного каналу: {invite_link}")
        else:
            # Для публічного каналу можна використовувати username або пряме посилання за ID, якщо відомо
            # Тут ми припускаємо, що для публічного каналу CHANNEL_ID це його username без @
            channel_link = f"https://t.me/{CHANNEL_ID}"
            logger.info(f"Використано пряме посилання на публічний канал: {channel_link}")
    except telebot.apihelper.ApiTelegramException as e:
        logger.warning(f"Бот не може згенерувати посилання на запрошення для каналу {CHANNEL_ID} (можливо, не має прав або канал публічний і використовується інший спосіб): {e}")
        # Спробуємо альтернативні статичні посилання, якщо генерація не вдалася
        if str(CHANNEL_ID).startswith('-100'):
            channel_link = f"https://t.me/c/{str(CHANNEL_ID)[4:]}"
        else:
            channel_link = f"https://t.me/{CHANNEL_ID}" # Спробуємо просто посилання на username
    except Exception as e:
        logger.error(f"Невідома помилка при генерації посилання на запрошення: {e}", exc_info=True)
        channel_link = "https://t.me/your_channel_link_here_manually" # Заглушка, якщо щось пішло не так

    if not channel_link or "your_channel_link_here_manually" in channel_link:
        bot.send_message(chat_id, "На жаль, посилання на канал не вдалося сформувати автоматично. Будь ласка, зверніться до адміністратора.")
        logger.warning(f"Не вдалося сформувати посилання на канал {CHANNEL_ID}.")
        return

    invite_text = (
        f"Запрошуємо вас приєднатися до нашого каналу, щоб не пропустити нові оголошення!\n\n"
        f"👉 [Приєднатися до каналу]({channel_link})"
    )
    
    bot.send_message(chat_id, invite_text, parse_mode='Markdown', disable_web_page_preview=True)
    logger.info(f"Користувач {chat_id} запросив посилання на канал.")


# --- Початок процесу додавання товару ---
def start_add_product(message):
    chat_id = message.chat.id
    user_data[chat_id] = {'step': 'waiting_name', 'photos': []}
    bot.send_message(chat_id, "Введіть назву вашого товару:")

# --- Обробка введених даних про товар ---
@bot.message_handler(content_types=['text']) # Цей обробник потрібен для текстових відповідей
def process_product_input(message):
    chat_id = message.chat.id
    
    if chat_id not in user_data:
        # Це текстове повідомлення, яке не є частиною процесу додавання товару
        # Обробляється загальним handle_text, який вже є.
        # Цей обробник спрацює лише, якщо chat_id є в user_data.
        return 

    current_step = user_data[chat_id]['step']

    if current_step == 'waiting_name':
        user_data[chat_id]['name'] = message.text
        user_data[chat_id]['step'] = 'waiting_price'
        bot.send_message(chat_id, "Тепер введіть ціну товару (наприклад, '100 грн' або 'Договірна'):")
    elif current_step == 'waiting_price':
        user_data[chat_id]['price'] = message.text
        user_data[chat_id]['step'] = 'waiting_photos'
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(telebot.types.KeyboardButton("Пропустити"))
        bot.send_message(chat_id, "Надішліть фотографії товару (до 10 фото, кожне окремим повідомленням). Коли закінчите, натисніть 'Пропустити'.", reply_markup=markup)
    elif current_step == 'waiting_description': # Це новий крок після фото
        user_data[chat_id]['description'] = message.text
        # Генерація хештегів на основі опису
        hashtags = generate_hashtags(message.text)
        user_data[chat_id]['hashtags'] = hashtags
        send_for_moderation(chat_id)
    elif current_step == 'waiting_photos':
        if message.text == "Пропустити":
            user_data[chat_id]['step'] = 'waiting_description' # Переходимо до опису
            bot.send_message(chat_id, "Добре, тепер, будь ласка, надайте короткий опис товару (до 500 символів):")
        else: # Якщо користувач надіслав текст замість фото або "Пропустити"
            bot.send_message(chat_id, "Будь ласка, надішліть фотографії або натисніть 'Пропустити'.")
    else:
        bot.send_message(chat_id, "Невідома команда. Будь ласка, скористайтеся кнопками або командою /start.")

# --- Обробка фотографій ---
@bot.message_handler(content_types=['photo'])
def handle_photos(message):
    chat_id = message.chat.id
    if chat_id in user_data and user_data[chat_id]['step'] == 'waiting_photos':
        photo_file_id = message.photo[-1].file_id # Зберігаємо file_id найбільшої версії фото
        if len(user_data[chat_id]['photos']) < 10: # Обмеження до 10 фото
            user_data[chat_id]['photos'].append(photo_file_id)
            bot.send_message(chat_id, f"Фото {len(user_data[chat_id]['photos'])} додано. Надішліть ще або натисніть 'Пропустити'.")
        else:
            bot.send_message(chat_id, "Ви досягли максимальної кількості фото (10). Будь ласка, натисніть 'Пропустити'.")
    elif chat_id in user_data and user_data[chat_id]['step'] != 'waiting_photos':
        bot.send_message(chat_id, "Будь ласка, дотримуйтесь послідовності. Зараз не час для завантаження фото.")
    else:
        bot.send_message(chat_id, "Для початку додавання товару натисніть 'Додати товар' або скористайтеся командою /start.")


# --- Відправка товару на модерацію адміністратору ---
def send_for_moderation(chat_id):
    data = user_data[chat_id]
    name = data['name']
    price = data['price']
    description = data['description']
    photos = data['photos']
    seller_chat_id = chat_id
    hashtags = data.get('hashtags', '')

    # Зберігаємо товар в базу даних зі статусом 'pending'
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (seller_chat_id, product_name, price, description, photos, status) VALUES (?, ?, ?, ?, ?, ?)",
                   (seller_chat_id, name, price, description, ",".join(photos), 'pending'))
    product_id = cursor.lastrowid # Отримуємо ID щойно доданого товару
    conn.commit()
    conn.close()
    logger.info(f"Товар ID:{product_id} від користувача {seller_chat_id} збережено в БД.")

    # Формуємо повідомлення для адміністратора
    admin_message_text = (
        f"📩 *Новий товар на модерацію!* (ID: {product_id})\n\n"
        f"📦 *Назва:* {name}\n"
        f"💰 *Ціна:* {price}\n"
        f"📝 *Опис:* {description}\n"
        f"🏷️ *Хештеги:* {hashtags}\n\n"
        f"👤 *Продавець:* [Користувач {seller_chat_id}](tg://user?id={seller_chat_id})\n"
        f"📸 *Фото:* {'Є' if photos else 'Немає'}\n\n"
        f"Оберіть дію:"
    )

    markup_admin = telebot.types.InlineKeyboardMarkup()
    markup_admin.add(
        telebot.types.InlineKeyboardButton("✅ Опублікувати", callback_data=f"approve_{product_id}"),
        telebot.types.InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{product_id}")
    )

    try:
        # Надсилаємо фото окремо або як альбом, якщо їх більше одного
        if photos:
            media = []
            for photo_id in photos:
                media.append(telebot.types.InputMediaPhoto(photo_id))
            
            if media:
                # Перше фото з підписом, інші без
                first_photo = media[0]
                first_photo.caption = admin_message_text
                first_photo.parse_mode = 'Markdown'
                
                admin_msg = bot.send_photo(ADMIN_CHAT_ID, first_photo.media, caption=first_photo.caption, parse_mode='Markdown', reply_markup=markup_admin)
                
                if len(media) > 1:
                    remaining_media = media[1:]
                    bot.send_media_group(ADMIN_CHAT_ID, remaining_media)
            else: # Це fallback, якщо photos був не порожній, але media порожній (теоретично не має статися)
                admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_message_text, parse_mode='Markdown', reply_markup=markup_admin, disable_web_page_preview=True)
        else: # Якщо фото немає
            admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_message_text, parse_mode='Markdown', reply_markup=markup_admin, disable_web_page_preview=True)
            
        # Оновлюємо admin_message_id у базі даних
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET admin_message_id = ? WHERE id = ?", (admin_msg.message_id, product_id))
        conn.commit()
        conn.close()

        bot.send_message(chat_id, "Ваш товар відправлено на модерацію. Адміністратор розгляне його найближчим часом.", 
                         reply_markup=telebot.types.ReplyKeyboardRemove()) # Прибираємо кнопку "Пропустити"
        logger.info(f"Товар ID:{product_id} відправлено адміністратору {ADMIN_CHAT_ID} для модерації.")

    except Exception as e:
        logger.error(f"Не вдалося відправити сповіщення адміністратору {ADMIN_CHAT_ID} про товар ID:{product_id}: {e}", exc_info=True)
        bot.send_message(chat_id, "Виникла помилка при відправці адміністратору. Будь ласка, спробуйте пізніше або зв'яжіться з підтримкою.",
                         reply_markup=telebot.types.ReplyKeyboardRemove())
    finally:
        if chat_id in user_data:
            del user_data[chat_id]

# --- Обробка натискань на кнопки InlineKeyboardMarkup для адміністратора ---
@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    if call.message:
        data_parts = call.data.split('_')
        action = data_parts[0]
        product_id = int(data_parts[1])

        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute("SELECT seller_chat_id, product_name, price, description, photos, status FROM products WHERE id = ?", (product_id,))
        product_info = cursor.fetchone()

        if not product_info:
            bot.answer_callback_query(call.id, "Товар не знайдено.")
            conn.close()
            return

        seller_chat_id, name, price, description, photos_str, current_status = product_info
        photos = photos_str.split(',') if photos_str else []

        if action == "approve" and current_status == 'pending':
            # Опублікувати товар в канал
            try:
                # Формуємо текст для публікації в каналі
                hashtags = generate_hashtags(description) # Знову генеруємо хештеги для каналу
                channel_text = (
                    f"✨ *Нове оголошення!* ✨\n\n"
                    f"📦 *Назва:* {name}\n"
                    f"💰 *Ціна:* {price}\n"
                    f"📝 *Опис:* {description}\n"
                    f"🏷️ {hashtags}\n\n"
                    f"🔗 *Зв'язок з продавцем:* [Написати продавцю](tg://user?id={seller_chat_id})"
                )

                channel_msg = None
                if photos:
                    media = []
                    for photo_id in photos:
                        media.append(telebot.types.InputMediaPhoto(photo_id))
                    
                    if media:
                        first_photo = media[0]
                        first_photo.caption = channel_text
                        first_photo.parse_mode = 'Markdown'
                        
                        channel_msg = bot.send_photo(CHANNEL_ID, first_photo.media, caption=first_photo.caption, parse_mode='Markdown')
                        
                        if len(media) > 1:
                            remaining_media = media[1:]
                            bot.send_media_group(CHANNEL_ID, remaining_media)
                    else:
                        channel_msg = bot.send_message(CHANNEL_ID, channel_text, parse_mode='Markdown', disable_web_page_preview=True)
                else: # Якщо фото немає
                    channel_msg = bot.send_message(CHANNEL_ID, channel_text, parse_mode='Markdown', disable_web_page_preview=True)
                    
                if channel_msg:
                    channel_message_id = channel_msg.message_id
                    cursor.execute("UPDATE products SET status = 'approved', channel_message_id = ? WHERE id = ?", (channel_message_id, product_id))
                    conn.commit()
                    
                    bot.answer_callback_query(call.id, "Товар опубліковано!")
                    bot.edit_message_text(chat_id=call.message.chat.id,
                                            message_id=call.message.message_id,
                                            text=f"✅ Товар ID:{product_id} опубліковано в канал.\n\n" + call.message.text.split('\n\n')[1], # Зберігаємо частину оригінального тексту
                                            parse_mode='Markdown',
                                            reply_markup=telebot.types.InlineKeyboardMarkup().add(
                                                telebot.types.InlineKeyboardButton("✅ Опубліковано", callback_data=f"approved_{product_id}"),
                                                telebot.types.InlineKeyboardButton("💰 Продано", callback_data=f"sold_{product_id}") # Додана кнопка "Продано"
                                            ))
                    bot.send_message(seller_chat_id, f"🎉 Ваш товар '{name}' був опублікований в каналі!")
                    logger.info(f"Товар ID:{product_id} опубліковано в канал {CHANNEL_ID}.")
                else:
                    raise Exception("Не вдалося отримати ID повідомлення каналу.")

            except telebot.apihelper.ApiTelegramException as e:
                bot.answer_callback_query(call.id, f"Помилка публікації: {e}. Перевірте права бота в каналі.")
                logger.error(f"Помилка публікації товару ID:{product_id} в канал: {e}", exc_info=True)
            except Exception as e:
                bot.answer_callback_query(call.id, f"Невідома помилка при публікації: {e}")
                logger.error(f"Невідома помилка при публікації товару ID:{product_id}: {e}", exc_info=True)

        elif action == "reject" and current_status == 'pending':
            cursor.execute("UPDATE products SET status = 'rejected' WHERE id = ?", (product_id,))
            conn.commit()
            bot.answer_callback_query(call.id, "Товар відхилено.")
            bot.edit_message_text(chat_id=call.message.chat.id,
                                    message_id=call.message.message_id,
                                    text=f"❌ Товар ID:{product_id} відхилено.\n\n" + call.message.text.split('\n\n')[1],
                                    parse_mode='Markdown',
                                    reply_markup=None) # Прибираємо кнопки після відхилення
            bot.send_message(seller_chat_id, f"😔 На жаль, ваш товар '{name}' був відхилений адміністратором. Зв'яжіться з адміністратором для деталей.")
            logger.info(f"Товар ID:{product_id} відхилено адміністратором.")

        elif action == "sold" and current_status == 'approved':
            # Оновлюємо статус товару на 'sold'
            cursor.execute("UPDATE products SET status = 'sold' WHERE id = ?", (product_id,))
            conn.commit()

            # Обчислюємо комісію (припускаємо, що ціна у форматі "100 грн")
            commission_amount = 0
            try:
                # Вилучаємо число з ціни, ігноруючи текст
                price_value_match = re.search(r'(\d+\.?\d*)', price)
                if price_value_match:
                    item_price = float(price_value_match.group(1))
                    commission_amount = item_price * 0.10 # 10% комісія
                    commission_message = f"Сума комісії: *{commission_amount:.2f} грн*."
                else:
                    commission_message = "Не вдалося розрахувати комісію (ціна не містить числа)."
            except ValueError:
                commission_message = "Не вдалося розрахувати комісію (ціна не є числом)."

            bot.answer_callback_query(call.id, "Статус товару оновлено на 'Продано'.")
            bot.edit_message_text(chat_id=call.message.chat.id,
                                    message_id=call.message.message_id,
                                    text=f"✅ Товар ID:{product_id} продано.\n\n" + call.message.text.split('\n\n')[1],
                                    parse_mode='Markdown',
                                    reply_markup=None) # Прибираємо кнопки після продажу
            
            # Повідомлення продавцю про продаж та комісію
            bot.send_message(seller_chat_id, 
                             f"🎉 Ваш товар '{name}' було продано! {commission_message}\n"
                             f"Будь ласка, сплатіть комісію на картку Monobank: `{MONOBANK_CARD_NUMBER}`\n"
                             f"Дякуємо за співпрацю!", parse_mode='Markdown')
            logger.info(f"Товар ID:{product_id} продано. Продавцю {seller_chat_id} надіслано повідомлення про комісію.")

            # Оновлення повідомлення в каналі, якщо воно було опубліковано
            if CHANNEL_ID and product_info[7]: # product_info[7] - channel_message_id
                try:
                    bot.edit_message_text(chat_id=CHANNEL_ID,
                                          message_id=product_info[7],
                                          text=f"❌ *Оголошення неактивне* ❌\n\n{call.message.text}", # Можна змінити на більш підходящий текст
                                          parse_mode='Markdown',
                                          reply_markup=None) # Видалити кнопки, якщо є
                except Exception as e:
                    logger.error(f"Не вдалося оновити повідомлення в каналі для товару ID:{product_id}: {e}", exc_info=True)


        else:
            bot.answer_callback_query(call.id, "Цей товар вже було оброблено або дія недійсна.")
            logger.info(f"Спроба повторної дії над товаром ID:{product_id} (поточний статус: {current_status}, дія: {action}).")

        conn.close()

# --- Запуск бота ---
if __name__ == '__main__':
    init_db() # Ініціалізуємо базу даних при запуску
    logger.info("Бот запущено. Початок polling...")
    try:
        bot.polling(none_stop=True, interval=0)
    except Exception as e:
        logger.error(f"Помилка запуску бота: {e}", exc_info=True)