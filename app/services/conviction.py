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
MAX_TOKENS = 500

# Pondération centralisée du score de conviction (somme = 100).
# Facile à ajuster ici sans toucher au reste du code.
POIDS_CONVICTION = {
    'technique':    20,
    'fondamentaux': 30,
    'sentiment':    20,
    'documents':    15,   # impact des documents uploadés (signal propre, hors presse)
    'historique':   15,
}


def _score_technique(ticker):
    """Composante technique à partir de calculer_sentiment_technique."""
    result = calculer_sentiment_technique(ticker)
    if not result:
        return None, {}
    # score est entre -1 et +1, normaliser vers le poids de la composante
    score_norm = round((result['score'] + 1) / 2 * POIDS_CONVICTION['technique'])
    return score_norm, {
        'score_brut': result['score'],
        'nb_signaux': result['nb_signaux'],
        'signaux': result['signaux'][:3],
    }


def _score_fondamentaux(ticker):
    """Composante fondamentaux à partir de score_qualite."""
    fonda = Fondamentaux.objects.filter(titre__ticker=ticker).order_by('-date_maj').first()
    if not fonda or fonda.score_qualite is None:
        return None, {}
    # score_qualite est 0-10, normaliser vers le poids de la composante
    score_norm = round(float(fonda.score_qualite) / 10 * POIDS_CONVICTION['fondamentaux'])
    return score_norm, {
        'score_qualite': float(fonda.score_qualite),
        'per': str(fonda.per) if fonda.per else 'N/A',
        'roe': str(fonda.roe) if fonda.roe else 'N/A',
        'consensus': fonda.consensus or 'N/A',
    }


def _score_sentiment(ticker):
    """Composante sentiment presse."""
    sentiment = ScoreSentiment.objects.filter(
        titre__ticker=ticker, source='presse'
    ).order_by('-date').first()
    if not sentiment:
        return None, {}
    # score est entre -1 et +1, normaliser vers le poids de la composante
    score_norm = round((float(sentiment.score) + 1) / 2 * POIDS_CONVICTION['sentiment'])
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

    # Score interne 0-20 (fiab 0-14 + signaux 0-6) → rescalé sur le poids de la composante
    score_interne = min(fiab_score + sig_score, 20)
    score_norm = round(score_interne / 20 * POIDS_CONVICTION['historique'])
    return score_norm, {
        'fiabilite_moyenne': round(fiab_score / 14 * 100) if fiab_score else 0,
        'nb_alertes': nb_alertes,
        'signaux_haussiers': signaux_h,
        'signaux_baissiers': signaux_b,
    }


def _score_documents(ticker):
    """
    Composante DOCUMENTS : impact des documents uploadés, noté par Mistral (-1 à +1).
    Calculé en cache sur le Titre (score_documents) ; recalculé seulement si un
    document a changé depuis la dernière évaluation (pour ne pas re-appeler le LLM
    à chaque conviction).
    """
    from app.models import Titre, DocumentTitre

    try:
        titre = Titre.objects.get(ticker=ticker)
    except Titre.DoesNotExist:
        return None, {}

    dernier_doc = (
        DocumentTitre.objects.filter(titre=titre)
        .order_by('-date_upload')
        .values_list('date_upload', flat=True)
        .first()
    )
    if not dernier_doc:
        return None, {}  # aucun document → composante absente

    # (Re)calcul si pas encore noté, ou si un document est plus récent que la dernière éval
    if titre.score_documents is None or titre.date_score_documents is None \
            or dernier_doc > titre.date_score_documents:
        try:
            from app.services.scoring_llm import evaluer_impact_documents
            evaluer_impact_documents(ticker)
            titre.refresh_from_db(fields=['score_documents', 'analyse_documents_ia', 'date_score_documents'])
        except Exception as e:
            logger.error("Évaluation documents %s échouée : %s", ticker, e)

    if titre.score_documents is None:
        return None, {}

    # -1..+1 → 0..poids
    score_norm = round((float(titre.score_documents) + 1) / 2 * POIDS_CONVICTION['documents'])
    return score_norm, {
        'score_brut': float(titre.score_documents),
        'nb_documents': DocumentTitre.objects.filter(titre=titre).count(),
        'analyse': titre.analyse_documents_ia,
    }


def _get_niveaux_prix(ticker):
    """Récupère les niveaux de prix clés pour un titre, dans sa devise de cotation."""
    from app.models import PrixJournalier, Fondamentaux
    from app.services.devises import symbole_pour_ticker
    bougie = PrixJournalier.objects.filter(titre__ticker=ticker).order_by('-date').first()
    fond = Fondamentaux.objects.filter(titre__ticker=ticker).order_by('-date_maj').first()
    if not bougie:
        return ""
    sym = symbole_pour_ticker(ticker)
    lines = [f"Cours actuel : {bougie.cloture} {sym}"]
    if bougie.mm_20:
        lines.append(f"Moyenne 20 jours (tendance court terme) : {bougie.mm_20} {sym}")
    if bougie.mm_50:
        lines.append(f"Moyenne 50 jours (tendance moyen terme) : {bougie.mm_50} {sym}")
    if bougie.mm_200:
        lines.append(f"Moyenne 200 jours (support long terme) : {bougie.mm_200} {sym}")
    if bougie.boll_inf:
        lines.append(f"Plancher technique (Bollinger bas) : {bougie.boll_inf} {sym}")
    if bougie.boll_sup:
        lines.append(f"Plafond technique (Bollinger haut) : {bougie.boll_sup} {sym}")
    if fond and fond.objectif_cours_moyen:
        lines.append(f"Objectif moyen des analystes : {fond.objectif_cours_moyen} {sym}")
    return "\n".join(lines)


