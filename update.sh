#!/bin/bash
# =============================================================================
# update.sh — Mise à jour du PEA Dashboard sur le VPS OVH
# =============================================================================
# Usage : bash update.sh
# À lancer depuis $APPDIR après avoir poussé du code sur GitHub.
# =============================================================================

APPDIR="/var/www/pea"
VENV="$APPDIR/venv"

set -e
cd $APPDIR

echo "[1/5] Pull du code..."
git pull

echo "[2/5] Mise à jour des dépendances Python..."
source $VENV/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

echo "[3/5] Migrations..."
python manage.py migrate --noinput

echo "[4/5] Collecte fichiers statiques..."
python manage.py collectstatic --noinput

echo "[5/5] Rebuild frontend React..."
cd $APPDIR/frontend
npm ci --silent
npm run build
cd $APPDIR

echo "Redémarrage des services..."
supervisorctl restart pea_gunicorn pea_celery_worker pea_celery_beat
nginx -t && systemctl reload nginx

echo "Mise à jour terminée ✓"
supervisorctl status


# =============================================================================
# COMMANDES DE MAINTENANCE UTILES
# =============================================================================
# (copier-coller selon le besoin)

: '
# --- Vérifier l état des services ---
supervisorctl status

# --- Voir les logs en temps réel ---
tail -f /var/www/pea/logs/pea.log
tail -f /var/www/pea/logs/celery_worker.log
tail -f /var/www/pea/logs/celery_beat.log
tail -f /var/www/pea/logs/gunicorn_error.log

# --- Relancer un service ---
supervisorctl restart pea_gunicorn
supervisorctl restart pea_celery_worker
supervisorctl restart pea_celery_beat

# --- Tester une tâche Celery manuellement ---
cd /var/www/pea && source venv/bin/activate
python manage.py shell -c "
from app.tasks import fetch_cours_eod_task
result = fetch_cours_eod_task.delay()
print(result.get(timeout=30))
"

# --- Importer l historique d un titre manuellement ---
cd /var/www/pea && source venv/bin/activate
python manage.py shell -c "
from app.tasks import import_historique_task
import_historique_task.delay('MC.PA')
"

# --- Vérifier le quota EODHD du jour ---
cd /var/www/pea && source venv/bin/activate
python manage.py shell -c "
from app.services.eodhd import EODHDClient
client = EODHDClient()
print(client.statut_quota())
"

# --- Tester l envoi Telegram ---
cd /var/www/pea && source venv/bin/activate
python manage.py shell -c "
from app.services.notifications import _envoyer_telegram_texte
_envoyer_telegram_texte('Test PEA Dashboard — Telegram OK ✓')
"

# --- Backup base de données ---
pg_dump -U pea_user pea_db > /var/backups/pea_db_$(date +%Y%m%d).sql

# --- Renouvellement SSL (automatique via cron, mais si besoin manuellement) ---
certbot renew --dry-run
'
