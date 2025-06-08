from flask import Flask, request
import os
import logging

# Налаштування логування (для налагодження)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__) # ГЛОБАЛЬНЕ ВИЗНАЧЕННЯ

# ПРОСТО тестовий маршрут
@app.route('/')
def hello_world():
    logger.info("Received request on /")
    return 'Hello from Render! Bot is running.'

# Маршрут для вебхука Telegram (використовуватиметься Telegram)
@app.route(f'/{os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        # Тут в реальному боті був би bot.process_new_updates
        logger.info(f"Received webhook update: {json_string[:200]}...") # Логуємо частину оновлення
        return '!', 200
    else:
        return 'Hello World from webhook!', 200

if __name__ == '__main__':
    # Цей блок НЕ буде виконуватися Gunicorn на Render.
    # Gunicorn імпортує `app` безпосередньо.
    # Але для локального запуску це корисно.
    # При запуску на Render WEBHOOK_URL встановлюється з Environment Variables.
    # Ми не викликаємо app.run() тут, оскільки Gunicorn це зробить.
    logger.info("Running locally (if __name__ == '__main__': block)")
    # Для локального тестування можна розкоментувати app.run()
    # app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))