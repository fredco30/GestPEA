"""
app/services/tradingagents_bridge.py
------------------------------------
Pont entre GestPEA (Django) et TradingAgents (outil multi-agents, venv séparé).

TradingAgents tourne dans son propre environnement Python (dépendances
incompatibles avec Django) : on l'invoque donc en SOUS-PROCESSUS via le script
`scripts/gestpea_ta_bridge.py`, exécuté par le Python du venv TradingAgents.

Flux :
  1. lancer_analyse(ticker, date)  → sous-processus → JSON (note + rapports EN)
  2. resumer_francais(titre, data) → Mistral → synthèse FR accessible
  3. analyser_titre(ticker)        → orchestre + persiste sur le Titre

Cette analyse est LONGUE (2-5 min) et coûte quelques centimes : à appeler
depuis une tâche Celery, jamais en synchrone dans une requête HTTP.
"""

import os
import json
import logging
import subprocess

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

SENTINEL = "===GESTPEA_TA_JSON==="

MODEL_SYNTHESE = "mistral-large-latest"


# ---------------------------------------------------------------------------
# 1. Sous-processus TradingAgents
# ---------------------------------------------------------------------------

def lancer_analyse(ticker: str, analyse_date: str) -> dict:
    """
    Exécute TradingAgents sur (ticker, date) via son venv dédié et renvoie le
    dict JSON émis par le script pont. Lève RuntimeError si la sortie est illisible.
    """
    cmd = [
        settings.TRADINGAGENTS_PYTHON,
        settings.TRADINGAGENTS_SCRIPT,
        ticker,
        analyse_date,
    ]
    # HOME pointé vers un dossier appartenant à www-data : TradingAgents y écrit
    # son état (~/.tradingagents) — sinon échec d'écriture sous l'utilisateur de service.
    env = {
        **os.environ,
        "GESTPEA_TA_ENV": settings.TRADINGAGENTS_ENV,
        "HOME": settings.TRADINGAGENTS_HOME,
    }

    logger.info("[TA] Lancement analyse %s @ %s", ticker, analyse_date)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.TRADINGAGENTS_TIMEOUT,
            env=env,
            cwd=settings.TRADINGAGENTS_HOME,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"TradingAgents a dépassé le délai de {settings.TRADINGAGENTS_TIMEOUT}s"
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"Python TradingAgents introuvable : {settings.TRADINGAGENTS_PYTHON} "
            "(TradingAgents est-il installé dans /var/www/tradingagents ?)"
        )

    # Extraire le JSON encadré par les sentinelles (le reste = logs, ignoré)
    parts = proc.stdout.split(SENTINEL)
    if len(parts) < 3:
        extrait = (proc.stderr or proc.stdout or "")[-800:]
        raise RuntimeError(
            f"Sortie TradingAgents illisible (code {proc.returncode}). Détail : {extrait}"
        )

    try:
        return json.loads(parts[1].strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON TradingAgents invalide : {e}")


# ---------------------------------------------------------------------------
# 2. Synthèse française (Mistral)
# ---------------------------------------------------------------------------

def resumer_francais(titre, data: dict) -> str:
    """
    Transforme les rapports anglais de TradingAgents en une synthèse française
    accessible (débutant), niveaux de prix dans la devise du titre.
    Fallback : concatène les rapports bruts si Mistral indisponible.
    """
    from app.services.scoring_llm import _get_client

    sym = titre.symbole_devise
    note = data.get("note", "N/D")

    # On envoie la décision + le plan + les rapports clés (tronqués) au LLM
    blocs = []
    for cle, libelle in [
        ("final_decision", "Décision finale du gérant"),
        ("trader_plan", "Plan du trader"),
        ("fundamentals_report", "Fondamentaux"),
        ("market_report", "Analyse technique"),
        ("sentiment_report", "Sentiment social"),
        ("news_report", "Actualités / macro"),
    ]:
        contenu = (data.get(cle) or "").strip()
        if contenu:
            blocs.append(f"## {libelle}\n{contenu[:2500]}")
    rapports = "\n\n".join(blocs) if blocs else "(rapports indisponibles)"

    prompt = f"""Voici l'analyse multi-agents (en anglais) produite par l'outil TradingAgents pour {titre.nom or titre.ticker} ({titre.ticker}), devise de cotation {titre.devise} ({sym}).

NOTE GLOBALE DE L'OUTIL : {note} (échelle Buy / Overweight / Hold / Underweight / Sell)

{rapports}

Rédige une SYNTHÈSE EN FRANÇAIS pour un investisseur particulier DÉBUTANT (aucune connaissance technique) :
1. Commence par une phrase qui explique la note en mots simples (ex : "Underweight = l'outil suggère la prudence / un poids réduit").
2. 3 à 5 puces : les points clés (forces, risques, dynamique de prix, sentiment).
3. Donne les NIVEAUX DE PRIX dans la devise du titre ({sym}) quand ils apparaissent (support, résistance, objectif), jamais convertis.
4. Pas de jargon brut (traduis RSI/MACD en langage courant).
5. Termine EXACTEMENT par : "— Analyse générée par TradingAgents (outil de recherche). Ceci ne constitue pas un conseil d'investissement."
Réponds directement, sans titre ni préambule. Maximum ~250 mots."""

    try:
        client = _get_client()
        response = client.chat.complete(
            model=MODEL_SYNTHESE,
            max_tokens=700,
            temperature=0.2,
            messages=[
                {"role": "system", "content": (
                    "Tu traduis et synthétises en français des analyses boursières "
                    "pour un débutant. Tu donnes les niveaux de prix dans la devise du "
                    "titre, sans les convertir. Tu ne donnes jamais de conseil d'investissement."
                )},
                {"role": "user", "content": prompt},
            ],
        )
        texte = response.choices[0].message.content.strip()
        disclaimer = "Ceci ne constitue pas un conseil d'investissement."
        if disclaimer not in texte:
            texte += ("\n\n— Analyse générée par TradingAgents (outil de recherche). "
                      "Ceci ne constitue pas un conseil d'investissement.")
        return texte
    except Exception as e:
        logger.error("[TA] Synthèse FR échouée pour %s : %s", titre.ticker, e)
        return (f"Note TradingAgents : {note}\n\n{rapports}\n\n"
                "— Analyse générée par TradingAgents (outil de recherche). "
                "Ceci ne constitue pas un conseil d'investissement.")


# ---------------------------------------------------------------------------
# 3. Orchestration + persistance
# ---------------------------------------------------------------------------

def analyser_titre(ticker: str, analyse_date: str = None) -> dict:
    """
    Lance l'analyse TradingAgents pour un titre et persiste note + rapport.
    Le ticker est passé tel quel : TradingAgents s'appuie sur yfinance, qui
    accepte le même format que GestPEA (AAPL pour les US, MC.PA pour Euronext).
    """
    from datetime import date as date_cls
    from app.models import Titre

    titre = Titre.objects.get(ticker=ticker)
    if not analyse_date:
        analyse_date = date_cls.today().isoformat()

    try:
        data = lancer_analyse(titre.ticker, analyse_date)
    except Exception as e:
        logger.error("[TA] Analyse %s échouée : %s", ticker, e)
        titre.ta_statut = "erreur"
        titre.ta_rapport = f"Erreur lors de l'analyse approfondie : {e}"
        titre.ta_date_analyse = timezone.now()
        titre.save(update_fields=["ta_statut", "ta_rapport", "ta_date_analyse"])
        return {"ok": False, "error": str(e)}

    if not data.get("ok"):
        titre.ta_statut = "erreur"
        titre.ta_rapport = f"TradingAgents : {data.get('error', 'erreur inconnue')}"
        titre.ta_date_analyse = timezone.now()
        titre.save(update_fields=["ta_statut", "ta_rapport", "ta_date_analyse"])
        return data

    titre.ta_note = (data.get("note") or "")[:20]
    titre.ta_rapport = resumer_francais(titre, data)
    titre.ta_statut = "termine"
    titre.ta_date_analyse = timezone.now()
    titre.save(update_fields=["ta_note", "ta_rapport", "ta_statut", "ta_date_analyse"])
    logger.info("[TA] Analyse %s terminée : note=%s", ticker, titre.ta_note)
    return data
