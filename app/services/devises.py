"""
app/services/devises.py
------------------------
Gestion des devises pour un portefeuille multinational (PEA en EUR + CTO en USD…).

Deux responsabilités :
  1. Symboles d'affichage (€, $, £…) — utilisés par le front ET les prompts LLM.
  2. Conversion vers l'EUR pour agréger un portefeuille multi-devises.

La conversion s'appuie sur le modèle TauxChange (rafraîchi via EODHD Forex).
En l'absence de taux en base, un fallback configurable évite un total faux à 0 :
on convertit avec une valeur approximative (loguée) plutôt que d'ignorer la position.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

# Symboles d'affichage par code devise
SYMBOLES = {
    'EUR': '€', 'USD': '$', 'GBP': '£', 'GBX': 'p',
    'CHF': 'CHF', 'JPY': '¥', 'CAD': 'C$', 'AUD': 'A$',
    'SEK': 'kr', 'NOK': 'kr', 'DKK': 'kr', 'HKD': 'HK$',
}

# Fallback approximatif (1 unité de devise = X EUR) si aucun taux en base.
# Sert uniquement de filet de sécurité ; la tâche Celery quotidienne fournit la vraie valeur.
# GBX = pence britannique = 1/100 GBP.
TAUX_FALLBACK = {
    'USD': Decimal('0.92'),
    'GBP': Decimal('1.17'),
    'GBX': Decimal('0.0117'),
    'CHF': Decimal('1.04'),
    'CAD': Decimal('0.67'),
    'JPY': Decimal('0.0060'),
    'SEK': Decimal('0.088'),
    'NOK': Decimal('0.086'),
    'DKK': Decimal('0.134'),
    'HKD': Decimal('0.118'),
    'AUD': Decimal('0.60'),
}

# Au-delà de ce nombre de jours, un taux en base est jugé périmé → on tente le fallback.
FRAICHEUR_MAX_JOURS = 10


def symbole(devise: str) -> str:
    """Symbole monétaire d'affichage pour une devise (défaut € si vide/inconnue)."""
    if not devise:
        return '€'
    return SYMBOLES.get(devise.upper(), devise.upper())


def symbole_pour_ticker(ticker: str) -> str:
    """Symbole de la devise d'un titre, par son ticker (1 requête DB légère)."""
    from app.models import Titre
    dev = Titre.objects.filter(ticker=ticker).values_list('devise', flat=True).first()
    return symbole(dev or 'EUR')


def get_taux_vers_eur(devise: str) -> Decimal:
    """
    Renvoie le taux : 1 unité de `devise` = X EUR.
    EUR → 1. Sinon dernier TauxChange frais en base, puis fallback, puis 1 (loggé).
    """
    if not devise or devise.upper() == 'EUR':
        return Decimal('1')

    devise = devise.upper()
    from app.models import TauxChange

    tc = TauxChange.objects.filter(devise=devise).order_by('-date').first()
    if tc and tc.taux_vers_eur:
        if tc.date >= date.today() - timedelta(days=FRAICHEUR_MAX_JOURS):
            return tc.taux_vers_eur
        logger.warning("Taux %s périmé (%s) — utilisation quand même faute de mieux.", devise, tc.date)
        return tc.taux_vers_eur

    if devise in TAUX_FALLBACK:
        logger.warning("Aucun taux %s en base — fallback approximatif %s EUR.", devise, TAUX_FALLBACK[devise])
        return TAUX_FALLBACK[devise]

    logger.warning("Aucun taux ni fallback pour %s — conversion neutre (1:1), total possiblement faux.", devise)
    return Decimal('1')


def convertir_en_eur(montant, devise: str):
    """Convertit un montant exprimé en `devise` vers l'EUR. None reste None."""
    if montant is None:
        return None
    return Decimal(str(montant)) * get_taux_vers_eur(devise)


def rafraichir_taux_change(devises=None) -> dict:
    """
    Récupère les taux de change vers l'EUR via EODHD Forex et les persiste en base.

    Si `devises` est None, on rafraîchit uniquement les devises réellement présentes
    en portefeuille/surveillance (hors EUR) pour économiser le quota EODHD.

    EODHD : la paire EUR{BASE}.FOREX donne « combien de BASE pour 1 EUR ».
    Donc taux_vers_eur(BASE) = 1 / close. (GBX = pence → /100.)

    Returns : {'maj': n, 'erreurs': n, 'devises': [...]}.
    """
    from app.models import Titre, TauxChange
    from app.services.eodhd import EODHDClient, EODHDError

    if devises is None:
        devises = set(
            Titre.objects.filter(actif=True)
            .exclude(statut='archive')
            .exclude(devise__in=['', 'EUR'])
            .values_list('devise', flat=True)
        )
    devises = {d.upper() for d in devises if d and d.upper() != 'EUR'}

    stats = {'maj': 0, 'erreurs': 0, 'devises': sorted(devises)}
    if not devises:
        return stats

    client = EODHDClient()
    aujourd_hui = date.today()

    for devise in devises:
        # GBX (pence) se convertit via GBP
        base = 'GBP' if devise == 'GBX' else devise
        paire = f"EUR{base}.FOREX"
        try:
            cours = client.get_cours_eod(paire, depuis=aujourd_hui - timedelta(days=7))
            close = None
            for bougie in reversed(cours or []):
                close = bougie.get('close') or bougie.get('adjusted_close')
                if close:
                    break
            if not close or float(close) == 0:
                raise EODHDError(f"pas de close exploitable pour {paire}")

            taux = Decimal('1') / Decimal(str(close))
            if devise == 'GBX':
                taux = taux / Decimal('100')

            TauxChange.objects.update_or_create(
                devise=devise, date=aujourd_hui,
                defaults={'taux_vers_eur': taux, 'source': 'eodhd'},
            )
            stats['maj'] += 1
            logger.info("Taux %s : 1 %s = %s EUR (via %s)", devise, devise, round(taux, 6), paire)
        except (EODHDError, Exception) as e:
            stats['erreurs'] += 1
            logger.error("Rafraîchissement taux %s échoué : %s", devise, e)

    return stats
