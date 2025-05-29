from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, unique=True, nullable=False)
    username = Column(String)
    is_blocked = Column(Boolean, default=False)
