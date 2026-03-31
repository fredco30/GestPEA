#!/usr/bin/env python3
"""
pea_deploy.py — Deploiement complet PEA Dashboard sur VPS OVH
===============================================================
Detecte les ports/services existants (geoclic, etc.) pour eviter les conflits,
puis installe tout : clone, venv, pip, migrate, build frontend, supervisor, nginx.

Usage :
    python3 pea_deploy.py --scan
    python3 pea_deploy.py --install --domain pea.mondomaine.fr --email fred@mondomaine.fr
    python3 pea_deploy.py --install --domain pea.mondomaine.fr --email fred@mondomaine.fr --no-ssl
    python3 pea_deploy.py --check

Prerequis :
    Python 3.10+ sur le VPS, acces sudo/root
"""

import argparse
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# CONFIGURATION (valeurs par defaut, surchargees par CLI)
# ---------------------------------------------------------------------------

DEFAULTS = {
    'appdir':          '/var/www/pea',
    'logdir':          '/var/www/pea/logs',
    'db_name':         'pea_db',
    'db_user':         'pea_user',
    'repo_url':        'https://github.com/fredco30/GestPEA.git',
    'geoclic_appdir':  '/var/www/geoclic_final',
    'node_major':      '20',
}

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
    ports_occupes:       dict = field(default_factory=dict)
    services_actifs:     list = field(default_factory=list)
    nginx_ok:            bool = False
    nginx_sites:         list = field(default_factory=list)
    postgres_ok:         bool = False
    postgres_dbs:        list = field(default_factory=list)
    redis_ports:         list = field(default_factory=list)
    geoclic_detecte:     bool = False
    port_gunicorn_libre: int  = 8001
    port_redis_libre:    int  = 6380
    conflits:            list = field(default_factory=list)
    recommandations:     list = field(default_factory=list)


# ---------------------------------------------------------------------------
# COULEURS TERMINAL
# ---------------------------------------------------------------------------

class C:
    VERT  = '\033[92m'
    ROUGE = '\033[91m'
    JAUNE = '\033[93m'
    BLEU  = '\033[94m'
    GRIS  = '\033[90m'
    BOLD  = '\033[1m'
    RESET = '\033[0m'

def ok(msg):    print(f"  {C.VERT}[OK]{C.RESET} {msg}")
def err(msg):   print(f"  {C.ROUGE}[ERR]{C.RESET} {msg}")
def warn(msg):  print(f"  {C.JAUNE}[WARN]{C.RESET} {msg}")
def info(msg):  print(f"  {C.BLEU}[->]{C.RESET} {msg}")
def titre(msg): print(f"\n{C.BOLD}{msg}{C.RESET}")
def sep():      print(f"  {C.GRIS}{'=' * 56}{C.RESET}")


# ---------------------------------------------------------------------------
# UTILITAIRES SYSTEME
# ---------------------------------------------------------------------------

def run(cmd: str, capture=True) -> tuple:
    """Execute une commande shell. Retourne (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, shell=True, capture_output=capture, text=True, check=False
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def run_sudo(cmd: str) -> tuple:
    if os.geteuid() != 0:
        cmd = f"sudo {cmd}"
    return run(cmd)


def run_or_die(cmd: str, msg: str = ""):
    """Execute et quitte en cas d'erreur."""
    code, out, err_out = run_sudo(cmd)
    if code != 0:
        err(msg or f"Echec : {cmd}")
        if err_out:
            err(err_out)
        sys.exit(1)
    return out


def port_libre(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) != 0


def premier_port_libre(plage) -> int:
    for p in plage:
        if port_libre(p):
            return p
    raise RuntimeError(f"Aucun port libre dans la plage {list(plage)[:3]}...")


def service_actif(nom: str) -> bool:
    code, _, _ = run(f"systemctl is-active {nom} 2>/dev/null")
    return code == 0


def cmd_existe(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def generer_mdp(longueur=24) -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(longueur))


