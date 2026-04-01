"""
services/conviction.py
-----------------------
Score de conviction IA (Phase 2 — étape 34).

Calcule un score 0-100 pour chaque titre, combinant :
  - Technique (25%) : RSI, MACD, MMs, Bollinger
  - Fondamentaux (35%) : score_qualite du modèle Fondamentaux
  - Sentiment presse (20%) : dernier ScoreSentiment source='presse'
  - Historique alertes (20%) : fiabilité + signaux actifs

Mis à jour quotidiennement par tâche Celery.
Explication IA générée via Mistral (small).
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from app.models import (
    Titre, Fondamentaux, ScoreSentiment, Signal, Alerte,
)
from app.services.scoring_llm import calculer_sentiment_technique, _get_client

logger = logging.getLogger(__name__)

MODEL_CONVICTION = "mistral-small-latest"
MAX_TOKENS = 300


def _score_technique(ticker):
    """Composante technique (0-25 pts) à partir de calculer_sentiment_technique."""
    result = calculer_sentiment_technique(ticker)
    if not result:
        return None, {}
    # score est entre -1 et +1, normaliser vers 0-25
    score_norm = round((result['score'] + 1) / 2 * 25)
    return score_norm, {
        'score_brut': result['score'],
        'nb_signaux': result['nb_signaux'],
        'signaux': result['signaux'][:3],
    }


def _score_fondamentaux(ticker):
    """Composante fondamentaux (0-35 pts) à partir de score_qualite."""
    fonda = Fondamentaux.objects.filter(titre__ticker=ticker).order_by('-date_maj').first()
    if not fonda or fonda.score_qualite is None:
        return None, {}
    # score_qualite est 0-10, normaliser vers 0-35
    score_norm = round(float(fonda.score_qualite) / 10 * 35)
    return score_norm, {
        'score_qualite': float(fonda.score_qualite),
        'per': str(fonda.per) if fonda.per else 'N/A',
        'roe': str(fonda.roe) if fonda.roe else 'N/A',
        'consensus': fonda.consensus or 'N/A',
    }


def _score_sentiment(ticker):
    """Composante sentiment presse (0-20 pts)."""
    sentiment = ScoreSentiment.objects.filter(
        titre__ticker=ticker, source='presse'
    ).order_by('-date').first()
    if not sentiment:
        return None, {}
    # score est entre -1 et +1, normaliser vers 0-20
    score_norm = round((float(sentiment.score) + 1) / 2 * 20)
    return score_norm, {
        'score_brut': float(sentiment.score),
        'label': sentiment.label,
        'date': str(sentiment.date),
    }


def _score_historique(ticker):
    """Composante historique alertes (0-20 pts)."""
    # Fiabilité moyenne des alertes avec fiabilité connue
    alertes = Alerte.objects.filter(
        titre__ticker=ticker,
        fiabilite_historique__isnull=False,
    ).order_by('-date_detection')[:10]

    signaux_actifs = Signal.objects.filter(
        titre__ticker=ticker, actif=True
    ).count()

    if not alertes.exists() and signaux_actifs == 0:
        return None, {}

    # Fiabilité moyenne (0-100%) → 0-14 pts
    fiab_score = 0
    nb_alertes = 0
    if alertes.exists():
        fiab_values = [float(a.fiabilite_historique) for a in alertes if a.fiabilite_historique]
        if fiab_values:
            fiab_moyenne = sum(fiab_values) / len(fiab_values)
            fiab_score = round(fiab_moyenne / 100 * 14)
            nb_alertes = len(fiab_values)

    # Signaux actifs haussiers vs baissiers → 0-6 pts
    signaux_h = Signal.objects.filter(titre__ticker=ticker, actif=True, direction='haussier').count()
    signaux_b = Signal.objects.filter(titre__ticker=ticker, actif=True, direction='baissier').count()
    total_sig = signaux_h + signaux_b
    if total_sig > 0:
        ratio_haussier = signaux_h / total_sig
        sig_score = round(ratio_haussier * 6)
    else:
        sig_score = 3  # neutre

    score_norm = fiab_score + sig_score
    return min(score_norm, 20), {
        'fiabilite_moyenne': round(fiab_score / 14 * 100) if fiab_score else 0,
        'nb_alertes': nb_alertes,
        'signaux_haussiers': signaux_h,
        'signaux_baissiers': signaux_b,
    }


def _generer_explication(ticker, score_total, composantes):
    """Génère une explication IA du score en 2-3 phrases via Mistral."""
    prompt = f"""Score de conviction pour {ticker} : {score_total}/100.
