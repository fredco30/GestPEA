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
PAYS_ELIGIBLES_PEA_ISO3 = {
    "FRA", "DEU", "NLD", "BEL", "ESP", "ITA", "PRT", "FIN", "IRL",
    "LUX", "AUT", "GRC", "DNK", "SWE", "POL", "CZE", "HUN", "ROU",
    "SVK", "SVN", "EST", "LVA", "LTU", "BGR", "HRV", "CYP", "MLT",
    "ISL", "LIE", "NOR",
}

# Codes ISO-2 des mêmes pays (EODHD retourne parfois ISO-2)
PAYS_ELIGIBLES_PEA_ISO2 = {
    "FR", "DE", "NL", "BE", "ES", "IT", "PT", "FI", "IE",
    "LU", "AT", "GR", "DK", "SE", "PL", "CZ", "HU", "RO",
    "SK", "SI", "EE", "LV", "LT", "BG", "HR", "CY", "MT",
    "IS", "LI", "NO",
}

# Mapping ISO-2 → ISO-3 pour normaliser
ISO2_TO_ISO3 = {
    "FR": "FRA", "DE": "DEU", "NL": "NLD", "BE": "BEL", "ES": "ESP",
    "IT": "ITA", "PT": "PRT", "FI": "FIN", "IE": "IRL", "LU": "LUX",
    "AT": "AUT", "GR": "GRC", "DK": "DNK", "SE": "SWE", "PL": "POL",
    "CZ": "CZE", "HU": "HUN", "RO": "ROU", "SK": "SVK", "SI": "SVN",
    "EE": "EST", "LV": "LVA", "LT": "LTU", "BG": "BGR", "HR": "HRV",
    "CY": "CYP", "MT": "MLT", "IS": "ISL", "LI": "LIE", "NO": "NOR",
    "GB": "GBR", "US": "USA", "CH": "CHE", "JP": "JPN", "CN": "CHN",
}


def _normaliser_pays(code: str) -> str:
    """Normalise un code pays en ISO-3. Accepte ISO-2 ou ISO-3."""
    code = code.upper().strip()
    if len(code) == 2:
        return ISO2_TO_ISO3.get(code, code)
    return code


def _est_eligible_pea(pays_iso3: str) -> bool:
    """Vérifie si un pays ISO-3 est éligible PEA."""
    return pays_iso3 in PAYS_ELIGIBLES_PEA_ISO3

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

        # nom_court : Code EODHD sans le suffixe exchange (ex: "AI" pas "AI.PA")
        code = general.get("Code", "")
        metadata['nom_court'] = code.split(".")[0] if code else ticker.split(".")[0]

        # Place : Exchange ou déduit du ticker (ex: MC.PA → PA)
        exchange = general.get("Exchange", "")
        if not exchange and "." in ticker:
            exchange = ticker.split(".")[-1]
        metadata['place'] = exchange

        metadata['isin'] = general.get("ISIN", "")
        metadata['secteur'] = general.get("Sector", "")
        metadata['sous_secteur'] = general.get("Industry", "")

        # Pays : normaliser ISO-2 → ISO-3
        pays_brut = (general.get("CountryISO") or general.get("Country", "")).upper()
        pays = _normaliser_pays(pays_brut)
        metadata['pays'] = pays

        # Éligibilité PEA
        metadata['eligible_pea'] = _est_eligible_pea(pays)
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
                sym = profil.get("symbol", ticker.split(".")[0])
                metadata['nom_court'] = sym.split(".")[0]
                exchange = profil.get("exchangeShortName", "")
                if not exchange and "." in ticker:
                    exchange = ticker.split(".")[-1]
                metadata['place'] = exchange
                metadata['isin'] = profil.get("isin", "")
                metadata['secteur'] = profil.get("sector", "")
                metadata['sous_secteur'] = profil.get("industry", "")
                pays_brut = (profil.get("country") or "")[:3].upper()
                pays = _normaliser_pays(pays_brut)
                metadata['pays'] = pays
                metadata['eligible_pea'] = _est_eligible_pea(pays)
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