def generer_secret_key(longueur=50) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*(-_=+)"
    return ''.join(secrets.choice(chars) for _ in range(longueur))


# ---------------------------------------------------------------------------
# SCAN PRINCIPAL
# ---------------------------------------------------------------------------

def scanner_serveur() -> RapportScan:
    rapport = RapportScan()
    titre("SCAN DU SERVEUR EN COURS...")
    sep()

    # --- 1. Ports TCP occupes ---
    titre("1. Ports TCP en ecoute")
    code, out, _ = run("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null")
    ports_detectes = {}

    for ligne in out.splitlines():
        m_port = re.search(r':(\d+)\s', ligne)
        m_proc = re.search(r'"([^"]+)"', ligne) or re.search(r"users:\(\(\"?([^\",)]+)", ligne)
        if m_port:
            port = int(m_port.group(1))
            proc = m_proc.group(1) if m_proc else 'inconnu'
            ports_detectes[port] = proc

    rapport.ports_occupes = ports_detectes

    ports_surveilles = [80, 443, 3000, 5432, 6379, 6380, 8000, 8001, 8002, 8080]
    for p in sorted(set(ports_surveilles) | set(ports_detectes.keys())):
        if p in ports_detectes:
            warn(f"Port {p:5} -- OCCUPE par '{ports_detectes[p]}'")
        elif p in ports_surveilles:
            ok(f"Port {p:5} -- libre")

    # --- 2. Nginx ---
    titre("2. Nginx")
    rapport.nginx_ok = cmd_existe('nginx') and service_actif('nginx')
    if rapport.nginx_ok:
        ok("Nginx actif")
        _, sites_enabled, _ = run("ls /etc/nginx/sites-enabled/ 2>/dev/null")
        rapport.nginx_sites = [s for s in sites_enabled.splitlines() if s]
        for s in rapport.nginx_sites:
            info(f"Site actif : {s}")
            if 'geoclic' in s.lower():
                rapport.geoclic_detecte = True
                ok("  geoclic.fr detecte et preserve")
    else:
        warn("Nginx non installe ou inactif")

    # --- 3. PostgreSQL ---
    titre("3. PostgreSQL")
    rapport.postgres_ok = cmd_existe('psql') and service_actif('postgresql')
    if rapport.postgres_ok:
        ok("PostgreSQL actif")
        code, dbs, _ = run("sudo -u postgres psql -tc '\\l' 2>/dev/null | grep '|' | awk '{print $1}'")
        rapport.postgres_dbs = [d.strip() for d in dbs.splitlines() if d.strip() and d.strip() != '|']
        for db in rapport.postgres_dbs:
            info(f"Base existante : {db}")
            if db == DEFAULTS['db_name']:
                warn(f"  {DEFAULTS['db_name']} existe deja -- sera reutilisee")
    else:
        warn("PostgreSQL non installe -- sera installe")

    # --- 4. Redis ---
    titre("4. Redis")
    for port in [6379, 6380, 6381]:
        if not port_libre(port):
            rapport.redis_ports.append(port)
            ok(f"Redis detecte sur port {port}")
    if not rapport.redis_ports:
        warn("Aucune instance Redis active -- sera installee")

    # --- 5. Geoclic ---
    titre("5. Geoclic (a preserver)")
    geoclic_path = Path(DEFAULTS['geoclic_appdir'])
    if geoclic_path.exists():
        rapport.geoclic_detecte = True
        ok(f"Repertoire geoclic trouve : {geoclic_path}")
    else:
        info("Repertoire geoclic non trouve a l'emplacement par defaut")
        code, found, _ = run("find /var/www -name 'manage.py' -maxdepth 3 2>/dev/null")
        if found:
            for f in found.splitlines():
                info(f"  Application Django trouvee : {Path(f).parent}")

    # --- 6. Supervisor ---
    titre("6. Supervisor")
    code, out, _ = run("supervisorctl status 2>/dev/null")
    if code == 0 and out:
        for ligne in out.splitlines():
            if ligne.strip():
                info(f"Process supervisor : {ligne.strip()}")

    # --- 7. Node.js ---
    titre("7. Node.js")
    if cmd_existe('node'):
        _, node_ver, _ = run("node --version")
        ok(f"Node.js installe : {node_ver}")
    else:
        warn("Node.js non installe -- sera installe")

    # --- 8. Attribution des ports PEA ---
    titre("8. Attribution des ports PEA")
    try:
        rapport.port_gunicorn_libre = premier_port_libre(PORT_RANGE_GUNICORN)
        ok(f"Port Gunicorn PEA   : {rapport.port_gunicorn_libre}")
    except RuntimeError as e:
        err(str(e))
        rapport.conflits.append("Aucun port disponible pour Gunicorn PEA")

    try:
        if 6379 in rapport.redis_ports:
            rapport.port_redis_libre = 6379
            ok("Redis existant reutilise : port 6379 (DB 1 pour PEA)")
            rapport.recommandations.append("Redis partage : DB 0 pour geoclic, DB 1 pour PEA")
        else:
            rapport.port_redis_libre = premier_port_libre(PORT_RANGE_REDIS)
            ok(f"Port Redis PEA      : {rapport.port_redis_libre}")
    except RuntimeError as e:
        err(str(e))
        rapport.conflits.append("Aucun port disponible pour Redis PEA")

    # --- 9. Synthese ---
    titre("9. Synthese")
    if rapport.conflits:
        for c in rapport.conflits:
            err(f"CONFLIT : {c}")
    else:
        ok("Aucun conflit detecte -- installation possible")
    if rapport.recommandations:
        for r in rapport.recommandations:
            warn(f"Recommandation : {r}")

    return rapport


