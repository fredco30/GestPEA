"""
pea_project/celery.py
----------------------
Configuration Celery pour le projet PEA.

Démarrage en développement :
    # Terminal 1 — worker
    celery -A pea_project worker --loglevel=info

    # Terminal 2 — scheduler (beat)
    celery -A pea_project beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler

    # Terminal 3 — Django
    python manage.py runserver

En production (supervisor ou systemd) :
    voir section 17 déploiement VPS OVH dans claude_pea.md
"""

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pea_project.settings')

app = Celery('pea_project')

# Charger la config depuis Django settings (clés préfixées CELERY_)
app.config_from_object('django.conf:settings', namespace='CELERY')

# Autodiscovery des tâches dans tous les apps installées
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tâche de test — vérifier que Celery fonctionne : celery -A pea_project call app.celery.debug_task"""
    print(f'[Celery] Worker actif — Request: {self.request!r}')
