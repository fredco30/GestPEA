"""
pea_project/__init__.py
-----------------------
Charge l'app Celery au démarrage de Django.
Obligatoire pour que les tâches @shared_task fonctionnent.
"""

from .celery_app import app as celery_app

__all__ = ('celery_app',)
