"""
pea_project/settings.py
------------------------
Configuration Django pour le projet PEA.
Les valeurs sensibles (clés API) sont lues depuis les variables d'environnement.

En développement : copier .env.example → .env et renseigner les clés.
En production    : définir les variables dans l'environnement du VPS OVH.

Installation des dépendances :
    pip install django djangorestframework celery redis anthropic requests
                pandas pandas-ta python-dateutil django-environ psycopg2-binary
"""

import os
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# CHEMINS
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    EODHD_QUOTA_JOUR=(int, 20),
)
environ.Env.read_env(BASE_DIR / '.env')

# ---------------------------------------------------------------------------
# SÉCURITÉ
# ---------------------------------------------------------------------------

SECRET_KEY = env('SECRET_KEY')
DEBUG      = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

# ---------------------------------------------------------------------------
# APPLICATIONS
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Tiers
    'rest_framework',
    'corsheaders',
    # Projet
    'app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'pea_project.urls'

# ---------------------------------------------------------------------------
# BASE DE DONNÉES
# ---------------------------------------------------------------------------

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME':     env('DB_NAME',     default='pea_db'),
        'USER':     env('DB_USER',     default='pea_user'),
        'PASSWORD': env('DB_PASSWORD', default=''),
        'HOST':     env('DB_HOST',     default='localhost'),
        'PORT':     env('DB_PORT',     default='5432'),
        'OPTIONS': {
            'connect_timeout': 10,
        },
    }
}

# ---------------------------------------------------------------------------
# CELERY — tâches asynchrones et scheduler
# ---------------------------------------------------------------------------

CELERY_BROKER_URL        = env('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND    = env('REDIS_URL', default='redis://localhost:6379/0')
CELERY_TIMEZONE          = 'Europe/Paris'
CELERY_TASK_SERIALIZER   = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT    = ['json']

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {

    # --- Cours EOD : chaque soir lun-ven à 18h30 (après clôture Euronext 17h35) ---
    'fetch-cours-eod': {
        'task':     'app.tasks.fetch_cours_eod_task',
        'schedule': crontab(hour=18, minute=30, day_of_week='1-5'),
    },

    # --- Fondamentaux lot A : lundi + mercredi à 19h00 ---
    'fetch-fondamentaux-lot-a': {
        'task':     'app.tasks.fetch_fondamentaux_lot_task',
        'args':     ['A'],
        'schedule': crontab(hour=19, minute=0, day_of_week='1,3'),
    },

    # --- Fondamentaux lot B : mardi + jeudi à 19h00 ---
    'fetch-fondamentaux-lot-b': {
        'task':     'app.tasks.fetch_fondamentaux_lot_task',
        'args':     ['B'],
        'schedule': crontab(hour=19, minute=0, day_of_week='2,4'),
    },

    # --- News mutualisée : chaque soir lun-ven à 20h00 ---
    'fetch-news': {
        'task':     'app.tasks.fetch_news_task',
        'schedule': crontab(hour=20, minute=0, day_of_week='1-5'),
    },

    # --- Calcul indicateurs techniques : chaque soir à 21h00 ---
    # (après fetch_cours_eod — chaîne automatique : indicateurs → signaux → confluence → LLM)
    'run-indicateurs': {
        'task':     'app.tasks.run_indicateurs_task',
        'schedule': crontab(hour=21, minute=0, day_of_week='1-5'),
    },

    # --- Screener PEA éligibilité : 1er vendredi du mois à 8h00 ---
    'update-eligibles-pea': {
        'task':     'app.tasks.update_eligibles_pea_task',
        'schedule': crontab(hour=8, minute=0, day_of_week='5', day_of_month='1-7'),
    },

    # --- Digest hebdomadaire : vendredi soir à 19h00 ---
    'digest-hebdomadaire': {
        'task':     'app.tasks.digest_hebdomadaire_task',
        'schedule': crontab(hour=19, minute=0, day_of_week='5'),
    },
}

# ---------------------------------------------------------------------------
# APIS EXTERNES
# ---------------------------------------------------------------------------

# EODHD — cours, fondamentaux, screener, news (tier gratuit : 20 req/jour)
EODHD_API_KEY    = env('EODHD_API_KEY', default='')
EODHD_QUOTA_JOUR = env('EODHD_QUOTA_JOUR', default=20)

# Anthropic Claude — scoring sentiment + rédaction alertes
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY', default='')

# NewsAPI — presse française (tier gratuit : 100 req/jour)
NEWSAPI_KEY = env('NEWSAPI_KEY', default='')

# FMP — fondamentaux complémentaires (tier gratuit : 250 req/jour)
FMP_API_KEY = env('FMP_API_KEY', default='')

# ---------------------------------------------------------------------------
# DJANGO REST FRAMEWORK
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

# ---------------------------------------------------------------------------
# CORS (pour le frontend React en développement)
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[
    'http://localhost:3000',
    'http://127.0.0.1:3000',
])

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} — {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class':     'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'fichier': {
            'class':     'logging.handlers.RotatingFileHandler',
            'filename':  BASE_DIR / 'logs' / 'pea.log',
            'maxBytes':  5 * 1024 * 1024,  # 5 MB
            'backupCount': 3,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'app': {
            'handlers':  ['console', 'fichier'],
            'level':     'INFO',
            'propagate': False,
        },
        'celery': {
            'handlers':  ['console', 'fichier'],
            'level':     'INFO',
            'propagate': False,
        },
    },
}

# ---------------------------------------------------------------------------
# DIVERS
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE     = 'Europe/Paris'
USE_I18N      = True
USE_TZ        = True

STATIC_URL   = '/static/'
STATIC_ROOT  = BASE_DIR / 'staticfiles'
MEDIA_URL    = '/media/'
MEDIA_ROOT   = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
