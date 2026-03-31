"""
app/tasks/collecte.py
---------------------
Tâches Celery pour la collecte automatique des données PEA.

Planning (défini dans settings.py CELERY_BEAT_SCHEDULE) :
  - 18h30 lun-ven  : fetch_cours_eod          (cours du jour, tous titres)
  - 19h00 lun+mer  : fetch_fondamentaux('A')   (lot A)
  - 19h00 mar+jeu  : fetch_fondamentaux('B')   (lot B)
  - 20h00 lun-ven  : fetch_news                (news mutualisées)
  - 08h00 1er ven  : update_eligibles_pea      (screener mensuel)
"""

import logging
from datetime import date

from celery import shared_task
from django.db import transaction

from app.models import Titre, PrixJournalier
from app.services.eodhd import EODHDClient, EODHDRateLimitError, EODHDError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tickers(statuts=("portefeuille", "surveillance"), lot=None):
    """Retourne la liste des tickers actifs, filtrés par statut et lot."""
    qs = Titre.objects.filter(actif=True, statut__in=statuts)
    if lot:
        qs = qs.filter(lot=lot)
    return list(qs.values_list("ticker", flat=True))


def _log_resume(task_name: str, ok: list, ko: list, nb_req: int):
    logger.info(
        "[%s] Terminé — OK: %d, Erreurs: %d, Requêtes API: %d",
        task_name, len(ok), len(ko), nb_req
    )
    if ko:
        logger.warning("[%s] Tickers en erreur : %s", task_name, ko)


# ---------------------------------------------------------------------------
# 1. Cours EOD quotidiens
# ---------------------------------------------------------------------------

@shared_task(
    name="app.tasks.fetch_cours_eod",
    bind=True,
    max_retries=1,
    default_retry_delay=300,   # réessai dans 5 min si problème réseau
)
def fetch_cours_eod(self):
    """
    Récupère la bougie du jour pour tous les titres actifs.
    Tâche : chaque soir lun-ven à 18h30 (après clôture Euronext 17h30).
    Coût quota EODHD : 1 requête × nombre de titres.
    """
    client  = EODHDClient()
    tickers = _get_tickers()

    if not tickers:
        logger.info("fetch_cours_eod : aucun titre actif.")
        return

    if not client.bourse_ouverte():
        logger.info("fetch_cours_eod : marché fermé aujourd'hui.")
        return

    ok, ko = [], []

    for ticker in tickers:
        try:
            prix = client.maj_cours_du_jour(ticker)
            if prix:
                ok.append(ticker)
        except EODHDRateLimitError as e:
            logger.error("Quota EODHD atteint pendant fetch_cours_eod : %s", e)
            break   # on arrête proprement pour ne pas gaspiller
        except EODHDError as e:
            logger.error("Erreur cours %s : %s", ticker, e)
            ko.append(ticker)
        except Exception as e:
            logger.exception("Erreur inattendue cours %s : %s", ticker, e)
            ko.append(ticker)

    _log_resume("fetch_cours_eod", ok, ko, client.nb_requetes_session)

    # Après mise à jour des cours, déclenche le calcul des indicateurs
    if ok:
        calculate_indicators.delay(ok)

    return {"ok": ok, "ko": ko, "requetes": client.nb_requetes_session}


# ---------------------------------------------------------------------------
# 2. Fondamentaux (rotation lots A / B)
# ---------------------------------------------------------------------------

@shared_task(
    name="app.tasks.fetch_fondamentaux",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
)
def fetch_fondamentaux(self, lot: str):
    """
    Met à jour les fondamentaux du lot spécifié (A ou B).

    Lot A → lundi + mercredi
    Lot B → mardi + jeudi

    La rotation garantit que chaque titre est rafraîchi 2x/semaine
    sans jamais dépasser 20 requêtes/jour (quota EODHD gratuit).

    Coût quota : 1 requête × nombre de titres dans le lot.
    """
    if lot not in ("A", "B"):
        logger.error("Lot invalide : %s (doit être 'A' ou 'B')", lot)
        return

    client  = EODHDClient()
    tickers = _get_tickers(lot=lot)

    if not tickers:
        logger.info("fetch_fondamentaux lot %s : aucun titre.", lot)
        return

    ok, ko = [], []

    for ticker in tickers:
        try:
            fond = client.maj_fondamentaux(ticker)
            if fond:
                ok.append(ticker)
                logger.debug("Lot %s — %s : score_qualite=%s",
                             lot, ticker, fond.score_qualite)
        except EODHDRateLimitError as e:
            logger.error("Quota EODHD atteint pendant fondamentaux lot %s : %s", lot, e)
            break
        except EODHDError as e:
            logger.error("Erreur fondamentaux %s : %s", ticker, e)
            ko.append(ticker)
        except Exception as e:
            logger.exception("Erreur inattendue fondamentaux %s : %s", ticker, e)
            ko.append(ticker)

    _log_resume(f"fetch_fondamentaux_lot_{lot}", ok, ko, client.nb_requetes_session)
    return {"lot": lot, "ok": ok, "ko": ko, "requetes": client.nb_requetes_session}


