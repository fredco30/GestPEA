"""
services/indicators.py
-----------------------
Calcul des indicateurs techniques sur la série historique d'un titre.
Utilise pandas-ta (wrapper pandas sur TA-Lib).

Indicateurs calculés et stockés dans PrixJournalier :
  - RSI(14)
  - MACD(12, 26, 9) → macd, signal, histogramme
  - MM20, MM50, MM200 (Moyennes mobiles simples)
  - Bollinger Bands(20, 2) → bande sup, mid, inf
  - Volume ratio (volume / moyenne mobile volume 20j)

Appelé par :
  - run_indicateurs_task (Celery, chaque soir après fetch_cours_eod)
  - import_historique_task (après import bulk initial)
"""

import logging
import math
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

try:
    import pandas_ta as ta
    PANDAS_TA_OK = True
except ImportError:
    logger.warning("[Indicators] pandas-ta non installé — indicateurs non calculés.")
    PANDAS_TA_OK = False


def calculate_indicators(titre, nb_jours: int = None) -> int:
    """
    Calcule tous les indicateurs techniques pour un titre.

    Args:
        titre      : instance de app.models.Titre
        nb_jours   : si précisé, recalcule seulement les N derniers jours.
                     None = recalcule tout l'historique.

    Retourne le nombre de bougies mises à jour.
    """
    from app.models import PrixJournalier

    if not PANDAS_TA_OK:
        return 0

    # Charger l'historique complet en mémoire (nécessaire pour les calculs)
    qs = titre.prix_journaliers.order_by('date').values(
        'id', 'date', 'ouverture', 'haut', 'bas', 'cloture', 'volume'
    )

    if not qs.exists():
        logger.debug(f"[Indicators] {titre.ticker} — aucune bougie en base.")
        return 0

    df = pd.DataFrame(list(qs))
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()

    # Renommage pour pandas-ta
    df = df.rename(columns={
        'ouverture': 'open',
        'haut':      'high',
        'bas':       'low',
        'cloture':   'close',
        'volume':    'volume',
    })

    # Convertir en float (nécessaire pour pandas-ta)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)

    # ------------------------------------------------------------------
    # Calcul des indicateurs
    # ------------------------------------------------------------------

    # RSI(14)
    df['rsi_14'] = ta.rsi(df['close'], length=14)

    # MACD(12, 26, 9)
    macd_df = ta.macd(df['close'], fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        df['macd']        = macd_df.iloc[:, 0]  # MACD line
        df['macd_signal'] = macd_df.iloc[:, 2]  # Signal line (MACDs)
        df['macd_hist']   = macd_df.iloc[:, 1]  # Histogram (MACDh)
    else:
        df['macd'] = df['macd_signal'] = df['macd_hist'] = None

    # Moyennes mobiles simples
    df['mm_20']  = ta.sma(df['close'], length=20)
    df['mm_50']  = ta.sma(df['close'], length=50)
    df['mm_200'] = ta.sma(df['close'], length=200)

    # Bollinger Bands(20, 2)
    boll_df = ta.bbands(df['close'], length=20, std=2)
    if boll_df is not None and not boll_df.empty:
        cols = boll_df.columns.tolist()
        # pandas-ta nomme les colonnes : BBL_20_2.0, BBM_20_2.0, BBU_20_2.0
        bbl = [c for c in cols if c.startswith('BBL')]
        bbm = [c for c in cols if c.startswith('BBM')]
        bbu = [c for c in cols if c.startswith('BBU')]
        # pandas-ta v0.3+ : 'BBL_20_2.0_2.0', 'BBM_20_2.0_2.0', 'BBU_20_2.0_2.0'
        # pandas-ta v0.2  : 'BBL_20_2.0', 'BBM_20_2.0', 'BBU_20_2.0'
        # Le startswith gère les deux cas.
        df['boll_inf'] = boll_df[bbl[0]] if bbl else None
        df['boll_mid'] = boll_df[bbm[0]] if bbm else None
        df['boll_sup'] = boll_df[bbu[0]] if bbu else None
    else:
        df['boll_inf'] = df['boll_mid'] = df['boll_sup'] = None

    # Volume ratio (volume / SMA volume 20j)
    df['vol_sma20']    = ta.sma(df['volume'], length=20)
    df['volume_ratio'] = df.apply(
        lambda r: round(r['volume'] / r['vol_sma20'], 2)
        if r['vol_sma20'] and r['vol_sma20'] > 0 else None,
        axis=1
    )

    # ------------------------------------------------------------------
    # Restriction aux N derniers jours si demandé
    # ------------------------------------------------------------------

    if nb_jours:
        cutoff = pd.Timestamp(date.today() - timedelta(days=nb_jours))
        df_a_sauver = df[df.index >= cutoff]
    else:
        df_a_sauver = df

    # ------------------------------------------------------------------
    # Mise à jour en base (bulk_update par lots de 200)
    # ------------------------------------------------------------------

    now = pd.Timestamp.now(tz='UTC')
    ids_df = df_a_sauver.reset_index()[['date', 'id',
                                         'rsi_14', 'macd', 'macd_signal', 'macd_hist',
                                         'mm_20', 'mm_50', 'mm_200',
                                         'boll_sup', 'boll_mid', 'boll_inf',
                                         'volume_ratio']]

    bougies_maj = []
    for _, row in ids_df.iterrows():
        bougies_maj.append(PrixJournalier(
            id=int(row['id']),
            rsi_14       = _safe(row['rsi_14']),
            macd         = _safe(row['macd']),
            macd_signal  = _safe(row['macd_signal']),
            macd_hist    = _safe(row['macd_hist']),
            mm_20        = _safe(row['mm_20']),
            mm_50        = _safe(row['mm_50']),
            mm_200       = _safe(row['mm_200']),
            boll_sup     = _safe(row['boll_sup']),
            boll_mid     = _safe(row['boll_mid']),
            boll_inf     = _safe(row['boll_inf']),
            volume_ratio = _safe(row['volume_ratio']),
            date_calcul_indicateurs = now,
        ))

    # bulk_update par lots de 200 pour ne pas saturer la DB
    champs = [
        'rsi_14', 'macd', 'macd_signal', 'macd_hist',
        'mm_20', 'mm_50', 'mm_200',
        'boll_sup', 'boll_mid', 'boll_inf',
        'volume_ratio', 'date_calcul_indicateurs',
    ]
    BATCH = 200
    nb_total = 0
    for i in range(0, len(bougies_maj), BATCH):
        lot = bougies_maj[i:i + BATCH]
        PrixJournalier.objects.bulk_update(lot, champs)
        nb_total += len(lot)

    logger.info(
        f"[Indicators] {titre.ticker} — {nb_total} bougies mises à jour "
        f"(RSI, MACD, MM20/50/200, Bollinger, Volume ratio)"
    )
    return nb_total


def _safe(val):
    """Convertit NaN/None en None propre pour Django DecimalField."""
    if val is None:
        return None
    try:
        if math.isnan(float(val)):
            return None
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None