def _generer_explication(ticker, score_total, composantes):
    """Génère une explication IA du score en 2-3 phrases via Mistral."""
    from app.services.devises import symbole_pour_ticker
    niveaux = _get_niveaux_prix(ticker)
    sym = symbole_pour_ticker(ticker)

    libelles = {'technique': 'technique', 'fondamentaux': 'fondamentaux',
                'sentiment': 'sentiment presse', 'documents': 'documents',
                'historique': 'historique'}
    comp_txt = ", ".join(
        f"{libelles[nom]} {composantes.get(nom)}/{poids}"
        for nom, poids in POIDS_CONVICTION.items()
        if composantes.get(nom) is not None
    ) or "données partielles"

    # Contexte documents : l'analyse Mistral de tes PDF (signal hors-presse)
    doc_ctx = ""
    analyse_doc = (composantes.get('details_documents') or {}).get('analyse')
    if analyse_doc:
        doc_ctx = f"\nDOCUMENTS DÉPOSÉS PAR L'UTILISATEUR (rapports/études, hors presse) — impact :\n{analyse_doc}\n"

    prompt = f"""Score de conviction pour {ticker} : {score_total}/100.
Composantes : {comp_txt}.
{doc_ctx}
NIVEAUX DE PRIX (devise du titre : {sym}) :
{niveaux}

CONTEXTE : L'utilisateur est un investisseur long terme qui cherche les meilleurs points d'entrée.

Rédige une analyse concise en 3-4 phrases, en langage accessible :

1. SITUATION ACTUELLE : Position du cours par rapport à la moyenne 20 jours (dynamique court terme) et 50 jours (moyen terme). Indique si la tendance CT est favorable ou non.

2. POINTS D'ENTRÉE : Identifie les meilleurs niveaux de prix pour entrer ou renforcer :
   - Si le cours est proche de la MM20 en tendance haussière → signaler le pullback comme zone d'entrée
   - Si le cours est sous la MM20 → indiquer la MM20 comme résistance à reconquérir
   - Indiquer les supports concrets dans la devise du titre ({sym}) (MM50, Bollinger bas, plus bas récents)
   - Exemple : "Une zone d'entrée intéressante se situerait entre XX {sym} (MM20) et XX {sym} (support)"

3. RÉSISTANCES : Indiquer les niveaux à surveiller au-dessus (objectif analystes, plafond technique).

Tous les niveaux doivent être dans la devise de cotation du titre ({sym}), jamais convertis. Termine par un disclaimer : *Cette analyse ne constitue pas un conseil d'investissement.*
Réponds directement sans titre ni introduction."""

    try:
        client = _get_client()
        response = client.chat.complete(
            model=MODEL_CONVICTION,
            messages=[
                {"role": "system", "content": "Tu es un analyste financier expert en analyse technique. Tu aides un investisseur long terme à identifier les meilleurs points d'entrée en utilisant les moyennes mobiles (MM20, MM50, MM200) et les niveaux de support/résistance. Tu donnes toujours des niveaux de prix concrets dans la devise de cotation du titre (€, $, £…), jamais convertis."},
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
    parts = [
        f"{nom} {composantes.get(nom)}/{poids}"
        for nom, poids in POIDS_CONVICTION.items()
        if composantes.get(nom) is not None
    ]
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

    # Calculer les composantes
    tech_score, tech_details = _score_technique(ticker)
    fonda_score, fonda_details = _score_fondamentaux(ticker)
    sent_score, sent_details = _score_sentiment(ticker)
    doc_score, doc_details = _score_documents(ticker)
    hist_score, hist_details = _score_historique(ticker)

    # Compter les composantes disponibles et calculer le score (poids centralisés)
    composantes_dispo = {}
    score_obtenu = 0
    max_possible = 0

    for nom, score in [
        ('technique', tech_score),
        ('fondamentaux', fonda_score),
        ('sentiment', sent_score),
        ('documents', doc_score),
        ('historique', hist_score),
    ]:
        if score is not None:
            composantes_dispo[nom] = score
            score_obtenu += score
            max_possible += POIDS_CONVICTION[nom]
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
    composantes_dispo['details_documents'] = doc_details
    composantes_dispo['details_historique'] = hist_details

    # Générer l'explication IA
    explication = _generer_explication(ticker, score_total, composantes_dispo)

    # Sauvegarder
    titre.score_conviction = score_total
    titre.explication_conviction = explication
    titre.date_calcul_conviction = timezone.now()
    titre.save(update_fields=['score_conviction', 'explication_conviction', 'date_calcul_conviction'])

    logger.info("Conviction %s : %d/100 (tech=%s, fonda=%s, sent=%s, docs=%s, hist=%s)",
                ticker, score_total,
                tech_score, fonda_score, sent_score, doc_score, hist_score)

    return {
        'score': score_total,
        'explication': explication,
        'composantes': composantes_dispo,
    }
