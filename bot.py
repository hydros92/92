import os
import telebot
from telebot import types
import logging
import requests # Потрібно для safe_send_message обробки винятків
from dotenv import load_dotenv

load_dotenv()

# --- 1. Конфігурація Бота ---
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '0')) # Захист від None. Потрібен для fallback-повідомлень.

WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# --- 2. Конфігурація логування ---
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
logger.info(f"DIAGNOSTIC: ADMIN_CHAT_ID loaded: {ADMIN_CHAT_ID}")
# --- КІНЕЦЬ ДІАГНОСТИЧНИХ ЛОГІВ ---


# Базова перевірка наявності основних змінних
if not TOKEN:
    logger.critical("Помилка: TELEGRAM_BOT_TOKEN не встановлено у змінних оточення. Вихід.")
    exit(1)

bot = telebot.TeleBot(TOKEN)
# Імпортуємо Flask тут, щоб уникнути помилок, якщо він не потрібен для локального тестування без вебхука.
# Але для Render він завжди потрібен.
from flask import Flask, request
app = Flask(__name__)


# --- Нова функція для безпечної відправки повідомлень з детальною діагностикою ---
# Це критично важлива функція для логування помилок від Telegram API
def safe_send_message(chat_id, text, **kwargs):
    logger.info(f"DEBUG_SEND: Attempting to send message to {chat_id}. Text preview: '{text[:50]}'")
    try:
        response = bot.send_message(chat_id, text, **kwargs)
        logger.info(f"DEBUG_SEND: Message sent successfully to {chat_id}. Message ID: {response.message_id}")
        return response
    except telebot.apihelper.ApiTelegramException as e:
        logger.error(f"ERROR_TELEGRAM_API: Failed to send message to {chat_id}. Telegram API error: {e}", exc_info=True)
        # Спроба відправити резервне повідомлення про помилку користувачеві
        if chat_id != ADMIN_CHAT_ID: # Щоб уникнути циклу, якщо ADMIN_CHAT_ID неправильний
            try:
                bot.send_message(chat_id, "❌ Вибачте, сталася проблема при надсиланні відповіді. Telegram API повернув помилку. Будь ласка, спробуйте пізніше.")
            except Exception as e_inner:
                logger.error(f"ERROR_TELEGRAM_API: Could not send fallback message to {chat_id}: {e_inner}")
        return None
    except Exception as e:
        logger.critical(f"ERROR_GENERAL_SEND: Failed to send message to {chat_id}. General error: {e}", exc_info=True)
        if chat_id != ADMIN_CHAT_ID:
            try:
                bot.send_message(chat_id, "❌ Вибачте, сталася невідома помилка при надсиланні відповіді. Адміністратора повідомлено.")
            except Exception as e_inner:
                logger.error(f"ERROR_GENERAL_SEND: Could not send fallback message to {chat_id}: {e_inner}")
        return None

# --- Обробники команд (максимально спрощені) ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    logger.info(f"DEBUG_HANDLER: send_welcome handler CALLED for chat_id: {message.chat.id}, message_text: '{message.text}'")
    safe_send_message(message.chat.id, "Привіт! Я твій тестовий бот! Версія для діагностики.")
    logger.info(f"DEBUG_HANDLER: send_welcome handler FINISHED for chat_id: {message.chat.id}")

@bot.message_handler(commands=['test'])
def send_test_message(message):
    logger.info(f"DEBUG_HANDLER: send_test_message handler CALLED for chat_id: {message.chat.id}, message_text: '{message.text}'")
    safe_send_message(message.chat.id, "Це тестове повідомлення. Бот працює! 🎉")
    logger.info(f"DEBUG_HANDLER: send_test_message handler FINISHED for chat_id: {message.chat.id}")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'location'])
def handle_all_messages(message):
    logger.info(f"DEBUG_HANDLER: handle_all_messages handler CALLED for chat_id: {message.chat.id}, type: {message.content_type}, text: '{message.text[:50] if message.text else 'N/A'}'")
    
    response_text = "Я отримав ваше повідомлення не команду. Дякую!"
    if message.content_type == 'photo':
        response_text = "Я отримав ваше фото. Поки що не знаю, що з ним робити."
    elif message.content_type == 'location':
        response_text = f"Я отримав вашу геолокацію: {message.location.latitude}, {message.location.longitude}."
        
    safe_send_message(message.chat.id, response_text)
    logger.info(f"DEBUG_HANDLER: handle_all_messages handler FINISHED for chat_id: {message.chat.id}")


# --- Запуск бота та налаштування вебхука для Render ---

# Прибираємо ініціалізацію БД, оскільки вона не потрібна для цього діагностичного коду.
# Якщо ви видалили попередній сервіс, то таблиць немає.
# logger.info("Запуск ініціалізації БД...")
# init_db() 

logger.info(f"DEBUG: Number of message handlers registered: {len(bot.message_handlers)}")
logger.info(f"DEBUG: Number of callback query handlers registered: {len(bot.callback_query_handlers)}")


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

if __name__ == '__main__':
    logger.info("Запуск Flask-додатка локально...")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook_receiver():
    """Обробляє вхідні оновлення від Telegram."""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        logger.info(f"DEBUG_WEBHOOK_RECEIVER: Raw incoming update JSON: {json_string[:200]}...")
        
        try:
            update = telebot.types.Update.de_json(json_string)
            
            if update.message:
                logger.info(f"DEBUG_WEBHOOK_RECEIVER: Received message update from {update.message.chat.id}, text: '{update.message.text[:50] if update.message.text else 'N/A'}'")
            elif update.callback_query:
                logger.info(f"DEBUG_WEBHOOK_RECEIVER: Received callback query update from {update.callback_query.message.chat.id}, data: '{update.callback_query.data}'")
            else:
                logger.info(f"DEBUG_WEBHOOK_RECEIVER: Received unknown update type: {update}")

            logger.info("DEBUG_WEBHOOK_RECEIVER: Attempting to process update with bot.process_new_updates...")
            bot.process_new_updates([update])
            logger.info("DEBUG_WEBHOOK_RECEIVER: bot.process_new_updates finished.")
            return '!', 200
        except Exception as e:
            logger.critical(f"FATAL_WEBHOOK_ERROR: during webhook processing or pyTelegramBotAPI dispatch: {e}", exc_info=True)
            return 'Error processing update', 500
    else:
        logger.warning("Received non-JSON request on webhook path. Ignoring.")
        return 'Hello from bot webhook!', 200

