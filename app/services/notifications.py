"""
app/services/notifications.py
-------------------------------
Service d'envoi des notifications pour les alertes PEA.

Canaux supportés :
  - Email (Django smtp via settings)
  - Telegram (bot API)
  - Webhook générique (Zapier, n8n, Make…)

Appelé par :
  - scorer_alerte_task (après génération du texte IA)
  - digest_hebdomadaire_task (vendredi soir)

Configuration dans .env :
  EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD
  EMAIL_DESTINATAIRE (ton adresse perso)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import logging
import json
from datetime import date

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# POINT D'ENTRÉE PRINCIPAL
# ---------------------------------------------------------------------------

def notifier_alerte(alerte_id: int) -> dict:
    """
    Envoie une alerte sur tous les canaux configurés pour ce titre.
    Appelée automatiquement après scorer_alerte_task.

    Retourne un dict {canal: bool} indiquant le succès par canal.
    """
    from app.models import Alerte

    try:
        alerte = Alerte.objects.select_related('titre').get(pk=alerte_id)
    except Alerte.DoesNotExist:
        logger.error(f"[Notif] Alerte {alerte_id} introuvable.")
        return {}

    config = getattr(alerte.titre, 'alerte_config', None)
    if not config or not config.actif:
        logger.debug(f"[Notif] {alerte.titre.ticker} — notifications désactivées.")
        return {}

    resultats = {}

    if config.notif_email:
        resultats['email'] = _envoyer_email(alerte)

    if config.notif_telegram:
        resultats['telegram'] = _envoyer_telegram(alerte)

    if config.notif_webhook and config.webhook_url:
        resultats['webhook'] = _envoyer_webhook(alerte, config.webhook_url)

    logger.info(
        f"[Notif] Alerte {alerte_id} ({alerte.titre.ticker}) — "
        f"résultats : {resultats}"
    )
    return resultats


def notifier_digest(texte: str) -> dict:
    """
    Envoie le digest hebdomadaire (vendredi soir).
    Envoie sur email + Telegram si configurés globalement.
    """
    resultats = {}

    email_dest = getattr(settings, 'EMAIL_DESTINATAIRE', '')
    if email_dest:
        resultats['email'] = _envoyer_email_digest(texte, email_dest)

    if getattr(settings, 'TELEGRAM_BOT_TOKEN', '') and getattr(settings, 'TELEGRAM_CHAT_ID', ''):
        resultats['telegram'] = _envoyer_telegram_texte(texte)

    return resultats


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def _envoyer_email(alerte) -> bool:
    """Envoie une alerte par email."""
    destinataire = getattr(settings, 'EMAIL_DESTINATAIRE', '')
    if not destinataire:
        logger.warning("[Notif Email] EMAIL_DESTINATAIRE non configuré dans .env")
        return False

    titre  = alerte.titre
    niveau = alerte.niveau.upper()
    emoji_niveau = {'FORTE': '🔴', 'MODEREE': '🟡', 'SURVEILLANCE': '⚪'}.get(niveau, '⚪')

    sujet = (
        f"{emoji_niveau} PEA — {titre.nom_court or titre.ticker} · "
        f"Score {alerte.score_confluence}/10 · {alerte.date_signal}"
    )

    # Corps texte brut
    corps_texte = f"""
{alerte.texte_ia}

---
Cours au signal : {alerte.cours_au_signal} €
RSI : {alerte.rsi_au_signal or 'N/D'}
Sentiment : {alerte.sentiment_au_signal or 'N/D'}
Date : {alerte.date_signal}

