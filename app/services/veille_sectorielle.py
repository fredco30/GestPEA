"""
services/veille_sectorielle.py
-------------------------------
Étape 35 — Veille sectorielle via Google News RSS.

Surveille les secteurs des titres en portefeuille/surveillance :
  - Collecte d'articles par secteur via Google News RSS
  - Analyse IA de l'impact sectoriel (Mistral small)
  - Création d'alertes sectorielles si impact significatif

Gratuit et illimité (Google News RSS).
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from app.models import Titre, ArticleSectoriel, Signal

logger = logging.getLogger(__name__)

# Mots-clés de recherche Google News par secteur
SECTEUR_KEYWORDS = {
    'Healthcare':            'santé pharmaceutique biotechnologie bourse',
    'Biotechnology':         'biotechnologie essais cliniques bourse',
    'Drug Manufacturers':    'pharmaceutique médicaments bourse',
    'Energy':                'énergie pétrole gaz renouvelable bourse',
    'Consumer Cyclical':     'consommation luxe distribution bourse',
    'Technology':            'technologie semiconducteur logiciel bourse',
    'Financial Services':    'banque assurance finance bourse',
    'Industrials':           'industrie aéronautique défense bourse',
    'Basic Materials':       'matériaux chimie mines bourse',
    'Communication Services': 'télécoms médias bourse',
    'Utilities':             'services publics électricité bourse',
    'Real Estate':           'immobilier foncière bourse',
    'Consumer Defensive':    'agroalimentaire grande distribution bourse',
}


def collecter_news_sectorielles():
    """
    Collecte les articles Google News par secteur pour les titres suivis.

    Returns:
        dict: {secteur: nb_articles_collectés}
    """
    from app.services.rss_news import RSSCollector

    # Secteurs distincts des titres actifs
    secteurs = list(
        Titre.objects.filter(actif=True)
        .exclude(statut='archive')
        .exclude(secteur__isnull=True)
        .exclude(secteur='')
        .values_list('secteur', flat=True)
        .distinct()
    )

    if not secteurs:
        logger.info("[VeilleSector] Aucun secteur à surveiller")
        return {}

    collector = RSSCollector()
    resultats = {}

    for secteur in secteurs:
        try:
            nb = _collecter_secteur(collector, secteur)
            resultats[secteur] = nb
        except Exception as e:
            logger.error("[VeilleSector] Erreur %s : %s", secteur, e, exc_info=True)
            resultats[secteur] = 0

    total = sum(resultats.values())
    logger.info("[VeilleSector] %d articles collectés sur %d secteurs", total, len(secteurs))
    return resultats


def _collecter_secteur(collector, secteur):
    """Collecte les articles Google News pour un secteur donné."""
    import urllib.parse

    # Construire les mots-clés de recherche
    keywords = SECTEUR_KEYWORDS.get(secteur)
    if not keywords:
        # Fallback : utiliser le nom du secteur + "bourse"
        keywords = f"{secteur} bourse actions Europe"

    query = urllib.parse.quote(keywords)
    url = f"https://news.google.com/rss/search?q={query}+when:7d&hl=fr&gl=FR&ceid=FR:fr"

    articles = collector._fetch_rss(url)
    if not articles:
        return 0

    # URLs connues pour éviter les doublons
    urls_connues = set(
        ArticleSectoriel.objects.filter(secteur=secteur)
        .values_list('url', flat=True)
    )

    nb = 0
    for art in articles[:15]:  # Max 15 articles par secteur
        if art.get('url') in urls_connues:
            continue

        ArticleSectoriel.objects.create(
            secteur=secteur,
            date_pub=art.get('date') or timezone.now(),
            source='google_news',
            url=art.get('url', ''),
            titre_art=art.get('titre', '')[:300],
            extrait=art.get('description', '')[:500],
        )
        nb += 1

    return nb


def analyser_impact_sectoriel(article_ids=None):
    """
    Analyse l'impact sectoriel des articles via Mistral small.
    Score l'impact de -1 à +1 et génère une courte analyse.

    Si |impact| >= 0.4 : lie l'article aux titres du secteur.
    Si |impact| >= 0.6 : crée un Signal sectoriel + alerte potentielle.

    Args:
        article_ids: liste d'IDs d'articles à analyser. Si None, prend les non-analysés.

    Returns:
        int: nombre d'articles analysés.
    """
    from app.services.scoring_llm import _get_client

    if article_ids:
        qs = ArticleSectoriel.objects.filter(id__in=article_ids)
    else:
        qs = ArticleSectoriel.objects.filter(impact_secteur__isnull=True)

    articles = list(qs[:30])  # Max 30 par batch
    if not articles:
        return 0

    client = _get_client()
    nb_analyses = 0

    for article in articles:
        try:
            _analyser_article(client, article)
            nb_analyses += 1
        except Exception as e:
            logger.error("[VeilleSector] Erreur analyse article %d : %s",
                        article.id, e, exc_info=True)

    logger.info("[VeilleSector] %d articles analysés", nb_analyses)
    return nb_analyses


def _analyser_article(client, article):
    """Analyse un article sectoriel via Mistral small."""
    prompt = f"""Analyse cet article financier et détermine son impact sur le secteur "{article.secteur}".

