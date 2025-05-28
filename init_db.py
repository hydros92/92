import os
from sqlalchemy import create_engine
from models import Base  # заміни на свій модуль з ORM-моделями

from dotenv import load_dotenv

# Завантажуємо змінні середовища з файлу .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL не встановлено у змінних середовища")

engine = create_engine(DATABASE_URL)

# Створюємо таблиці у базі даних
Base.metadata.create_all(engine)

print("✅ Таблиці успішно створено")