Consulter le dashboard : {getattr(settings, 'DASHBOARD_URL', 'http://localhost:3000')}
"""

    # Corps HTML
    corps_html = _template_email_alerte(alerte)

    try:
        send_mail(
            subject=sujet,
            message=corps_texte.strip(),
            from_email=getattr(settings, 'EMAIL_HOST_USER', 'pea@mondomaine.fr'),
            recipient_list=[destinataire],
            html_message=corps_html,
            fail_silently=False,
        )
        logger.info(f"[Notif Email] Alerte envoyée à {destinataire} — {titre.ticker}")
        return True
    except Exception as e:
        logger.error(f"[Notif Email] Erreur envoi : {e}", exc_info=True)
        return False


def _envoyer_email_digest(texte: str, destinataire: str) -> bool:
    """Envoie le digest hebdomadaire par email."""
    sujet = f"📊 Digest PEA — semaine du {date.today().strftime('%d/%m/%Y')}"
    corps_html = f"""
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; color: #1a1a18;">
  <h2 style="font-size: 16px; font-weight: 500; margin-bottom: 16px;">
    📊 Digest PEA — {date.today().strftime('%d/%m/%Y')}
  </h2>
  <div style="white-space: pre-line; font-size: 14px; line-height: 1.7; color: #3d3d3a;">
{texte}
  </div>
  <hr style="margin: 20px 0; border: none; border-top: 1px solid #eee;">
  <p style="font-size: 11px; color: #888; margin: 0;">
    PEA Dashboard — <a href="{getattr(settings, 'DASHBOARD_URL', 'http://localhost:3000')}">Consulter le dashboard</a>
  </p>
</body>
</html>"""

    try:
        send_mail(
            subject=sujet,
            message=texte,
            from_email=getattr(settings, 'EMAIL_HOST_USER', 'pea@mondomaine.fr'),
            recipient_list=[destinataire],
            html_message=corps_html,
            fail_silently=False,
        )
        logger.info(f"[Notif Email] Digest envoyé à {destinataire}")
        return True
    except Exception as e:
        logger.error(f"[Notif Email] Erreur digest : {e}", exc_info=True)
        return False


def _template_email_alerte(alerte) -> str:
    """Génère le HTML de l'email d'alerte."""
    titre   = alerte.titre
    niveau  = alerte.niveau
    couleur = {'forte': '#E24B4A', 'moderee': '#BA7517', 'surveillance': '#888780'}.get(niveau, '#888780')
    bg      = {'forte': '#fcebeb', 'moderee': '#faeeda', 'surveillance': '#f1efe8'}.get(niveau, '#f1efe8')

    signaux_html = ''
    for s in alerte.signaux.all():
        icone = '↑' if s.direction == 'haussier' else '↓' if s.direction == 'baissier' else '→'
        signaux_html += f'<li style="margin-bottom: 4px;">{icone} {s.description}</li>'

    return f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; color: #1a1a18; background: #ffffff;">

  <!-- En-tête -->
  <div style="border-left: 3px solid {couleur}; padding: 12px 16px; background: {bg}; border-radius: 0 8px 8px 0; margin-bottom: 20px;">
    <div style="font-size: 13px; color: {couleur}; font-weight: 500; text-transform: uppercase; margin-bottom: 4px;">
      Alerte {niveau} · Score {alerte.score_confluence}/10
    </div>
    <div style="font-size: 20px; font-weight: 500;">{titre.nom_court or titre.nom} ({titre.ticker})</div>
    <div style="font-size: 13px; color: #5f5e5a; margin-top: 4px;">
      Cours au signal : <strong>{alerte.cours_au_signal} €</strong>
      {f'· RSI : {alerte.rsi_au_signal}' if alerte.rsi_au_signal else ''}
      · {alerte.date_signal}
    </div>
  </div>

  <!-- Signaux -->
  {'<div style="margin-bottom: 16px;"><div style="font-size: 12px; color: #888; margin-bottom: 6px; text-transform: uppercase;">Signaux détectés</div><ul style="margin: 0; padding-left: 18px; font-size: 13px; color: #3d3d3a; line-height: 1.8;">' + signaux_html + '</ul></div>' if signaux_html else ''}

  <!-- Texte IA -->
  <div style="font-size: 14px; line-height: 1.7; color: #3d3d3a; white-space: pre-line; margin-bottom: 20px;">
{alerte.texte_ia}
  </div>

  <!-- CTA -->
  <a href="{getattr(settings, 'DASHBOARD_URL', 'http://localhost:3000')}"
     style="display: inline-block; padding: 10px 20px; background: #1a1a18; color: #fff; border-radius: 8px; text-decoration: none; font-size: 13px; font-weight: 500;">
    Voir dans le dashboard →
  </a>

  <hr style="margin: 20px 0; border: none; border-top: 1px solid #eee;">
  <p style="font-size: 11px; color: #aaa; margin: 0;">
    Cette observation ne constitue pas un conseil d'investissement.
    Vous recevez cet email car vous avez activé les alertes pour {titre.ticker}.
  </p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def _envoyer_telegram(alerte) -> bool:
    """Envoie une alerte via le bot Telegram."""
    texte = _formater_telegram_alerte(alerte)
    return _envoyer_telegram_texte(texte)


def _envoyer_telegram_texte(texte: str) -> bool:
    """Envoie un message texte brut via Telegram."""
    token   = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')

    if not token or not chat_id:
        logger.warning("[Notif Telegram] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant.")
        return False

    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        'chat_id':    chat_id,
        'text':       texte,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }

    try:
        resp = requests.post(url, json=data, timeout=10)
        if resp.status_code == 200:
            logger.info(f"[Notif Telegram] Message envoyé (chat {chat_id})")
            return True
        else:
            logger.error(f"[Notif Telegram] Erreur HTTP {resp.status_code} : {resp.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"[Notif Telegram] Erreur réseau : {e}", exc_info=True)
        return False


def _formater_telegram_alerte(alerte) -> str:
    """Formate une alerte pour Telegram (HTML Telegram)."""
    titre   = alerte.titre
    niveau  = alerte.niveau
    emoji   = {'forte': '🔴', 'moderee': '🟡', 'surveillance': '⚪'}.get(niveau, '⚪')

    # Résumé des signaux
    signaux = alerte.signaux.all()
    lignes_signaux = '\n'.join(
        f"  {'↑' if s.direction == 'haussier' else '↓' if s.direction == 'baissier' else '→'} {s.description}"
        for s in signaux
    )

    # Extraire les 3 premières lignes du texte IA (sans le disclaimer)
    lignes_ia = [l for l in alerte.texte_ia.split('\n') if l.strip() and 'conseil' not in l.lower()]
    resume_ia = '\n'.join(lignes_ia[:4])

    fiabilite_str = ''
    if alerte.fiabilite_historique and alerte.nb_occurrences_passees:
        fiabilite_str = (
            f"\n📈 <b>Fiabilité historique :</b> {alerte.fiabilite_historique}% "
            f"({alerte.nb_occurrences_passees} occurrences similaires)"
        )

    return f"""{emoji} <b>{titre.nom_court or titre.nom}</b> ({titre.ticker})
