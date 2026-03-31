#!/usr/bin/env python3
"""
pea_deploy.py — Déploiement intelligent PEA Dashboard sur VPS OVH
==================================================================
Détecte automatiquement les ports et services déjà en place
(geoclic.fr, Nginx, PostgreSQL, Redis…) avant toute installation.

Usage :
    python3 pea_deploy.py --scan          # Scan seul, sans installer
    python3 pea_deploy.py --install       # Scan + installation complète
    python3 pea_deploy.py --check         # Vérifier l'état après install

Prérequis :
    Python 3.8+ sur le VPS (déjà présent sur Ubuntu)
    Accès sudo ou root
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# CONFIGURATION PAR DÉFAUT (modifiable avant lancement)
# ---------------------------------------------------------------------------

CONFIG = {
    # Domaine PEA
    'domaine':           'pea.tondomaine.fr',       # ← À CHANGER
    'email_certbot':     'fred@tondomaine.fr',       # ← À CHANGER

    # Répertoires
    'appdir':            '/var/www/pea',
    'logdir':            '/var/www/pea/logs',

    # Ports souhaités par défaut (seront ajustés si occupés)
    'port_gunicorn':     8001,    # Django/Gunicorn — 8000 souvent pris par geoclic
    'port_redis_pea':    6380,    # Redis dédié PEA — 6379 souvent pris
    'port_pg':           5432,    # PostgreSQL — partagé si déjà présent

    # DB
    'db_name':           'pea_db',
    'db_user':           'pea_user',

    # Geoclic (existant à protéger)
    'geoclic_appdir':    '/var/www/geoclic_final',
    'geoclic_domaine':   'geoclic.fr',
}

# Plages de ports à scanner pour trouver des ports libres
PORT_RANGE_GUNICORN = range(8001, 8020)
PORT_RANGE_REDIS    = range(6380, 6395)

# ---------------------------------------------------------------------------
# DATACLASSES
# ---------------------------------------------------------------------------

@dataclass
class ServiceExistant:
    nom:     str
    pid:     Optional[int]
    port:    Optional[int]
    user:    str = ''
    cmd:     str = ''
    conflit: bool = False

@dataclass
class RapportScan:
    ports_occupes:      dict = field(default_factory=dict)   # {port: service_name}
    services_actifs:    list = field(default_factory=list)   # [ServiceExistant]
    nginx_ok:           bool = False
    nginx_sites:        list = field(default_factory=list)
    postgres_ok:        bool = False
    postgres_dbs:       list = field(default_factory=list)
    redis_ports:        list = field(default_factory=list)
    geoclic_detecte:    bool = False
    port_gunicorn_libre: int = 8001
    port_redis_libre:    int = 6380
    conflits:           list = field(default_factory=list)
    recommandations:    list = field(default_factory=list)


# ---------------------------------------------------------------------------
# COULEURS TERMINAL
# ---------------------------------------------------------------------------

class C:
    VERT   = '\033[92m'
    ROUGE  = '\033[91m'
    JAUNE  = '\033[93m'
    BLEU   = '\033[94m'
    GRIS   = '\033[90m'
    BOLD   = '\033[1m'
    RESET  = '\033[0m'

def ok(msg):    print(f"  {C.VERT}✓{C.RESET} {msg}")
def err(msg):   print(f"  {C.ROUGE}✗{C.RESET} {msg}")
def warn(msg):  print(f"  {C.JAUNE}⚠{C.RESET} {msg}")
def info(msg):  print(f"  {C.BLEU}→{C.RESET} {msg}")
def titre(msg): print(f"\n{C.BOLD}{msg}{C.RESET}")
def sep():      print(f"  {C.GRIS}{'─' * 56}{C.RESET}")


# ---------------------------------------------------------------------------
# UTILITAIRES SYSTÈME
# ---------------------------------------------------------------------------

def run(cmd: str, capture=True, check=False) -> tuple[int, str, str]:
    """Exécute une commande shell. Retourne (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, shell=True, capture_output=capture,
        text=True, check=False
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def run_sudo(cmd: str) -> tuple[int, str, str]:
    if os.geteuid() != 0:
        cmd = f"sudo {cmd}"
    return run(cmd)


def port_libre(port: int) -> bool:
    """Vérifie si un port TCP est libre sur localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) != 0


def premier_port_libre(plage) -> int:
    """Retourne le premier port libre dans une plage."""
    for p in plage:
        if port_libre(p):
            return p
    raise RuntimeError(f"Aucun port libre dans la plage {list(plage)[:3]}…")


def service_actif(nom: str) -> bool:
    code, _, _ = run(f"systemctl is-active {nom} 2>/dev/null")
    return code == 0


def cmd_existe(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ---------------------------------------------------------------------------
# SCAN PRINCIPAL
# ---------------------------------------------------------------------------

def scanner_serveur() -> RapportScan:
    """
    Scan complet du serveur avant installation.
    Détecte tous les ports occupés, services actifs et configurations existantes.
    """
    rapport = RapportScan()
    titre("SCAN DU SERVEUR EN COURS…")
    sep()

    # --- 1. Ports TCP occupés ---
    titre("1. Ports TCP en écoute")
    code, out, _ = run("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null")
    ports_detectes = {}

    for ligne in out.splitlines():
        # Extraire port et process depuis ss/netstat
        m_port = re.search(r':(\d+)\s', ligne)
        m_proc = re.search(r'\"([^\"]+)\"', ligne) or re.search(r'users:\(\("?([^",\)]+)', ligne)
        if m_port:
            port = int(m_port.group(1))
            proc = m_proc.group(1) if m_proc else 'inconnu'
            ports_detectes[port] = proc

    rapport.ports_occupes = ports_detectes

    # Ports critiques à afficher
    ports_surveilles = [80, 443, 3000, 5432, 6379, 6380, 8000, 8001, 8002, 8080]
    for p in sorted(set(ports_surveilles) | set(ports_detectes.keys())):
        if p in ports_detectes:
            warn(f"Port {p:5} — OCCUPÉ par '{ports_detectes[p]}'")
        elif p in ports_surveilles:
            ok(f"Port {p:5} — libre")

    # --- 2. Nginx ---
    titre("2. Nginx")
    rapport.nginx_ok = cmd_existe('nginx') and service_actif('nginx')

    if rapport.nginx_ok:
        ok("Nginx actif")
        # Lister les sites configurés
        _, sites_enabled, _ = run("ls /etc/nginx/sites-enabled/ 2>/dev/null")
        rapport.nginx_sites = [s for s in sites_enabled.splitlines() if s]
        for s in rapport.nginx_sites:
            info(f"Site actif : {s}")
            if 'geoclic' in s.lower():
                rapport.geoclic_detecte = True
                ok(f"  → geoclic.fr détecté et préservé")
    else:
        warn("Nginx non installé ou inactif")

    # --- 3. PostgreSQL ---
    titre("3. PostgreSQL")
    rapport.postgres_ok = cmd_existe('psql') and service_actif('postgresql')

    if rapport.postgres_ok:
        ok("PostgreSQL actif")
        code, dbs, _ = run("sudo -u postgres psql -tc '\\l' 2>/dev/null | grep '|' | awk '{print $1}'")
        rapport.postgres_dbs = [d.strip() for d in dbs.splitlines() if d.strip() and d.strip() != '|']
        for db in rapport.postgres_dbs:
            info(f"Base existante : {db}")
            if db == CONFIG['db_name']:
                warn(f"  → {CONFIG['db_name']} existe déjà — sera réutilisée")
    else:
        warn("PostgreSQL non installé — sera installé")

    # --- 4. Redis ---
    titre("4. Redis")
    for port in [6379, 6380, 6381]:
        if not port_libre(port):
            rapport.redis_ports.append(port)
            ok(f"Redis détecté sur port {port}")

    if not rapport.redis_ports:
        warn("Aucune instance Redis active — sera installée")

    # --- 5. Geoclic ---
    titre("5. Geoclic (à préserver)")
    geoclic_path = Path(CONFIG['geoclic_appdir'])
    if geoclic_path.exists():
        rapport.geoclic_detecte = True
        ok(f"Répertoire geoclic trouvé : {geoclic_path}")

        # Détecter le port Gunicorn geoclic
        code, out, _ = run("supervisorctl status 2>/dev/null | grep -i geoclic")
        if out:
            m = re.search(r'--bind\s+\S+:(\d+)', out)
            if m:
                port_geo = int(m.group(1))
                warn(f"Gunicorn geoclic sur port {port_geo} — sera évité")
    else:
        info("Répertoire geoclic non trouvé à l'emplacement par défaut")
        # Chercher ailleurs
        code, found, _ = run("find /var/www -name 'manage.py' -maxdepth 3 2>/dev/null")
        if found:
            for f in found.splitlines():
                info(f"  Application Django trouvée : {Path(f).parent}")

    # --- 6. Supervisor ---
    titre("6. Supervisor")
    code, out, _ = run("supervisorctl status 2>/dev/null")
    if code == 0 and out:
        for ligne in out.splitlines():
            parts = ligne.split()
            if parts:
                info(f"Process supervisor : {ligne.strip()}")

    # --- 7. Calcul des ports libres pour PEA ---
    titre("7. Attribution des ports PEA")

    try:
        rapport.port_gunicorn_libre = premier_port_libre(PORT_RANGE_GUNICORN)
        ok(f"Port Gunicorn PEA   : {rapport.port_gunicorn_libre}")
    except RuntimeError as e:
        err(str(e))
        rapport.conflits.append("Aucun port disponible pour Gunicorn PEA")

    try:
        # Si Redis tourne déjà sur 6379, on peut le réutiliser avec une DB différente
        if 6379 in rapport.redis_ports:
            rapport.port_redis_libre = 6379
            ok(f"Redis existant réutilisé : port 6379 (DB 1 pour PEA)")
            rapport.recommandations.append("Redis partagé : utiliser DB 0 pour geoclic, DB 1 pour PEA")
        else:
            rapport.port_redis_libre = premier_port_libre(PORT_RANGE_REDIS)
            ok(f"Port Redis PEA      : {rapport.port_redis_libre}")
    except RuntimeError as e:
        err(str(e))
        rapport.conflits.append("Aucun port disponible pour Redis PEA")

    # --- 8. Synthèse des conflits ---
    titre("8. Synthèse")
    if rapport.conflits:
        for c in rapport.conflits:
            err(f"CONFLIT : {c}")
    else:
        ok("Aucun conflit détecté — installation possible")

    if rapport.recommandations:
        for r in rapport.recommandations:
            warn(f"Recommandation : {r}")

    return rapport


# ---------------------------------------------------------------------------
# GÉNÉRATION DE LA CONFIG ADAPTÉE
# ---------------------------------------------------------------------------

def generer_config(rapport: RapportScan) -> dict:
    """
    Génère la configuration finale en tenant compte du scan.
    Retourne un dict avec tous les paramètres ajustés.
    """
    redis_url = (
        f"redis://127.0.0.1:6379/1"   # DB 1 si Redis partagé avec geoclic
        if rapport.port_redis_libre == 6379
        else f"redis://127.0.0.1:{rapport.port_redis_libre}/0"
    )

    return {
        **CONFIG,
        'port_gunicorn':  rapport.port_gunicorn_libre,
        'port_redis':     rapport.port_redis_libre,
        'redis_url':      redis_url,
        'redis_partage':  rapport.port_redis_libre == 6379,
        'postgres_existe': rapport.postgres_ok,
        'nginx_existe':    rapport.nginx_ok,
    }


# ---------------------------------------------------------------------------
# INSTALLATION
# ---------------------------------------------------------------------------

def installer(rapport: RapportScan):
    """Lance l'installation complète en tenant compte du scan."""

    if rapport.conflits:
        err("Des conflits ont été détectés. Résoudre avant d'installer.")
        sys.exit(1)

    cfg = generer_config(rapport)

    titre("INSTALLATION PEA DASHBOARD")
    sep()
    info(f"Domaine       : {cfg['domaine']}")
    info(f"Répertoire    : {cfg['appdir']}")
    info(f"Port Gunicorn : {cfg['port_gunicorn']}")
    info(f"Redis URL     : {cfg['redis_url']}")
    info(f"PostgreSQL    : {'partagé' if cfg['postgres_existe'] else 'nouvelle installation'}")
    info(f"Nginx         : {'partagé' if cfg['nginx_existe'] else 'nouvelle installation'}")
    print()

    reponse = input(f"  {C.JAUNE}Confirmer l'installation ? (oui/non) : {C.RESET}")
    if reponse.strip().lower() not in ('oui', 'o', 'yes', 'y'):
        print("  Installation annulée.")
        sys.exit(0)

    # Étapes dans l'ordre
    _installer_paquets(cfg)
    _configurer_postgres(cfg)
    _configurer_redis(cfg)
    _creer_env(cfg)
    _configurer_supervisor(cfg)
    _configurer_nginx(cfg)
    _configurer_ssl(cfg)
    _demarrer_services(cfg)
    _afficher_resume(cfg)


def _installer_paquets(cfg):
    titre("→ Installation des paquets système")

    paquets = ['python3.12', 'python3.12-venv', 'python3-pip', 'supervisor', 'certbot', 'python3-certbot-nginx', 'git']

    if not cfg['postgres_existe']:
        paquets += ['postgresql', 'postgresql-contrib']
    if not cfg['nginx_existe']:
        paquets += ['nginx']
    if not cfg['redis_partage'] and not cmd_existe('redis-server'):
        paquets += ['redis-server']

    code, _, err_out = run_sudo(f"apt-get install -y -qq {' '.join(paquets)}")
    if code == 0:
        ok(f"{len(paquets)} paquets installés/vérifiés")
    else:
        err(f"Erreur apt-get : {err_out}")
        sys.exit(1)


def _configurer_postgres(cfg):
    titre("→ Configuration PostgreSQL")

    if not cfg['postgres_existe']:
        run_sudo("systemctl start postgresql")
        run_sudo("systemctl enable postgresql")
        ok("PostgreSQL démarré")

    # Créer user et DB si besoin
    code, out, _ = run(f"sudo -u postgres psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='{cfg['db_user']}'\"")
    if '1' not in out:
        mdp = _generer_mdp()
        run(f"sudo -u postgres psql -c \"CREATE USER {cfg['db_user']} WITH PASSWORD '{mdp}';\"")
        # Sauvegarder le mot de passe pour l'afficher à la fin
        cfg['db_password_genere'] = mdp
        ok(f"Utilisateur {cfg['db_user']} créé")
    else:
        warn(f"Utilisateur {cfg['db_user']} existe déjà")
        cfg['db_password_genere'] = 'VOIR_FICHIER_.ENV_EXISTANT'

    code, out, _ = run(f"sudo -u postgres psql -tc \"SELECT 1 FROM pg_database WHERE datname='{cfg['db_name']}'\"")
    if '1' not in out:
        run(f"sudo -u postgres psql -c \"CREATE DATABASE {cfg['db_name']} OWNER {cfg['db_user']};\"")
        run(f"sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE {cfg['db_name']} TO {cfg['db_user']};\"")
        ok(f"Base {cfg['db_name']} créée")
    else:
        warn(f"Base {cfg['db_name']} existe déjà — réutilisée")


def _configurer_redis(cfg):
    titre("→ Configuration Redis")

    if cfg['redis_partage']:
        ok("Redis partagé avec geoclic — DB 1 réservée pour PEA")
        return

    # Nouvelle instance Redis sur port dédié
    run_sudo("systemctl start redis-server")
    run_sudo("systemctl enable redis-server")

    if cfg['port_redis'] != 6379:
        # Créer une config Redis sur le port alternatif
        redis_conf = f"""
port {cfg['port_redis']}
daemonize yes
logfile /var/log/redis/redis-pea.log
dir /var/lib/redis
"""
        Path('/etc/redis/redis-pea.conf').write_text(redis_conf)
        run_sudo(f"redis-server /etc/redis/redis-pea.conf")
        ok(f"Redis PEA démarré sur port {cfg['port_redis']}")
    else:
        ok("Redis démarré sur port 6379")


def _creer_env(cfg):
    titre("→ Création du fichier .env")

    appdir = Path(cfg['appdir'])
    env_path = appdir / '.env'

    if env_path.exists():
        warn(f".env existe déjà — sauvegarde en .env.backup")
        env_path.rename(appdir / '.env.backup')

    secret_key = _generer_secret_key()
    mdp = cfg.get('db_password_genere', 'CHANGER_MOT_DE_PASSE')

    env_content = f"""# PEA Dashboard — Configuration générée automatiquement
# Généré le : {__import__('datetime').date.today()}
# Port Gunicorn : {cfg['port_gunicorn']}

SECRET_KEY={secret_key}
DEBUG=False
ALLOWED_HOSTS={cfg['domaine']},localhost,127.0.0.1

# PostgreSQL
DB_NAME={cfg['db_name']}
DB_USER={cfg['db_user']}
DB_PASSWORD={mdp}
DB_HOST=localhost
DB_PORT=5432

# Redis (DB 1 si partagé avec geoclic)
REDIS_URL={cfg['redis_url']}

# EODHD — https://eodhd.com/register
EODHD_API_KEY=RENSEIGNER_ICI
EODHD_QUOTA_JOUR=20

# Anthropic — https://console.anthropic.com
ANTHROPIC_API_KEY=RENSEIGNER_ICI

# NewsAPI — https://newsapi.org
NEWSAPI_KEY=RENSEIGNER_ICI

# FMP — https://financialmodelingprep.com
FMP_API_KEY=RENSEIGNER_ICI

# Email
EMAIL_DESTINATAIRE=RENSEIGNER_ICI
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=RENSEIGNER_ICI
EMAIL_HOST_PASSWORD=RENSEIGNER_ICI
EMAIL_USE_TLS=True

# Telegram
TELEGRAM_BOT_TOKEN=RENSEIGNER_ICI
TELEGRAM_CHAT_ID=RENSEIGNER_ICI

# Dashboard
DASHBOARD_URL=https://{cfg['domaine']}
CORS_ALLOWED_ORIGINS=https://{cfg['domaine']}
"""

    env_path.write_text(env_content)
    os.chmod(env_path, 0o600)  # Lisible uniquement par root/owner
    ok(f".env créé dans {env_path}")
    warn("⚠  Renseigner les clés API dans .env avant de lancer migrate")


def _configurer_supervisor(cfg):
    titre("→ Configuration Supervisor (Gunicorn + Celery)")

    appdir  = cfg['appdir']
    venv    = f"{appdir}/venv"
    logdir  = cfg['logdir']
    port    = cfg['port_gunicorn']

    # Gunicorn
    gunicorn_conf = f"""[program:pea_gunicorn]
command={venv}/bin/gunicorn pea_project.wsgi:application --bind 127.0.0.1:{port} --workers 2 --timeout 60 --access-logfile {logdir}/gunicorn_access.log --error-logfile {logdir}/gunicorn_error.log
directory={appdir}
user=www-data
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
environment=DJANGO_SETTINGS_MODULE="pea_project.settings"
"""

    # Celery worker
    worker_conf = f"""[program:pea_celery_worker]
command={venv}/bin/celery -A pea_project worker --loglevel=info --concurrency=2
directory={appdir}
user=www-data
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile={logdir}/celery_worker.log
stderr_logfile={logdir}/celery_worker_err.log
environment=DJANGO_SETTINGS_MODULE="pea_project.settings"
"""

    # Celery beat
    beat_conf = f"""[program:pea_celery_beat]
command={venv}/bin/celery -A pea_project beat --loglevel=info
directory={appdir}
user=www-data
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile={logdir}/celery_beat.log
stderr_logfile={logdir}/celery_beat_err.log
environment=DJANGO_SETTINGS_MODULE="pea_project.settings"
"""

    for nom, contenu in [
        ('pea_gunicorn',      gunicorn_conf),
        ('pea_celery_worker', worker_conf),
        ('pea_celery_beat',   beat_conf),
    ]:
        path = Path(f"/etc/supervisor/conf.d/{nom}.conf")
        path.write_text(contenu)
        ok(f"Supervisor config : {path.name}")

    run_sudo("supervisorctl reread")
    run_sudo("supervisorctl update")


def _configurer_nginx(cfg):
    titre("→ Configuration Nginx (virtual host PEA)")

    appdir  = cfg['appdir']
    domaine = cfg['domaine']
    port    = cfg['port_gunicorn']

    nginx_conf = f"""# PEA Dashboard — {domaine}
# Généré automatiquement par pea_deploy.py
# Ne pas modifier manuellement — relancer pea_deploy.py --install

server {{
    listen 80;
    server_name {domaine};

    # Frontend React
    root {appdir}/frontend/build;
    index index.html;

    # Fichiers statiques Django
    location /static/ {{
        alias {appdir}/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }}

    # API Django → Gunicorn (port {port})
    location /api/ {{
        proxy_pass         http://127.0.0.1:{port};
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60;
    }}

    # Admin Django
    location /admin/ {{
        proxy_pass         http://127.0.0.1:{port};
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
    }}

    # React Router SPA
    location / {{
        try_files $uri $uri/ /index.html;
    }}
}}
"""

    site_path = Path(f"/etc/nginx/sites-available/pea")
    site_path.write_text(nginx_conf)

    link_path = Path(f"/etc/nginx/sites-enabled/pea")
    if not link_path.exists():
        link_path.symlink_to(site_path)

    # Test de la config Nginx AVANT de recharger (protège geoclic)
    code, out, err_out = run_sudo("nginx -t")
    if code == 0:
        ok("Config Nginx valide")
        run_sudo("systemctl reload nginx")
        ok("Nginx rechargé")
    else:
        err(f"Config Nginx invalide — geoclic PRÉSERVÉ, PEA non activé")
        err(f"Erreur : {err_out}")
        site_path.unlink(missing_ok=True)
        link_path.unlink(missing_ok=True)
        sys.exit(1)


def _configurer_ssl(cfg):
    titre("→ Certificat SSL Let's Encrypt")

    code, out, _ = run_sudo(
        f"certbot --nginx --non-interactive --agree-tos "
        f"--email {cfg['email_certbot']} "
        f"--domains {cfg['domaine']} --redirect"
    )
    if code == 0:
        ok(f"SSL activé pour {cfg['domaine']}")
    else:
        warn("SSL non configuré — vérifier que le DNS pointe bien vers ce VPS")
        warn("Relancer manuellement : certbot --nginx -d " + cfg['domaine'])


def _demarrer_services(cfg):
    titre("→ Démarrage des services")

    services = ['pea_gunicorn', 'pea_celery_worker', 'pea_celery_beat']
    for s in services:
        code, out, _ = run_sudo(f"supervisorctl start {s}")
        if 'RUNNING' in out or code == 0:
            ok(f"{s} démarré")
        else:
            warn(f"{s} : {out or 'vérifier supervisorctl status'}")


def _afficher_resume(cfg):
    titre("INSTALLATION TERMINÉE")
    sep()
    ok(f"Dashboard    : https://{cfg['domaine']}")
    ok(f"Admin Django : https://{cfg['domaine']}/admin")
    ok(f"Gunicorn     : port {cfg['port_gunicorn']}")
    ok(f"Redis URL    : {cfg['redis_url']}")
    sep()
    warn("ACTIONS REQUISES avant le premier lancement :")
    warn(f"  1. Renseigner les clés API dans {cfg['appdir']}/.env")
    warn(f"  2. python manage.py migrate")
    warn(f"  3. python manage.py createsuperuser")
    warn(f"  4. cd frontend && npm ci && npm run build")
    sep()
    info("Commandes de vérification :")
    info("  supervisorctl status")
    info(f"  tail -f {cfg['logdir']}/pea.log")
    info(f"  tail -f {cfg['logdir']}/celery_worker.log")


# ---------------------------------------------------------------------------
# VÉRIFICATION POST-INSTALL
# ---------------------------------------------------------------------------

def verifier():
    titre("VÉRIFICATION POST-INSTALLATION")
    sep()

    checks = [
        ("Nginx actif",           service_actif('nginx')),
        ("PostgreSQL actif",      service_actif('postgresql')),
        ("Redis actif",           not port_libre(6379) or not port_libre(6380)),
        ("Supervisor actif",      service_actif('supervisor')),
        ("Gunicorn PEA",          any(not port_libre(p) for p in PORT_RANGE_GUNICORN)),
        ("Config .env",           Path(CONFIG['appdir'] + '/.env').exists()),
        ("Répertoire logs",       Path(CONFIG['logdir']).exists()),
        ("Frontend build",        Path(CONFIG['appdir'] + '/frontend/build').exists()),
        ("Geoclic préservé",      Path(CONFIG['geoclic_appdir']).exists()),
    ]

    for label, resultat in checks:
        if resultat:
            ok(f"{label}")
        else:
            warn(f"{label} — à vérifier")

    # Tester que l'API répond
    code, out, _ = run(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{CONFIG['port_gunicorn']}/api/quota/ 2>/dev/null")
    if out == '200':
        ok("API Django répond (200 OK)")
    elif out == '403':
        ok("API Django répond (403 — CSRF attendu, c'est normal)")
    else:
        warn(f"API Django : HTTP {out or 'pas de réponse'} — vérifier Gunicorn")


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _generer_mdp(longueur=20) -> str:
    import secrets, string
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(secrets.choice(chars) for _ in range(longueur))


def _generer_secret_key(longueur=50) -> str:
    import secrets, string
    chars = string.ascii_letters + string.digits + "!@#$%^&*(-_=+)"
    return ''.join(secrets.choice(chars) for _ in range(longueur))


# ---------------------------------------------------------------------------
# POINT D'ENTRÉE
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Déploiement intelligent PEA Dashboard sur VPS OVH'
    )
    parser.add_argument('--scan',    action='store_true', help='Scan seul sans installer')
    parser.add_argument('--install', action='store_true', help='Scan + installation complète')
    parser.add_argument('--check',   action='store_true', help='Vérification post-installation')
    args = parser.parse_args()

    if args.check:
        verifier()
    elif args.scan:
        rapport = scanner_serveur()
        titre("CONFIGURATION QUI SERAIT APPLIQUÉE")
        cfg = generer_config(rapport)
        for k, v in cfg.items():
            info(f"{k:25} : {v}")
    elif args.install:
        if os.geteuid() != 0:
            err("Installation requiert les droits root : sudo python3 pea_deploy.py --install")
            sys.exit(1)
        rapport = scanner_serveur()
        installer(rapport)
    else:
        parser.print_help()
        print(f"\n  {C.JAUNE}Exemple : sudo python3 pea_deploy.py --install{C.RESET}\n")