Composantes : technique {composantes.get('technique', 'N/A')}/25, fondamentaux {composantes.get('fondamentaux', 'N/A')}/35, sentiment presse {composantes.get('sentiment', 'N/A')}/20, historique {composantes.get('historique', 'N/A')}/20.
Détails techniques : {composantes.get('details_technique', {})}
Détails fondamentaux : {composantes.get('details_fondamentaux', {})}
Détails sentiment : {composantes.get('details_sentiment', {})}

Rédige une explication concise de ce score en 2-3 phrases maximum. Ton professionnel.
Ne donne PAS de conseil d'investissement. Utilise des formulations comme "le titre montre", "les indicateurs suggèrent".
Réponds directement sans titre ni introduction."""

    try:
        client = _get_client()
        response = client.chat.complete(
            model=MODEL_CONVICTION,
            messages=[
                {"role": "system", "content": "Tu es un analyste financier qui rédige des explications concises de scores de conviction. Jamais de conseil d'investissement."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=MAX_TOKENS,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("Erreur génération explication conviction %s: %s", ticker, e)
        return _explication_fallback(ticker, score_total, composantes)


def _explication_fallback(ticker, score, composantes):
    """Explication de secours sans appel IA."""
    niveau = "élevé" if score >= 70 else "modéré" if score >= 40 else "faible"
    parts = []
    tech = composantes.get('technique')
    fonda = composantes.get('fondamentaux')
    sent = composantes.get('sentiment')
    if tech is not None:
        parts.append(f"technique {tech}/25")
    if fonda is not None:
        parts.append(f"fondamentaux {fonda}/35")
    if sent is not None:
        parts.append(f"sentiment {sent}/20")
    detail = ", ".join(parts) if parts else "données partielles"
    return f"Score de conviction {niveau} ({score}/100) pour {ticker}. Composantes : {detail}."


def calculer_score_conviction(ticker):
    """
    Calcule le score de conviction IA pour un titre.

    Returns:
        dict: {'score': int, 'explication': str, 'composantes': {...}} ou None si erreur.
    """
    try:
        titre = Titre.objects.get(ticker=ticker, actif=True)
    except Titre.DoesNotExist:
        logger.warning("Titre %s non trouvé pour calcul conviction", ticker)
        return None

    # Calculer les 4 composantes
    tech_score, tech_details = _score_technique(ticker)
    fonda_score, fonda_details = _score_fondamentaux(ticker)
    sent_score, sent_details = _score_sentiment(ticker)
    hist_score, hist_details = _score_historique(ticker)

    # Compter les composantes disponibles et calculer le score
    composantes_dispo = {}
    score_obtenu = 0
    max_possible = 0

    for nom, score, poids in [
        ('technique', tech_score, 25),
        ('fondamentaux', fonda_score, 35),
        ('sentiment', sent_score, 20),
        ('historique', hist_score, 20),
    ]:
        if score is not None:
            composantes_dispo[nom] = score
            score_obtenu += score
            max_possible += poids
        else:
            composantes_dispo[nom] = None

    # Si aucune composante, pas de score
    if max_possible == 0:
        logger.info("Aucune donnée pour calculer conviction de %s", ticker)
        return None

    # Normaliser si des composantes manquent (ramener à /100)
    score_total = round(score_obtenu / max_possible * 100)
    score_total = max(0, min(100, score_total))

    # Stocker les détails pour l'explication
    composantes_dispo['details_technique'] = tech_details
    composantes_dispo['details_fondamentaux'] = fonda_details
    composantes_dispo['details_sentiment'] = sent_details
    composantes_dispo['details_historique'] = hist_details

    # Générer l'explication IA
    explication = _generer_explication(ticker, score_total, composantes_dispo)

    # Sauvegarder
    titre.score_conviction = score_total
    titre.explication_conviction = explication
    titre.date_calcul_conviction = timezone.now()
    titre.save(update_fields=['score_conviction', 'explication_conviction', 'date_calcul_conviction'])

    logger.info("Conviction %s : %d/100 (tech=%s, fonda=%s, sent=%s, hist=%s)",
                ticker, score_total,
                tech_score, fonda_score, sent_score, hist_score)

    return {
        'score': score_total,
        'explication': explication,
        'composantes': composantes_dispo,
    }
