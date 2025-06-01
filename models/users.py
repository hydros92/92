from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# Базовий клас для декларативних моделей SQLAlchemy
Base = declarative_base()

class User(Base):
    __tablename__ = 'users' # Назва таблиці в базі даних

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    joined_at = Column(DateTime, default=datetime.now)
    last_activity = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_blocked = Column(Boolean, default=False)
    blocked_by = Column(Integer) # ID адміністратора, який заблокував
    blocked_at = Column(DateTime)
    user_status = Column(String, default='idle') # Додана колонка для статусу користувача

    def __repr__(self):
        return f"<User(id={self.id}, chat_id={self.chat_id}, username='{self.username}')>"

# Ви можете додати інші моделі тут, наприклад Product, Conversation, FAQ
# class Product(Base):
#     __tablename__ = 'products'
#     id = Column(Integer, primary_key=True)
#     seller_chat_id = Column(Integer, nullable=False, index=True)
#     product_name = Column(String, nullable=False)
#     price = Column(String) # Може бути "Договірна"
#     description = Column(String)
#     photos = Column(String) # Зберігаємо як JSON-рядок з file_id фото
#     geolocation = Column(String) # Зберігаємо як JSON-рядок {latitude, longitude}
#     status = Column(String, default='pending') # pending, approved, rejected, sold, expired
#     created_at = Column(DateTime, default=datetime.now)
#     moderated_at = Column(DateTime)
#     moderator_id = Column(Integer)
#     channel_message_id = Column(Integer) # ID повідомлення в каналі, якщо опубліковано
#     seller_username = Column(String) # Для зручності

# class Conversation(Base):
#     __tablename__ = 'conversations'
#     id = Column(Integer, primary_key=True)
#     user_chat_id = Column(Integer, nullable=False, index=True)
#     sender_type = Column(String, nullable=False) # 'user' or 'ai'
#     content = Column(String, nullable=False)
#     timestamp = Column(DateTime, default=datetime.now)
#     product_id = Column(Integer, index=True) # Якщо розмова стосується конкретного товару

# class FAQ(Base):
#     __tablename__ = 'faq'
#     id = Column(Integer, primary_key=True)
#     question = Column(String, nullable=False, unique=True)
#     answer = Column(String, nullable=False)
#     created_at = Column(DateTime, default=datetime.now)
#     updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