# ---------------------------------------------------------------------------
# GENERATION DE LA CONFIG
# ---------------------------------------------------------------------------

def generer_config(rapport: RapportScan, args) -> dict:
    redis_url = (
        "redis://127.0.0.1:6379/1"
        if rapport.port_redis_libre == 6379
        else f"redis://127.0.0.1:{rapport.port_redis_libre}/0"
    )

    return {
        'domaine':         args.domain,
        'email':           args.email,
        'appdir':          DEFAULTS['appdir'],
        'logdir':          DEFAULTS['logdir'],
        'db_name':         DEFAULTS['db_name'],
        'db_user':         DEFAULTS['db_user'],
        'repo_url':        args.repo,
        'port_gunicorn':   rapport.port_gunicorn_libre,
        'port_redis':      rapport.port_redis_libre,
        'redis_url':       redis_url,
        'redis_partage':   rapport.port_redis_libre == 6379,
        'postgres_existe': rapport.postgres_ok,
        'nginx_existe':    rapport.nginx_ok,
        'ssl':             args.ssl,
    }


# ---------------------------------------------------------------------------
# ETAPES D'INSTALLATION
# ---------------------------------------------------------------------------

def installer(rapport: RapportScan, args):
    if rapport.conflits:
        err("Des conflits ont ete detectes. Resoudre avant d'installer.")
        sys.exit(1)

    cfg = generer_config(rapport, args)

    titre("INSTALLATION PEA DASHBOARD")
    sep()
    info(f"Domaine       : {cfg['domaine']}")
    info(f"Repertoire    : {cfg['appdir']}")
    info(f"Port Gunicorn : {cfg['port_gunicorn']}")
    info(f"Redis URL     : {cfg['redis_url']}")
    info(f"PostgreSQL    : {'partage' if cfg['postgres_existe'] else 'nouvelle installation'}")
    info(f"Nginx         : {'partage' if cfg['nginx_existe'] else 'nouvelle installation'}")
    info(f"SSL           : {'oui' if cfg['ssl'] else 'non (HTTP seul)'}")
    info(f"Repo          : {cfg['repo_url']}")
    print()

    reponse = input(f"  {C.JAUNE}Confirmer l'installation ? (oui/non) : {C.RESET}")
    if reponse.strip().lower() not in ('oui', 'o', 'yes', 'y'):
        print("  Installation annulee.")
        sys.exit(0)

    _etape_paquets_systeme(cfg)
    _etape_nodejs(cfg)
    _etape_postgres(cfg)
    _etape_redis(cfg)
    _etape_clone(cfg)
    _etape_venv_pip(cfg)
    _etape_env(cfg)
    _etape_django(cfg)
    _etape_frontend(cfg)
    _etape_supervisor(cfg)
    _etape_nginx(cfg)
    if cfg['ssl']:
        _etape_ssl(cfg)
    _etape_demarrage(cfg)
    _afficher_resume(cfg)