<b>Score confluence : {alerte.score_confluence}/10</b> · {alerte.date_signal}

<b>Cours :</b> {alerte.cours_au_signal} €{f'  |  RSI : {alerte.rsi_au_signal}' if alerte.rsi_au_signal else ''}{f'  |  Sentiment : {alerte.sentiment_au_signal:+.2f}' if alerte.sentiment_au_signal else ''}

<b>Signaux :</b>
{lignes_signaux}

{resume_ia}{fiabilite_str}

<i>Cette observation ne constitue pas un conseil d'investissement.</i>"""


# ---------------------------------------------------------------------------
# WEBHOOK GÉNÉRIQUE
# ---------------------------------------------------------------------------

def _envoyer_webhook(alerte, url: str) -> bool:
    """
    Envoie les données de l'alerte vers un webhook (Zapier, n8n, Make…).
    Payload JSON standardisé.
    """
    payload = {
        'event':      'alerte_pea',
        'version':    '1.0',
        'timestamp':  alerte.date_detection.isoformat(),
        'alerte': {
            'id':               alerte.id,
            'ticker':           alerte.titre.ticker,
            'nom':              alerte.titre.nom_court or alerte.titre.nom,
            'score':            float(alerte.score_confluence),
            'niveau':           alerte.niveau,
            'cours':            float(alerte.cours_au_signal),
            'rsi':              float(alerte.rsi_au_signal) if alerte.rsi_au_signal else None,
            'sentiment':        float(alerte.sentiment_au_signal) if alerte.sentiment_au_signal else None,
            'date_signal':      str(alerte.date_signal),
            'texte_ia':         alerte.texte_ia,
            'fiabilite_pct':    float(alerte.fiabilite_historique) if alerte.fiabilite_historique else None,
            'signaux':          [
                {'type': s.type_signal, 'direction': s.direction, 'description': s.description}
                for s in alerte.signaux.all()
            ],
        },
    }

    try:
        resp = requests.post(
            url, json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10,
        )
        if resp.status_code in (200, 201, 202, 204):
            logger.info(f"[Notif Webhook] Payload envoyé à {url}")
            return True
        else:
            logger.error(f"[Notif Webhook] HTTP {resp.status_code} sur {url}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"[Notif Webhook] Erreur réseau : {e}", exc_info=True)
        return False
