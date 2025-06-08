import os
from flask import Flask, request
import logging

# Налаштування логування (для налагодження на Render)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# !!! ЦЕЙ РЯДОК МАЄ БУТИ НА ГЛОБАЛЬНОМУ РІВНІ !!!
app = Flask(__name__)

# Мінімум, щоб Render міг запустити веб-сервіс
@app.route('/')
def home():
    logger.info("Received request on / (home page)")
    return "Hello from Render! The basic Flask app is running."

# Маршрут для вебхука Telegram
@app.route(f'/{os.getenv("TELEGRAM_BOT_TOKEN", "your_token_placeholder")}', methods=['POST'])
def webhook_receiver():
    logger.info("Received webhook request.")
    # Тут зазвичай був би bot.process_new_updates([telebot.types.Update.de_json(request.get_data().decode('utf-8'))])
    return "OK", 200 # Повертаємо 200 OK для Telegram

# Це запускається тільки при локальному запуску файлу напряму (python bot.py)
# Gunicorn не використовує цей блок.
if __name__ == '__main__':
    logger.info("Running Flask app locally...")
    # При локальному запуску для відладки
    # import telebot
    # BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'your_token_placeholder')
    # if BOT_TOKEN == 'your_token_placeholder':
    #     logger.warning("TELEGRAM_BOT_TOKEN not set, webhook route will use placeholder.")
    # bot = telebot.TeleBot(BOT_TOKEN)
    #
    # # Запускаємо Flask-додаток локально
    # app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

    # Для Render цей блок не виконується.
    # Виходимо, щоб не конфліктувати з Gunicorn.
    pass