# --- 1. Paquets systeme ---

def _etape_paquets_systeme(cfg):
    titre("ETAPE 1/12 : Paquets systeme")

    run_sudo("apt-get update -qq")

    paquets = [
        'python3', 'python3-venv', 'python3-pip', 'python3-dev',
        'build-essential', 'libpq-dev',
        'supervisor', 'git', 'curl',
    ]

    if not cfg['postgres_existe']:
        paquets += ['postgresql', 'postgresql-contrib']
    if not cfg['nginx_existe']:
        paquets += ['nginx']
    if not cfg['redis_partage'] and not cmd_existe('redis-server'):
        paquets += ['redis-server']
    if cfg['ssl']:
        paquets += ['certbot', 'python3-certbot-nginx']

    code, _, err_out = run_sudo(f"apt-get install -y -qq {' '.join(paquets)}")
    if code == 0:
        ok(f"{len(paquets)} paquets installes/verifies")
    else:
        err(f"Erreur apt-get : {err_out}")
        sys.exit(1)


# --- 2. Node.js ---

def _etape_nodejs(cfg):
    titre("ETAPE 2/12 : Node.js")

    if cmd_existe('node'):
        _, ver, _ = run("node --version")
        ok(f"Node.js deja installe : {ver}")
        return

    info("Installation de Node.js LTS...")
    major = DEFAULTS['node_major']
    run_sudo(f"curl -fsSL https://deb.nodesource.com/setup_{major}.x | bash -")
    run_sudo("apt-get install -y -qq nodejs")

    _, ver, _ = run("node --version")
    ok(f"Node.js installe : {ver}")


# --- 3. PostgreSQL ---

def _etape_postgres(cfg):
    titre("ETAPE 3/12 : PostgreSQL")

    if not cfg['postgres_existe']:
        run_sudo("systemctl start postgresql")
        run_sudo("systemctl enable postgresql")
        ok("PostgreSQL demarre")

    # Creer user
    code, out, _ = run(f"sudo -u postgres psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='{cfg['db_user']}'\"")
    if '1' not in out:
        mdp = generer_mdp()
        run(f"sudo -u postgres psql -c \"CREATE USER {cfg['db_user']} WITH PASSWORD '{mdp}';\"")
        cfg['db_password'] = mdp
        ok(f"Utilisateur {cfg['db_user']} cree")
    else:
        warn(f"Utilisateur {cfg['db_user']} existe deja")
        cfg['db_password'] = None

    # Creer DB
    code, out, _ = run(f"sudo -u postgres psql -tc \"SELECT 1 FROM pg_database WHERE datname='{cfg['db_name']}'\"")
    if '1' not in out:
        run(f"sudo -u postgres psql -c \"CREATE DATABASE {cfg['db_name']} OWNER {cfg['db_user']};\"")
        run(f"sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE {cfg['db_name']} TO {cfg['db_user']};\"")
        ok(f"Base {cfg['db_name']} creee")
    else:
        warn(f"Base {cfg['db_name']} existe deja -- reutilisee")


# --- 4. Redis ---

