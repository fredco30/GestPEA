"""
app/services/auto_fill.py
--------------------------
Service d'auto-remplissage des métadonnées d'un titre.
Saisir un ticker, un ISIN ou un nom d'entreprise → l'IA résout le ticker
et complète tout automatiquement :
  - nom, nom_court, place, pays, secteur, sous_secteur, isin
  - éligibilité PEA
  - seuils d'alerte adaptés au secteur

Sources : EODHD (fondamentaux/General + search) + FMP (profil).

Usage :
    from app.services.auto_fill import resoudre_ticker, auto_remplir_titre
    ticker = resoudre_ticker('FR0010557264')  # → 'AB.PA'
    ticker = resoudre_ticker('AB Science')     # → 'AB.PA'
    titre  = auto_remplir_titre('AB.PA')
"""

import logging
import re
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


# Regex pour détecter un ISIN (2 lettres + 10 alphanumériques)
ISIN_PATTERN = re.compile(r'^[A-Z]{2}[A-Z0-9]{10}$')

# Exchanges européens prioritaires pour le PEA
EXCHANGES_PEA_PRIORITE = ['PA', 'AS', 'BR', 'MI', 'MC', 'XETRA', 'LSE']


def resoudre_ticker(saisie: str) -> str:
    """
    Résout une saisie libre en ticker EODHD.
    Accepte :
      - un ticker direct : 'MC.PA' → 'MC.PA'
      - un ISIN : 'FR0010557264' → 'AB.PA'
      - un nom d'entreprise : 'AB Science' → 'AB.PA'
      - un ISIN + code : 'FR0010557264 AB' → 'AB.PA'

    Coût quota : 0-1 requête EODHD (search).
    Retourne le ticker EODHD ou la saisie originale si non résolu.
    """
    saisie = saisie.strip().upper()

    if not saisie:
        return saisie

    # --- Cas 1 : déjà un ticker valide (CODE.EXCHANGE) ---
    if '.' in saisie and len(saisie.split('.')) == 2:
        code, exchange = saisie.split('.')
        if code.isalpha() and len(exchange) <= 6:
            return saisie

    # --- Extraire ISIN et/ou code de la saisie ---
    parties = saisie.split()
    isin_trouve = None
    code_trouve = None

    for partie in parties:
        if ISIN_PATTERN.match(partie):
            isin_trouve = partie
        elif partie.isalpha() and 1 <= len(partie) <= 10:
            code_trouve = partie

    # --- Cas 2 : ISIN + code fournis → construire le ticker directement ---
    if isin_trouve and code_trouve:
        # Deviner l'exchange depuis le préfixe ISIN
        exchange = _exchange_depuis_isin(isin_trouve)
        if exchange:
            ticker_candidat = f"{code_trouve}.{exchange}"
            logger.info("Résolution ISIN+code : %s → %s", saisie, ticker_candidat)
            return ticker_candidat

    # --- Cas 3 : recherche EODHD ---
    from app.services.eodhd import EODHDClient, EODHDError

    # Terme de recherche : le code, le nom, ou l'ISIN
    query = code_trouve or saisie.replace(isin_trouve or '', '').strip() or isin_trouve or saisie

    try:
        client = EODHDClient()
        resultats = client.recherche_ticker(query)

        if not resultats:
            logger.warning("Résolution ticker : aucun résultat pour '%s'", query)
            # Si on a un ISIN + code, tenter PA par défaut
            if isin_trouve and code_trouve:
                return f"{code_trouve}.PA"
            return saisie

        # Filtrer par ISIN si fourni
        if isin_trouve:
            match_isin = [r for r in resultats if r.get('ISIN') == isin_trouve]
            if match_isin:
                resultats = match_isin

        # Privilégier les exchanges européens PEA
        meilleur = _choisir_meilleur_resultat(resultats)

        if meilleur:
            ticker = f"{meilleur['Code']}.{meilleur['Exchange']}"
            logger.info("Résolution ticker : '%s' → %s (%s)",
                        saisie, ticker, meilleur.get('Name', ''))
            return ticker

    except EODHDError as e:
        logger.error("Résolution ticker EODHD : %s", e)

    # Fallback : si on a un code, essayer .PA (Euronext Paris)
    if code_trouve:
        return f"{code_trouve}.PA"

    return saisie