TITRE : {article.titre_art}
EXTRAIT : {article.extrait[:400]}

Réponds en JSON strict (pas de markdown, pas de ```json) :
{{
  "impact": 0.0,
  "type_impact": "regulation|concurrence|macro|resultats_secteur|innovation|restructuration|autre",
  "analyse": "2-3 phrases en français simple expliquant l'impact sur les actions du secteur"
}}

Règles pour le score impact :
- 0.0 = aucun impact sur les actions du secteur
- +0.3 à +0.6 = impact modérément positif
- +0.7 à +1.0 = impact très positif
- -0.3 à -0.6 = impact modérément négatif
- -0.7 à -1.0 = impact très négatif
"""

    import json
    response = client.chat.complete(
        model="mistral-small-latest",
        max_tokens=300,
        messages=[
            {"role": "system", "content": "Tu analyses l'impact d'actualités financières sur les secteurs boursiers. Réponds uniquement en JSON valide."},
            {"role": "user", "content": prompt},
        ],
    )

    texte = response.choices[0].message.content.strip()
    # Nettoyer les éventuels blocs markdown
    if texte.startswith('```'):
        texte = texte.split('\n', 1)[1].rsplit('```', 1)[0].strip()

    try:
        data = json.loads(texte)
    except json.JSONDecodeError:
        logger.warning("[VeilleSector] JSON invalide pour article %d", article.id)
        article.impact_secteur = Decimal('0')
        article.analyse_ia = "Analyse non disponible."
        article.save(update_fields=['impact_secteur', 'analyse_ia'])
        return

    impact = float(data.get('impact', 0))
    type_impact = data.get('type_impact', 'autre')
    analyse = data.get('analyse', '')

    article.impact_secteur = Decimal(str(round(impact, 3)))
    article.type_impact = type_impact if type_impact in dict(ArticleSectoriel.TYPE_IMPACT_CHOICES) else 'autre'
    article.analyse_ia = analyse[:500]
    article.save(update_fields=['impact_secteur', 'type_impact', 'analyse_ia'])

    # Si impact significatif, lier aux titres du secteur
    if abs(impact) >= 0.4:
        titres_secteur = Titre.objects.filter(
            secteur=article.secteur, actif=True
        ).exclude(statut='archive')
        article.titres_impactes.set(titres_secteur)

    # Si impact fort, créer un Signal sectoriel
    if abs(impact) >= 0.6:
        _creer_signaux_sectoriels(article, impact)


def _creer_signaux_sectoriels(article, impact):
    """Crée des signaux sectoriels pour chaque titre du secteur."""
    from datetime import date

    titres = article.titres_impactes.all()
    direction = 'haussier' if impact > 0 else 'baissier'
    aujourd_hui = date.today()

    for titre in titres:
        # Anti-doublon : max 1 signal sectoriel par titre par jour
        Signal.objects.get_or_create(
            titre=titre,
            date=aujourd_hui,
            type_signal='sectorielle',
            defaults={
                'direction': direction,
                'valeur': Decimal(str(abs(round(impact, 3)))),
                'description': f"[{article.secteur}] {article.titre_art[:150]}",
                'actif': True,
            },
        )