def _etape_redis(cfg):
    titre("ETAPE 4/12 : Redis")

    if cfg['redis_partage']:
        ok("Redis partage avec geoclic -- DB 1 reservee pour PEA")
        return

    run_sudo("systemctl start redis-server")
    run_sudo("systemctl enable redis-server")

    if cfg['port_redis'] != 6379:
        redis_conf = f"""\
port {cfg['port_redis']}
daemonize yes
logfile /var/log/redis/redis-pea.log
dir /var/lib/redis
pidfile /var/run/redis/redis-pea.pid
"""
        conf_path = Path('/etc/redis/redis-pea.conf')
        conf_path.write_text(redis_conf)
        run_sudo(f"redis-server /etc/redis/redis-pea.conf")
        ok(f"Redis PEA demarre sur port {cfg['port_redis']}")
    else:
        ok("Redis demarre sur port 6379")


# --- 5. Clone du depot ---

def _etape_clone(cfg):
    titre("ETAPE 5/12 : Clone du depot Git")

    appdir = Path(cfg['appdir'])

    if (appdir / '.git').exists():
        info("Depot deja clone -- git pull")
        code, out, err_out = run(f"cd {cfg['appdir']} && git pull")
        if code == 0:
            ok("Code mis a jour")
        else:
            warn(f"git pull echoue : {err_out}")
    else:
        # Creer le repertoire parent si besoin
        appdir.parent.mkdir(parents=True, exist_ok=True)

        # Si le repertoire existe mais sans .git, le sauvegarder
        if appdir.exists() and any(appdir.iterdir()):
            backup = appdir.with_name('pea_backup')
            warn(f"Repertoire {appdir} non-vide sans .git -- sauvegarde en {backup}")
            if backup.exists():
                shutil.rmtree(backup)
            appdir.rename(backup)

        code, _, err_out = run(f"git clone {cfg['repo_url']} {cfg['appdir']}")
        if code != 0:
            err(f"Clone echoue : {err_out}")
            sys.exit(1)
        ok(f"Depot clone dans {cfg['appdir']}")

    # Creer les repertoires necessaires
    for d in ['logs', 'staticfiles', 'media']:
        (appdir / d).mkdir(exist_ok=True)

    # Permissions
    run_sudo(f"chown -R www-data:www-data {cfg['appdir']}")
    ok("Repertoires et permissions configures")


# --- 6. Virtualenv + pip ---

def _etape_venv_pip(cfg):
    titre("ETAPE 6/12 : Virtualenv + pip install")

    venv = f"{cfg['appdir']}/venv"

    if not Path(venv, 'bin', 'activate').exists():
        code, _, err_out = run(f"python3 -m venv {venv}")
        if code != 0:
            err(f"Creation venv echouee : {err_out}")
            sys.exit(1)
        ok("Virtualenv cree")
    else:
        ok("Virtualenv existe deja")

    pip = f"{venv}/bin/pip"
    req = f"{cfg['appdir']}/requirements.txt"

    run(f"{pip} install --upgrade pip -q")
    code, _, err_out = run(f"{pip} install -r {req} -q")
    if code != 0:
        err(f"pip install echoue : {err_out}")
        sys.exit(1)
    ok("Dependances Python installees")


# --- 7. Fichier .env ---

def _etape_env(cfg):
    titre("ETAPE 7/12 : Fichier .env")

    env_path = Path(cfg['appdir']) / '.env'

    if env_path.exists():
        warn(".env existe deja -- conserve tel quel")
        warn("Verifier que les valeurs sont correctes")
        return

    secret_key = generer_secret_key()
    mdp = cfg.get('db_password') or 'CHANGER_MOT_DE_PASSE'

    schema = 'https' if cfg['ssl'] else 'http'

    env_content = f"""\
# PEA Dashboard -- Configuration generee par pea_deploy.py
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

# Redis
REDIS_URL={cfg['redis_url']}

# EODHD -- https://eodhd.com/register
EODHD_API_KEY=
EODHD_QUOTA_JOUR=20

# Mistral AI -- https://console.mistral.ai
MISTRAL_API_KEY=

# NewsAPI -- https://newsapi.org
NEWSAPI_KEY=

# FMP -- https://financialmodelingprep.com
FMP_API_KEY=

# Email
EMAIL_DESTINATAIRE=
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Dashboard
DASHBOARD_URL={schema}://{cfg['domaine']}
CORS_ALLOWED_ORIGINS={schema}://{cfg['domaine']}
"""

    env_path.write_text(env_content)
    os.chmod(str(env_path), 0o600)
    ok(f".env cree dans {env_path}")
    if mdp != 'CHANGER_MOT_DE_PASSE':
        info(f"Mot de passe DB genere : {mdp}")
    warn("Renseigner les cles API dans .env avant utilisation")