# ---------------------------------------------------------------------------
# 3. News & articles mutualisés
# ---------------------------------------------------------------------------

@shared_task(
    name="app.tasks.fetch_news",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def fetch_news(self):
    """
    Collecte les news pour TOUS les titres en UN SEUL appel EODHD.
    Coût quota : 1 requête, quelle que soit la taille du portefeuille.

    Après la collecte, déclenche le scoring sentiment via LLM.
    """
    client  = EODHDClient()
    tickers = _get_tickers()

    if not tickers:
        logger.info("fetch_news : aucun titre actif.")
        return

    try:
        nb_crees = client.import_news(tickers)
        logger.info("fetch_news : %d articles créés", nb_crees)
    except EODHDRateLimitError as e:
        logger.error("Quota EODHD atteint pendant fetch_news : %s", e)
        return
    except EODHDError as e:
        logger.error("Erreur fetch_news : %s", e)
        return
    except Exception as e:
        logger.exception("Erreur inattendue fetch_news : %s", e)
        return

    # Déclenche le scoring LLM sur les articles non encore scorés
    if nb_crees > 0:
        score_articles_non_traites.delay()

    return {"articles_crees": nb_crees, "requetes": client.nb_requetes_session}


# ---------------------------------------------------------------------------
# 4. Screener PEA mensuel
# ---------------------------------------------------------------------------

@shared_task(
    name="app.tasks.update_eligibles_pea",
    bind=True,
    max_retries=0,   # pas de retry — tâche mensuelle, on réessaie le lendemain
)
def update_eligibles_pea(self):
    """
    Met à jour l'éligibilité PEA de tous les titres actifs.
    Planifié : 1er vendredi du mois à 08h00.
    Coût quota : 1 requête × nombre de titres actifs.

    ⚠ Cette tâche peut consommer plusieurs requêtes si le portefeuille
    est important — prévue le matin avant les autres tâches.
    """
    client = EODHDClient()

    try:
        stats = client.maj_eligibilite_tous_titres()
        logger.info("Screener PEA : %s", stats)
    except EODHDRateLimitError as e:
        logger.error("Quota atteint pendant screener PEA : %s", e)
        return
    except Exception as e:
        logger.exception("Erreur screener PEA : %s", e)
        return

    return stats


# ---------------------------------------------------------------------------
# 5. Import historique initial (manuel, appelé à l'ajout d'un titre)
# ---------------------------------------------------------------------------

@shared_task(name="app.tasks.import_historique_bulk")
def import_historique_bulk(ticker: str):
    """
    Import initial de l'historique OHLCV complet d'un titre.
    Appelé une seule fois lors de l'ajout d'un titre dans l'interface.
    Coût quota : 1 requête (bulk).
    """
    client = EODHDClient()
    try:
        nb = client.import_historique_bulk(ticker)
        logger.info("Import bulk %s : %d bougies", ticker, nb)
        # Calcule les indicateurs sur tout l'historique
        if nb > 0:
            calculate_indicators.delay([ticker])
        return {"ticker": ticker, "bougies": nb}
    except EODHDError as e:
        logger.error("Erreur import bulk %s : %s", ticker, e)
        return {"ticker": ticker, "erreur": str(e)}


# ---------------------------------------------------------------------------
# 6. Calcul des indicateurs techniques (post-collecte des cours)
# ---------------------------------------------------------------------------

@shared_task(name="app.tasks.calculate_indicators")
def calculate_indicators(tickers: list[str] = None):
    """
    Calcule RSI, MACD, Moyennes Mobiles et Bollinger sur les PrixJournalier.
    Appelé automatiquement après fetch_cours_eod et import_historique_bulk.

    Utilise pandas + pandas-ta (pip install pandas-ta).
    Stocke les valeurs calculées directement dans les champs de PrixJournalier.
    """
    try:
        import pandas as pd
        import pandas_ta as ta
    except ImportError:
        logger.error("pandas-ta non installé. Lancer : pip install pandas-ta")
        return

    if tickers is None:
        tickers = _get_tickers()

    for ticker in tickers:
        try:
            titre_obj = Titre.objects.get(ticker=ticker)
            _calculer_indicateurs_titre(titre_obj)
        except Titre.DoesNotExist:
            logger.warning("Titre %s introuvable", ticker)
        except Exception as e:
            logger.exception("Erreur calcul indicateurs %s : %s", ticker, e)

    logger.info("Indicateurs calculés pour %d titre(s)", len(tickers))


def _calculer_indicateurs_titre(titre_obj: Titre):
    """Calcule et persiste les indicateurs pour un titre."""
    import pandas as pd
    import pandas_ta as ta
    from decimal import Decimal

    # Chargement des prix en DataFrame (ordre chronologique)
    qs = (PrixJournalier.objects
          .filter(titre=titre_obj)
          .order_by("date")
          .values("id", "date", "ouverture", "haut", "bas", "cloture", "volume"))

    if not qs.exists():
        return

    df = pd.DataFrame(list(qs))
    df["cloture"] = df["cloture"].astype(float)
    df["haut"]    = df["haut"].astype(float)
    df["bas"]     = df["bas"].astype(float)
    df["volume"]  = df["volume"].astype(float)

    # --- Calculs ---
    # RSI 14
    df["rsi_14"] = ta.rsi(df["cloture"], length=14)

    # MACD (12, 26, 9)
    macd_df = ta.macd(df["cloture"], fast=12, slow=26, signal=9)
    if macd_df is not None:
        df["macd"]        = macd_df.iloc[:, 0]   # MACD line
        df["macd_signal"] = macd_df.iloc[:, 2]   # Signal line
        df["macd_hist"]   = macd_df.iloc[:, 1]   # Histogram

    # Moyennes mobiles
    df["mm_20"]  = ta.sma(df["cloture"], length=20)
    df["mm_50"]  = ta.sma(df["cloture"], length=50)
    df["mm_200"] = ta.sma(df["cloture"], length=200)

    # Bollinger Bands (20, 2)
    boll_df = ta.bbands(df["cloture"], length=20, std=2)
    if boll_df is not None:
        df["boll_sup"] = boll_df.iloc[:, 0]   # Upper
        df["boll_mid"] = boll_df.iloc[:, 1]   # Middle
        df["boll_inf"] = boll_df.iloc[:, 2]   # Lower

    # Ratio volume vs moyenne 20j
    df["vol_moy_20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = (df["volume"] / df["vol_moy_20"]).round(2)

    # --- Mise à jour en base (uniquement les lignes nouvellement calculées) ---
    def safe_dec(val):
        try:
            if pd.isna(val):
                return None
            return Decimal(str(round(float(val), 4)))
        except Exception:
            return None

    champs_indicateurs = [
        "rsi_14", "macd", "macd_signal", "macd_hist",
        "mm_20", "mm_50", "mm_200",
        "boll_sup", "boll_mid", "boll_inf", "volume_ratio"
    ]

    # Mise à jour uniquement des lignes où les indicateurs ne sont pas encore calculés
    # ou de la dernière bougie (toujours recalculée)
    from django.utils import timezone
    a_maj = []
    for _, row in df.iterrows():
        obj = PrixJournalier(
            pk=int(row["id"]),
            date_calcul_indicateurs=timezone.now(),
        )
        for champ in champs_indicateurs:
            setattr(obj, champ, safe_dec(row.get(champ)))
        a_maj.append(obj)

    if a_maj:
        with transaction.atomic():
            PrixJournalier.objects.bulk_update(
                a_maj,
                champs_indicateurs + ["date_calcul_indicateurs"],
                batch_size=500,
            )

    logger.debug("Indicateurs %s : %d lignes mises à jour", titre_obj.ticker, len(a_maj))


# ---------------------------------------------------------------------------
# 7. Scoring sentiment LLM (stub — sera complété dans scoring_llm.py)
# ---------------------------------------------------------------------------

@shared_task(name="app.tasks.score_articles_non_traites")
def score_articles_non_traites():
    """
    Appelle le service LLM (Claude API) pour scorer les articles
    dont score_sentiment est encore NULL.
    Implémentation complète dans app/services/scoring_llm.py (étape suivante).
    """
    from app.models import Article
    non_scores = Article.objects.filter(score_sentiment__isnull=True).count()
    logger.info("Articles en attente de scoring LLM : %d", non_scores)
    # TODO : implémenter scoring_llm.score_batch()
