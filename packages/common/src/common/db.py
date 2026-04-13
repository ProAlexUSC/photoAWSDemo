import psycopg2
from common.config import get_database_url


def get_connection():
    """返回 psycopg2 连接。调用方负责关闭。"""
    return psycopg2.connect(get_database_url())