# --- 8. Django migrate + collectstatic ---

def _etape_django(cfg):
    titre("ETAPE 8/12 : Django migrate + collectstatic")

    venv = f"{cfg['appdir']}/venv"
    manage = f"{venv}/bin/python {cfg['appdir']}/manage.py"

    info("Migrations...")
    code, out, err_out = run(f"cd {cfg['appdir']} && {manage} migrate --noinput")
    if code != 0:
        err(f"Migrations echouees : {err_out}")
        warn("Verifier .env (DB_PASSWORD) et que PostgreSQL tourne")
        sys.exit(1)
    ok("Migrations appliquees")

    info("Collectstatic...")
    code, _, err_out = run(f"cd {cfg['appdir']} && {manage} collectstatic --noinput")
    if code != 0:
        warn(f"collectstatic echoue : {err_out}")
    else:
        ok("Fichiers statiques collectes")

    # Rappel createsuperuser
    warn("Penser a creer un superuser :")
    warn(f"  cd {cfg['appdir']} && source venv/bin/activate && python manage.py createsuperuser")


# --- 9. Build frontend React ---

def _etape_frontend(cfg):
    titre("ETAPE 9/12 : Build frontend React")

    frontend = f"{cfg['appdir']}/frontend"

    if not Path(frontend, 'package.json').exists():
        warn("Pas de package.json dans frontend/ -- etape ignoree")
        return

    info("npm ci (install propre)...")
    code, _, err_out = run(f"cd {frontend} && npm ci --silent 2>&1")
    if code != 0:
        # Fallback sur npm install si ci echoue (pas de package-lock)
        warn("npm ci echoue, tentative npm install...")
        code, _, err_out = run(f"cd {frontend} && npm install --silent 2>&1")
        if code != 0:
            err(f"npm install echoue : {err_out}")
            sys.exit(1)
    ok("Dependances npm installees")

    info("npm run build...")
    code, _, err_out = run(f"cd {frontend} && npm run build 2>&1")
    if code != 0:
        err(f"Build React echoue : {err_out}")
        sys.exit(1)
    ok("Frontend React compile")

    # Permissions pour www-data
    run_sudo(f"chown -R www-data:www-data {frontend}/build")


# --- 10. Supervisor (Gunicorn + Celery) ---

def _etape_supervisor(cfg):
    titre("ETAPE 10/12 : Supervisor (Gunicorn + Celery)")

    appdir = cfg['appdir']
    venv   = f"{appdir}/venv"
    logdir = cfg['logdir']
    port   = cfg['port_gunicorn']

    # Gunicorn
    gunicorn_conf = f"""\
[program:pea_gunicorn]
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
    worker_conf = f"""\
[program:pea_celery_worker]
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

    # Celery beat (scheduler fichier par defaut -- pas besoin de django-celery-beat
    # car CELERY_BEAT_SCHEDULE est defini dans settings.py)
    beat_conf = f"""\
[program:pea_celery_beat]
command={venv}/bin/celery -A pea_project beat --loglevel=info --schedule={appdir}/celerybeat-schedule
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
        ok(f"Config supervisor : {path.name}")

    run_sudo("supervisorctl reread")
    run_sudo("supervisorctl update")
    ok("Supervisor mis a jour")


# --- 11. Nginx ---

def _etape_nginx(cfg):
    titre("ETAPE 11/12 : Nginx (virtual host PEA)")

    appdir  = cfg['appdir']
    domaine = cfg['domaine']
    port    = cfg['port_gunicorn']

    nginx_conf = f"""\
