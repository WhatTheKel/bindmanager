try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    pass

from .celery import app as celery_app

__all__ = ('celery_app',)
