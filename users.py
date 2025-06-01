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
