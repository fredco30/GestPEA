#!/bin/bash
# =============================================================================
# deploy.sh — Déploiement PEA Dashboard sur VPS OVH (Ubuntu 24)
# =============================================================================
# Usage : bash deploy.sh
# À exécuter depuis le répertoire racine du projet sur le VPS.
#
# Prérequis VPS OVH :
#   - Ubuntu 24 LTS
#   - DNS : pea.tondomaine.fr → IP du VPS (configurer sur Hostinger/OVH)
#   - Port 22 (SSH), 80 (HTTP), 443 (HTTPS) ouverts
#   - Accès SSH root ou sudo
# =============================================================================

set -e  # Arrêter si une commande échoue

DOMAINE="pea.tondomaine.fr"      # ← Remplacer par ton domaine
APPDIR="/var/www/pea"
VENV="$APPDIR/venv"
USER="www-data"

echo "=============================="
echo "  Déploiement PEA Dashboard"
echo "=============================="

# -----------------------------------------------------------------------
# 1. Dépendances système
# -----------------------------------------------------------------------
echo "[1/9] Installation des dépendances système..."

apt-get update -qq
apt-get install -y -qq \
    python3.12 python3.12-venv python3-pip \
    postgresql postgresql-contrib \
    nginx \
    redis-server \
    supervisor \
    certbot python3-certbot-nginx \
    git curl

# -----------------------------------------------------------------------
# 2. PostgreSQL — création DB et utilisateur
# -----------------------------------------------------------------------
echo "[2/9] Configuration PostgreSQL..."

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='pea_db'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE pea_db;"

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='pea_user'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER pea_user WITH PASSWORD 'CHANGER_MOT_DE_PASSE';"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE pea_db TO pea_user;"
sudo -u postgres psql -c "ALTER DATABASE pea_db OWNER TO pea_user;"

# -----------------------------------------------------------------------
# 3. Répertoire de l'app
# -----------------------------------------------------------------------
echo "[3/9] Préparation du répertoire..."

mkdir -p $APPDIR/logs
mkdir -p $APPDIR/staticfiles
mkdir -p $APPDIR/media

# Cloner ou mettre à jour le dépôt
if [ -d "$APPDIR/.git" ]; then
    cd $APPDIR && git pull
else
    git clone https://github.com/fredco30/pea-dashboard.git $APPDIR
    cd $APPDIR
fi

# -----------------------------------------------------------------------
# 4. Environnement virtuel Python
# -----------------------------------------------------------------------
echo "[4/9] Environnement Python..."

python3.12 -m venv $VENV
source $VENV/bin/activate

pip install --upgrade pip -q
pip install -q \
    django \
    djangorestframework \
    django-cors-headers \
    django-environ \
    celery \
    redis \
    mistralai \
    requests \
    pandas \
    pandas-ta \
    python-dateutil \
    psycopg2-binary \
    gunicorn \
    whitenoise

# -----------------------------------------------------------------------
# 5. Configuration Django
# -----------------------------------------------------------------------
echo "[5/9] Configuration Django..."

# Vérifier que .env existe
if [ ! -f "$APPDIR/.env" ]; then
    echo "⚠️  ATTENTION : $APPDIR/.env manquant !"
    echo "   Créer le fichier .env avant de continuer."
    echo "   Voir env_example.txt pour le template."
    exit 1
fi

# Mettre DEBUG=False en production dans .env
sed -i 's/^DEBUG=True/DEBUG=False/' $APPDIR/.env

# Migrations et collecte des fichiers statiques
cd $APPDIR
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# -----------------------------------------------------------------------
# 6. Gunicorn — serveur WSGI Django
# -----------------------------------------------------------------------
echo "[6/9] Configuration Gunicorn..."

cat > /etc/supervisor/conf.d/pea_gunicorn.conf << EOF
[program:pea_gunicorn]
command=$VENV/bin/gunicorn pea_project.wsgi:application
    --bind 127.0.0.1:8000
    --workers 2
    --timeout 60
    --access-logfile $APPDIR/logs/gunicorn_access.log
    --error-logfile $APPDIR/logs/gunicorn_error.log
directory=$APPDIR
user=$USER
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
environment=DJANGO_SETTINGS_MODULE="pea_project.settings"
EOF

# -----------------------------------------------------------------------
# 7. Celery worker + beat
# -----------------------------------------------------------------------
echo "[7/9] Configuration Celery..."

cat > /etc/supervisor/conf.d/pea_celery_worker.conf << EOF
[program:pea_celery_worker]
command=$VENV/bin/celery -A pea_project worker --loglevel=info --concurrency=2
directory=$APPDIR
user=$USER
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile=$APPDIR/logs/celery_worker.log
stderr_logfile=$APPDIR/logs/celery_worker_err.log
environment=DJANGO_SETTINGS_MODULE="pea_project.settings"
EOF

cat > /etc/supervisor/conf.d/pea_celery_beat.conf << EOF
[program:pea_celery_beat]
command=$VENV/bin/celery -A pea_project beat --loglevel=info
    --scheduler django_celery_beat.schedulers:DatabaseScheduler
directory=$APPDIR
user=$USER
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile=$APPDIR/logs/celery_beat.log
stderr_logfile=$APPDIR/logs/celery_beat_err.log
environment=DJANGO_SETTINGS_MODULE="pea_project.settings"
EOF

# -----------------------------------------------------------------------
# 8. Nginx — reverse proxy + frontend React
# -----------------------------------------------------------------------
echo "[8/9] Configuration Nginx..."

cat > /etc/nginx/sites-available/pea << EOF
server {
    listen 80;
    server_name $DOMAINE;

    # Frontend React (build statique)
    root $APPDIR/frontend/build;
    index index.html;

    # Fichiers statiques Django
    location /static/ {
        alias $APPDIR/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Fichiers media (documents uploadés)
    location /media/ {
        alias $APPDIR/media/;
        expires 7d;
        add_header Cache-Control "public";
    }

    # API Django → Gunicorn
    location /api/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60;
    }

    # Admin Django
    location /admin/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
    }

    # React Router — toutes les routes inconnues → index.html
    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

ln -sf /etc/nginx/sites-available/pea /etc/nginx/sites-enabled/pea
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# -----------------------------------------------------------------------
# 9. SSL Let's Encrypt
# -----------------------------------------------------------------------
echo "[9/9] Certificat SSL Let's Encrypt..."

certbot --nginx \
    --non-interactive \
    --agree-tos \
    --email "fred@tondomaine.fr" \
    --domains "$DOMAINE" \
    --redirect

# -----------------------------------------------------------------------
# Démarrage de tous les services
# -----------------------------------------------------------------------
echo ""
echo "Démarrage des services..."

systemctl enable redis-server && systemctl start redis-server
supervisorctl reread
supervisorctl update
supervisorctl start pea_gunicorn pea_celery_worker pea_celery_beat

echo ""
echo "=============================="
echo "  Déploiement terminé ✓"
echo "=============================="
echo ""
echo "  Dashboard : https://$DOMAINE"
echo "  Admin     : https://$DOMAINE/admin"
echo ""
echo "  Vérifier les services :"
echo "    supervisorctl status"
echo "    tail -f $APPDIR/logs/pea.log"
echo ""
echo "  Tester Celery :"
echo "    cd $APPDIR && source venv/bin/activate"
echo "    celery -A pea_project call app.celery_app.debug_task"
