"""
services/renforcement.py
------------------------
Étape 32 — Recommandation de renforcement intelligent.

Détecte les opportunités de renforcement sur les titres en portefeuille :
  - Drawdown > seuil depuis le plus haut récent OU depuis le PRU
  - Fondamentaux solides (score qualité ≥ 6)
  - RSI < 45 (zone survendue)

Crée un Signal de type 'renforcement' qui sera intégré dans le moteur
de confluence existant (run_confluence_task).

Anti-doublon : max 1 signal renforcement par titre par semaine.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Max

from app.models import Titre, PrixJournalier, Fondamentaux, Signal, AlerteConfig

logger = logging.getLogger(__name__)

# Seuils par défaut (utilisés si AlerteConfig.seuil_drawdown est null)
SEUIL_DRAWDOWN_DEFAULT = Decimal('10.0')   # 10%
SEUIL_RSI = Decimal('45')
SEUIL_QUALITE = Decimal('6')
LOOKBACK_JOURS = 60   # Fenêtre pour le plus haut récent
ANTI_DOUBLON_JOURS = 7  # Max 1 signal par semaine


def detecter_opportunites_renforcement():
    """
    Parcourt les titres en portefeuille et détecte les opportunités
    de renforcement.

    Returns:
        int: nombre de signaux de renforcement créés.
    """
    titres = Titre.objects.filter(
        statut='portefeuille',
        actif=True,
        nb_actions__gt=0,
        prix_revient_moyen__isnull=False,
    )

    nb_signaux = 0
    aujourd_hui = date.today()

    for titre in titres:
        try:
            if _signal_recent_existe(titre, aujourd_hui):
                continue

            if not _config_autorise_renforcement(titre):
                continue

            result = _evaluer_renforcement(titre, aujourd_hui)
            if result:
                _creer_signal_renforcement(titre, aujourd_hui, result)
                nb_signaux += 1

        except Exception as e:
            logger.error("Erreur renforcement %s : %s", titre.ticker, e, exc_info=True)

    logger.info("⚙ Renforcement : %d signaux créés sur %d titres portefeuille",
                nb_signaux, titres.count())
    return nb_signaux


def _signal_recent_existe(titre, aujourd_hui):
    """Anti-doublon : vérifie qu'aucun signal renforcement n'existe cette semaine."""
    return Signal.objects.filter(
        titre=titre,
        type_signal='renforcement',
        date__gte=aujourd_hui - timedelta(days=ANTI_DOUBLON_JOURS),
    ).exists()


def _config_autorise_renforcement(titre):
    """Vérifie que l'AlerteConfig autorise les alertes de renforcement."""
    try:
        config = titre.alerte_config
        return config.actif and config.alertes_renforcement
    except AlerteConfig.DoesNotExist:
        return True  # Pas de config = tout autorisé par défaut


def _evaluer_renforcement(titre, aujourd_hui):
    """
    Évalue si un titre remplit les conditions de renforcement.

    Returns:
        dict ou None: contexte du signal si conditions remplies.
    """
    # Dernière bougie
    bougie = PrixJournalier.objects.filter(titre=titre).order_by('-date').first()
    if not bougie or not bougie.cloture:
        return None

    cours_actuel = bougie.cloture
    pru = titre.prix_revient_moyen

    # RSI doit être < seuil (survendu)
    if bougie.rsi_14 is None or bougie.rsi_14 >= SEUIL_RSI:
        return None

    # Fondamentaux solides
    fond = Fondamentaux.objects.filter(titre=titre).order_by('-date_maj').first()
    if not fond:
        return None
    score_qualite = fond.score_qualite
    if score_qualite is None or score_qualite < float(SEUIL_QUALITE):
        return None

    # Seuil drawdown (configurable via AlerteConfig)
    try:
        seuil = titre.alerte_config.seuil_drawdown or SEUIL_DRAWDOWN_DEFAULT
    except AlerteConfig.DoesNotExist:
        seuil = SEUIL_DRAWDOWN_DEFAULT

    # Plus haut récent (60 jours)
    date_min = aujourd_hui - timedelta(days=LOOKBACK_JOURS)
    haut_recent = PrixJournalier.objects.filter(
        titre=titre, date__gte=date_min
    ).aggregate(Max('haut'))['haut__max']

    # Calcul drawdown depuis le haut récent
    drawdown_haut = None
    if haut_recent and haut_recent > 0:
        drawdown_haut = (haut_recent - cours_actuel) / haut_recent * 100

    # Calcul drawdown depuis le PRU
    drawdown_pru = None
    if pru and pru > 0:
        drawdown_pru = (pru - cours_actuel) / pru * 100

    # Au moins l'un des drawdowns doit dépasser le seuil
    drawdown_principal = None
    source_drawdown = None

    if drawdown_haut and drawdown_haut >= seuil:
        drawdown_principal = drawdown_haut
        source_drawdown = 'haut_recent'

    if drawdown_pru and drawdown_pru >= seuil:
        if drawdown_principal is None or drawdown_pru > drawdown_principal:
            drawdown_principal = drawdown_pru
            source_drawdown = 'pru'

    if drawdown_principal is None:
        return None

    # Niveaux de prix en euros
    mm50 = bougie.mm_50
    mm200 = bougie.mm_200
    boll_inf = bougie.boll_inf

    return {
        'cours_actuel': float(cours_actuel),
        'pru': float(pru),
        'drawdown_pct': round(float(drawdown_principal), 1),
        'source_drawdown': source_drawdown,
        'haut_recent': float(haut_recent) if haut_recent else None,
        'rsi': float(bougie.rsi_14),
        'score_qualite': float(score_qualite),
        'mm50': float(mm50) if mm50 else None,
        'mm200': float(mm200) if mm200 else None,
        'boll_inf': float(boll_inf) if boll_inf else None,
        'objectif_analystes': float(fond.objectif_cours_moyen) if fond.objectif_cours_moyen else None,
        'nb_actions': titre.nb_actions,
        'pv_mv': float(titre.plus_moins_value) if titre.plus_moins_value else None,
    }


def _creer_signal_renforcement(titre, aujourd_hui, ctx):
    """Crée un Signal de type renforcement avec description détaillée."""
    source = "prix d'achat (PRU)" if ctx['source_drawdown'] == 'pru' else 'plus haut récent (60j)'
    description = (
        f"Baisse de {ctx['drawdown_pct']}% depuis le {source}. "
        f"Cours {ctx['cours_actuel']:.2f} €, PRU {ctx['pru']:.2f} €, "
        f"RSI {ctx['rsi']:.1f}, qualité {ctx['score_qualite']:.0f}/10."
    )

    Signal.objects.create(
        titre=titre,
        date=aujourd_hui,
        type_signal='renforcement',
        direction='haussier',
        valeur=Decimal(str(ctx['drawdown_pct'])),
        description=description[:200],
        actif=True,
    )

    logger.info("✚ Renforcement %s : drawdown %.1f%% depuis %s, RSI %.1f",
                titre.ticker, ctx['drawdown_pct'], ctx['source_drawdown'], ctx['rsi'])