def _extraire_nom_court(nom_complet: str, ticker: str) -> str:
    """
    Extrait un nom commercial court depuis le nom complet EODHD.
    Ex: "L'Air Liquide S.A." → "Air Liquide"
        "LVMH Moët Hennessy Louis Vuitton SE" → "LVMH"
        "AB Science S.A." → "AB Science"
        "TotalEnergies SE" → "TotalEnergies"
    """
    if not nom_complet:
        return ticker.split(".")[0]

    # Supprimer les suffixes juridiques courants
    suffixes = [
        ' S.A.', ' SA', ' SE', ' S.E.', ' N.V.', ' NV', ' PLC', ' AG',
        ' S.p.A.', ' SpA', ' S.A', ' Inc.', ' Inc', ' Corp.', ' Corp',
        ' Ltd.', ' Ltd', ' SCA', ' S.C.A.', ' S.A.S.',
    ]
    nom = nom_complet.strip()
    for suf in suffixes:
        if nom.upper().endswith(suf.upper()):
            nom = nom[:len(nom) - len(suf)].strip()
            break

    # Supprimer le préfixe "L'" si le reste est assez long
    if nom.startswith("L'") and len(nom) > 5:
        nom = nom[2:]

    # Si le nom est trop long (>20 chars), prendre le premier mot significatif
    if len(nom) > 20:
        # Cas spéciaux : acronymes au début (LVMH, BNP, etc.)
        premier_mot = nom.split()[0] if nom.split() else nom
        if premier_mot.isupper() and len(premier_mot) >= 2:
            return premier_mot
        # Sinon garder les 2 premiers mots
        mots = nom.split()[:2]
        return ' '.join(mots)

    return nom


def _exchange_depuis_isin(isin: str) -> str:
    """Déduit l'exchange EODHD probable depuis le préfixe pays de l'ISIN."""
    prefixe_to_exchange = {
        'FR': 'PA',     # France → Euronext Paris
        'NL': 'AS',     # Pays-Bas → Euronext Amsterdam
        'BE': 'BR',     # Belgique → Euronext Bruxelles
        'DE': 'XETRA',  # Allemagne → Xetra
        'IT': 'MI',     # Italie → Milan
        'ES': 'MC',     # Espagne → Madrid
        'PT': 'LS',     # Portugal → Lisbonne
        'IE': 'IR',     # Irlande
        'FI': 'HE',     # Finlande → Helsinki
        'AT': 'VI',     # Autriche → Vienne
        'DK': 'CO',     # Danemark → Copenhague
        'SE': 'ST',     # Suède → Stockholm
        'NO': 'OL',     # Norvège → Oslo
    }
    return prefixe_to_exchange.get(isin[:2], '')


def _choisir_meilleur_resultat(resultats: list[dict]) -> dict | None:
    """
    Parmi les résultats EODHD search, choisit le meilleur :
    priorité aux exchanges européens PEA.
    """
    if not resultats:
        return None

    # Trier : exchanges PEA en premier
    def score(r):
        ex = r.get('Exchange', '')
        if ex in EXCHANGES_PEA_PRIORITE:
            return EXCHANGES_PEA_PRIORITE.index(ex)
        return 99

    resultats_tries = sorted(resultats, key=score)
    return resultats_tries[0]


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

        # nom_court : extraire un nom commercial court depuis le nom complet
        # Ex: "L'Air Liquide S.A." → "Air Liquide", "LVMH Moët Hennessy..." → "LVMH"
        nom_complet = general.get("Name", "")
        metadata['nom_court'] = _extraire_nom_court(nom_complet, ticker)

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
                metadata['nom_court'] = _extraire_nom_court(
                    profil.get("companyName", ""), ticker
                )
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
