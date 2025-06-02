import os
from sqlalchemy import create_engine, text, inspect
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

# Перевірка та додавання колонки user_session_data, якщо її немає
inspector = inspect(engine)
if 'users' in inspector.get_table_names():
    existing_columns = [col['name'] for col in inspector.get_columns('users')]
    if 'user_session_data' not in existing_columns:
        print("ℹ️ Колонка 'user_session_data' не знайдена, додаю...")
        with engine.connect() as connection:
            connection.execute(text('ALTER TABLE users ADD COLUMN user_session_data TEXT DEFAULT \'{}\''))
            connection.commit()
        print("✅ Колонка 'user_session_data' додана до таблиці 'users'.")
    else:
        print("✅ Колонка 'user_session_data' вже існує.")
else:
    print("ℹ️ Таблиця 'users' не знайдена. Вона буде створена при першому запуску.")


print("✅ Таблиці успішно створено або оновлено")

