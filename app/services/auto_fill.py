"""
app/services/auto_fill.py
--------------------------
Service d'auto-remplissage des métadonnées d'un titre.
Saisir uniquement le ticker → l'IA complète tout automatiquement :
  - nom, nom_court, place, pays, secteur, sous_secteur, isin
  - éligibilité PEA
  - seuils d'alerte adaptés au secteur

Sources : EODHD (fondamentaux/General) + FMP (profil).

Usage :
    from app.services.auto_fill import auto_remplir_titre
    titre = auto_remplir_titre('MC.PA')
"""

import logging
from datetime import date
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

# Codes ISO-3 des pays éligibles PEA (UE/EEE)
PAYS_ELIGIBLES_PEA = {
    "FRA", "DEU", "NLD", "BEL", "ESP", "ITA", "PRT", "FIN", "IRL",
    "LUX", "AUT", "GRC", "DNK", "SWE", "POL", "CZE", "HUN", "ROU",
    "SVK", "SVN", "EST", "LVA", "LTU", "BGR", "HRV", "CYP", "MLT",
    "ISL", "LIE", "NOR",
}

# Seuils d'alerte par défaut selon le type de secteur
SEUILS_PAR_SECTEUR = {
    # Secteurs défensifs : seuils plus serrés (moins volatils)
    'Consumer Defensive': {'score_min': 4.5, 'seuil_drawdown': 8.0},
    'Utilities':          {'score_min': 4.5, 'seuil_drawdown': 8.0},
    'Healthcare':         {'score_min': 5.0, 'seuil_drawdown': 10.0},
    # Secteurs cycliques : seuils plus larges
    'Technology':         {'score_min': 5.5, 'seuil_drawdown': 15.0},
    'Financial Services': {'score_min': 5.0, 'seuil_drawdown': 12.0},
    'Consumer Cyclical':  {'score_min': 5.0, 'seuil_drawdown': 12.0},
    'Industrials':        {'score_min': 5.0, 'seuil_drawdown': 12.0},
    # Secteurs volatils
    'Energy':             {'score_min': 5.5, 'seuil_drawdown': 15.0},
    'Basic Materials':    {'score_min': 5.5, 'seuil_drawdown': 15.0},
    'Communication Services': {'score_min': 5.0, 'seuil_drawdown': 12.0},
    'Real Estate':        {'score_min': 5.0, 'seuil_drawdown': 10.0},
}

# Défaut si secteur inconnu
SEUILS_DEFAUT = {'score_min': 5.0, 'seuil_drawdown': 12.0}


def auto_remplir_titre(ticker: str) -> dict:
    """
    Récupère automatiquement les métadonnées d'un titre via EODHD.
    Retourne un dict avec les champs à remplir sur le modèle Titre.

    Coût quota : 1 requête EODHD (fondamentaux/General).
    """
    from app.services.eodhd import EODHDClient, EODHDError

    client = EODHDClient()
    metadata = {}

    try:
        raw = client.get_fondamentaux(ticker)
        general = raw.get("General", {}) or {}

        if not general:
            logger.warning("Auto-fill %s : pas de données General EODHD", ticker)
            return metadata

        # Mapping EODHD → champs Titre
        metadata['nom'] = general.get("Name", "")
        metadata['nom_court'] = general.get("Code", ticker.split(".")[0])
        metadata['place'] = general.get("Exchange", "")
        metadata['isin'] = general.get("ISIN", "")
        metadata['secteur'] = general.get("Sector", "")
        metadata['sous_secteur'] = general.get("Industry", "")

        # Pays (ISO-3)
        pays = (general.get("CountryISO") or "").upper()
        metadata['pays'] = pays

        # Éligibilité PEA
        metadata['eligible_pea'] = pays in PAYS_ELIGIBLES_PEA
        metadata['date_verif_eligibilite'] = date.today()

        logger.info(
            "Auto-fill %s : nom=%s, place=%s, pays=%s, secteur=%s, eligible_pea=%s",
            ticker, metadata['nom'][:40], metadata['place'],
            metadata['pays'], metadata['secteur'], metadata['eligible_pea']
        )

    except EODHDError as e:
        logger.error("Auto-fill EODHD %s : %s", ticker, e)

    # Fallback FMP si EODHD n'a pas retourné de données
    if not metadata.get('nom') and settings.FMP_API_KEY:
        try:
            from app.services.fmp import FMPClient, FMPError
            fmp = FMPClient()
            profil = fmp.get_profil(ticker)
            if profil:
                metadata['nom'] = profil.get("companyName", "")
                metadata['nom_court'] = profil.get("symbol", ticker.split(".")[0])
                metadata['place'] = profil.get("exchangeShortName", "")
                metadata['isin'] = profil.get("isin", "")
                metadata['secteur'] = profil.get("sector", "")
                metadata['sous_secteur'] = profil.get("industry", "")
                pays = (profil.get("country") or "")[:3].upper()
                metadata['pays'] = pays
                metadata['eligible_pea'] = pays in PAYS_ELIGIBLES_PEA
                metadata['date_verif_eligibilite'] = date.today()
                logger.info("Auto-fill FMP %s : nom=%s", ticker, metadata['nom'][:40])
        except Exception as e:
            logger.error("Auto-fill FMP %s : %s", ticker, e)

    # Nettoyer les valeurs vides
    metadata = {k: v for k, v in metadata.items() if v not in (None, "")}

    return metadata


def seuils_alerte_pour_secteur(secteur: str) -> dict:
    """
    Retourne les seuils d'alerte adaptés au secteur du titre.
    """
    return SEUILS_PAR_SECTEUR.get(secteur, SEUILS_DEFAUT)