# PEA Dashboard -- {domaine}
# Genere par pea_deploy.py -- ne pas modifier manuellement

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

    # API Django -> Gunicorn (port {port})
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

    # React Router SPA -- routes inconnues -> index.html
    location / {{
        try_files $uri $uri/ /index.html;
    }}
}}
"""

    site_available = Path(f"/etc/nginx/sites-available/pea")
    site_enabled   = Path(f"/etc/nginx/sites-enabled/pea")

    site_available.write_text(nginx_conf)

    if not site_enabled.exists():
        site_enabled.symlink_to(site_available)

    # Test config AVANT reload (protege geoclic)
    code, _, err_out = run_sudo("nginx -t")
    if code == 0:
        ok("Config Nginx valide")
        run_sudo("systemctl reload nginx")
        ok("Nginx recharge")
    else:
        err(f"Config Nginx invalide -- geoclic PRESERVE, PEA non active")
        err(f"Erreur : {err_out}")
        site_available.unlink(missing_ok=True)
        site_enabled.unlink(missing_ok=True)
        sys.exit(1)


# --- 12. SSL (optionnel) ---

def _etape_ssl(cfg):
    titre("ETAPE 12/12 : Certificat SSL Let's Encrypt")

    code, _, err_out = run_sudo(
        f"certbot --nginx --non-interactive --agree-tos "
        f"--email {cfg['email']} "
        f"--domains {cfg['domaine']} --redirect"
    )
    if code == 0:
        ok(f"SSL active pour {cfg['domaine']}")
    else:
        warn("SSL non configure -- verifier que le DNS pointe vers ce VPS")
        warn(f"Relancer manuellement : certbot --nginx -d {cfg['domaine']}")


# --- Demarrage ---

def _etape_demarrage(cfg):
    titre("Demarrage des services PEA")

    services = ['pea_gunicorn', 'pea_celery_worker', 'pea_celery_beat']
    for s in services:
        run_sudo(f"supervisorctl stop {s} 2>/dev/null")
        code, out, _ = run_sudo(f"supervisorctl start {s}")
        if code == 0:
            ok(f"{s} demarre")
        else:
            warn(f"{s} : {out or 'verifier supervisorctl status'}")


def _afficher_resume(cfg):
    schema = 'https' if cfg['ssl'] else 'http'
    titre("INSTALLATION TERMINEE")
    sep()
    ok(f"Dashboard    : {schema}://{cfg['domaine']}")
    ok(f"Admin Django : {schema}://{cfg['domaine']}/admin")
    ok(f"Gunicorn     : port {cfg['port_gunicorn']}")
    ok(f"Redis URL    : {cfg['redis_url']}")
    sep()
    warn("ACTIONS REQUISES :")
    warn(f"  1. Renseigner les cles API dans {cfg['appdir']}/.env")
    warn(f"  2. Creer un superuser Django :")
    warn(f"     cd {cfg['appdir']} && source venv/bin/activate")
    warn(f"     python manage.py createsuperuser")
    sep()
    info("Commandes utiles :")
    info("  supervisorctl status")
    info(f"  tail -f {cfg['logdir']}/pea.log")
    info(f"  tail -f {cfg['logdir']}/celery_worker.log")
    info("")
    info("Mise a jour du code :")
    info(f"  cd {cfg['appdir']} && git pull")
    info(f"  source venv/bin/activate && pip install -r requirements.txt")
    info(f"  python manage.py migrate --noinput")
    info(f"  python manage.py collectstatic --noinput")
    info(f"  cd frontend && npm ci && npm run build")
    info(f"  supervisorctl restart pea_gunicorn pea_celery_worker pea_celery_beat")


# ---------------------------------------------------------------------------
# VERIFICATION POST-INSTALL
# ---------------------------------------------------------------------------

def verifier():
    titre("VERIFICATION POST-INSTALLATION")
    sep()

    appdir = DEFAULTS['appdir']
    checks = [
        ("Nginx actif",           service_actif('nginx')),
        ("PostgreSQL actif",      service_actif('postgresql')),
        ("Redis actif",           not port_libre(6379) or not port_libre(6380)),
        ("Supervisor actif",      service_actif('supervisor')),
        ("Gunicorn PEA",          any(not port_libre(p) for p in PORT_RANGE_GUNICORN)),
        ("Config .env",           Path(f"{appdir}/.env").exists()),
        ("Virtualenv",            Path(f"{appdir}/venv/bin/activate").exists()),
        ("Repertoire logs",       Path(f"{appdir}/logs").exists()),
        ("Frontend build",        Path(f"{appdir}/frontend/build/index.html").exists()),
    ]

    # Detecter geoclic si present
    if Path(DEFAULTS['geoclic_appdir']).exists():
        checks.append(("Geoclic preserve", True))

    for label, resultat in checks:
        if resultat:
            ok(label)
        else:
            warn(f"{label} -- a verifier")

    # Tester que l'API repond
    for port in PORT_RANGE_GUNICORN:
        if not port_libre(port):
            code, out, _ = run(
                f"curl -s -o /dev/null -w '%{{http_code}}' "
                f"http://127.0.0.1:{port}/api/quota/ 2>/dev/null"
            )
            if out in ('200', '403', '401'):
                ok(f"API Django repond sur port {port} (HTTP {out})")
            else:
                warn(f"API Django : HTTP {out or 'pas de reponse'} sur port {port}")
            break


# ---------------------------------------------------------------------------
# POINT D'ENTREE
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Deploiement complet PEA Dashboard sur VPS OVH',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Exemples :
  python3 pea_deploy.py --scan
  sudo python3 pea_deploy.py --install --domain pea.exemple.fr --email fred@exemple.fr
  sudo python3 pea_deploy.py --install --domain pea.exemple.fr --email fred@exemple.fr --no-ssl
  python3 pea_deploy.py --check
"""
    )
    parser.add_argument('--scan',    action='store_true', help='Scan seul sans installer')
    parser.add_argument('--install', action='store_true', help='Scan + installation complete')
    parser.add_argument('--check',   action='store_true', help='Verification post-installation')

    parser.add_argument('--domain',  type=str, default=None,
                        help='Domaine pour le dashboard (ex: pea.mondomaine.fr)')
    parser.add_argument('--email',   type=str, default=None,
                        help='Email pour Let\'s Encrypt (ex: fred@mondomaine.fr)')
    parser.add_argument('--repo',    type=str, default=DEFAULTS['repo_url'],
                        help=f'URL du depot Git (defaut: {DEFAULTS["repo_url"]})')
    parser.add_argument('--no-ssl',  action='store_true',
                        help='Ne pas installer de certificat SSL (HTTP seul)')

    args = parser.parse_args()

    # Ajouter un attribut pratique
    args.ssl = not args.no_ssl

    if args.check:
        verifier()
    elif args.scan:
        rapport = scanner_serveur()
        titre("CONFIGURATION QUI SERAIT APPLIQUEE")
        info(f"Port Gunicorn : {rapport.port_gunicorn_libre}")
        info(f"Port Redis    : {rapport.port_redis_libre}")
    elif args.install:
        if args.domain is None:
            err("--domain est requis pour l'installation")
            err("  Exemple : sudo python3 pea_deploy.py --install --domain pea.exemple.fr --email fred@exemple.fr")
            sys.exit(1)
        if args.email is None:
            err("--email est requis pour l'installation")
            sys.exit(1)
        if os.geteuid() != 0:
            err("Installation requiert les droits root : sudo python3 pea_deploy.py --install ...")
            sys.exit(1)
        rapport = scanner_serveur()
        installer(rapport, args)
    else:
        parser.print_help()
