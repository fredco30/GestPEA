"""
app/services/yfinance_client.py
-------------------------------
Client Yahoo Finance (via yfinance) pour récupérer les cours OHLCV
sans clé API ni quota.

Avantages vs EODHD pour les cours :
  - Zéro quota, zéro clé API
  - Mode batch : 1 seul appel HTTP pour N tickers
  - Cours ajustés (splits/dividendes)
  - Libère les 20 req/jour EODHD pour les fondamentaux

Fallback : si yfinance échoue (Yahoo change ses endpoints ~2-3x/an),
la tâche Celery bascule automatiquement sur EODHD.

Usage :
    from app.services.yfinance_client import YFinanceClient
    client = YFinanceClient()
    result = client.maj_cours_batch(['MC.PA', 'AI.PA', 'BNP.PA'])
"""

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction

from app.models import PrixJournalier, Titre

logger = logging.getLogger(__name__)


class YFinanceClient:
    """
    Client Yahoo Finance via yfinance.
    Mode batch : récupère tous les tickers en un seul appel HTTP.
    """

    @staticmethod
    def _dec(val) -> Optional[Decimal]:
        """Convertit un float pandas en Decimal, gère NaN."""
        try:
            import math
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return None
            return Decimal(str(round(val, 4)))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def maj_cours_batch(self, tickers: list[str], jours: int = 7) -> dict:
        """
        Récupère les cours OHLCV pour TOUS les tickers en un seul appel.

        Args:
            tickers: liste de tickers (ex: ['MC.PA', 'AI.PA'])
            jours: profondeur de la fenêtre (défaut 7j)

        Returns:
            {'ok': [...], 'ko': [...], 'source': 'yfinance'}
        """
        import yfinance as yf

        if not tickers:
            return {'ok': [], 'ko': [], 'source': 'yfinance'}

        ok, ko = [], []
        multi = len(tickers) > 1

        try:
            raw = yf.download(
                tickers,
                period=f"{jours}d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )

            if raw.empty:
                logger.warning("[yfinance] Aucune donnée retournée pour %s", tickers)
                return {'ok': [], 'ko': list(tickers), 'source': 'yfinance'}

        except Exception as e:
            logger.error("[yfinance] Erreur download batch : %s", e)
            return {'ok': [], 'ko': list(tickers), 'source': 'yfinance'}

        for ticker in tickers:
            try:
                self._traiter_ticker(raw, ticker, multi)
                ok.append(ticker)
            except Exception as e:
                logger.error("[yfinance] Erreur %s : %s", ticker, e)
                ko.append(ticker)

        logger.info("[yfinance] Batch terminé : %d ok, %d ko", len(ok), len(ko))
        return {'ok': ok, 'ko': ko, 'source': 'yfinance'}

    def _traiter_ticker(self, raw, ticker: str, multi: bool) -> None:
        """Traite les données d'un ticker depuis le DataFrame batch."""
        # Extraction du sous-DataFrame pour ce ticker
        if multi:
            df = raw[ticker].dropna(how='all')
        else:
            df = raw.dropna(how='all')

        if df.empty:
            logger.warning("[yfinance] Pas de données pour %s", ticker)
            return

        titre_obj = Titre.objects.get(ticker=ticker)
        prev_close = None

        # Chercher la clôture veille en base pour la première bougie
        derniere_en_base = (
            PrixJournalier.objects
            .filter(titre=titre_obj, date__lt=df.index[0].date())
            .order_by('-date')
            .values_list('cloture', flat=True)
            .first()
        )
        if derniere_en_base is not None:
            prev_close = Decimal(str(derniere_en_base))

        for idx, row in df.iterrows():
            d = idx.date()
            ouverture = self._dec(row.get("Open"))
            haut      = self._dec(row.get("High"))
            bas       = self._dec(row.get("Low"))
            cloture   = self._dec(row.get("Close"))
            volume    = int(row.get("Volume", 0)) if row.get("Volume") else 0

            if any(v is None for v in (ouverture, haut, bas, cloture)):
                prev_close = cloture or prev_close
                continue

            PrixJournalier.objects.update_or_create(
                titre=titre_obj,
                date=d,
                defaults={
                    "ouverture":      ouverture,
                    "haut":           haut,
                    "bas":            bas,
                    "cloture":        cloture,
                    "cloture_veille": prev_close,
                    "volume":         volume,
                },
            )
            prev_close = cloture

    def maj_cours_single(self, ticker: str, jours: int = 7) -> Optional[PrixJournalier]:
        """
        Récupère les cours pour un seul ticker.
        Utile pour un import ponctuel ou un fallback.

        Returns: le PrixJournalier le plus récent, ou None.
        """
        result = self.maj_cours_batch([ticker], jours=jours)
        if ticker in result['ok']:
            titre_obj = Titre.objects.get(ticker=ticker)
            return titre_obj.prix_journaliers.order_by('-date').first()
        return None

    def import_historique(self, ticker: str) -> int:
        """
        Import historique complet via yfinance (max ~20 ans).
        Alternative gratuite à EODHD import_historique_bulk.
        Coût : 0 requête API payante.
        """
        import yfinance as yf

        titre_obj = Titre.objects.get(ticker=ticker)
        logger.info("[yfinance] Import historique : %s", ticker)

        try:
            tk = yf.Ticker(ticker)
            df = tk.history(period="max", auto_adjust=True)
        except Exception as e:
            logger.error("[yfinance] Erreur historique %s : %s", ticker, e)
            return 0

        if df.empty:
            logger.warning("[yfinance] Aucune donnée historique pour %s", ticker)
            return 0

        dates_existantes = set(
            PrixJournalier.objects.filter(titre=titre_obj)
            .values_list("date", flat=True)
        )

        a_creer = []
        prev_close = None

        for idx, row in df.iterrows():
            d = idx.date()
            cloture   = self._dec(row.get("Close"))
            ouverture = self._dec(row.get("Open"))
            haut      = self._dec(row.get("High"))
            bas       = self._dec(row.get("Low"))
            volume    = int(row.get("Volume", 0)) if row.get("Volume") else 0

            if any(v is None for v in (ouverture, haut, bas, cloture)):
                prev_close = cloture or prev_close
                continue

            if d not in dates_existantes:
                a_creer.append(PrixJournalier(
                    titre          = titre_obj,
                    date           = d,
                    ouverture      = ouverture,
                    haut           = haut,
                    bas            = bas,
                    cloture        = cloture,
                    cloture_veille = prev_close,
                    volume         = volume,
                ))

            prev_close = cloture

        if a_creer:
            with transaction.atomic():
                PrixJournalier.objects.bulk_create(a_creer, ignore_conflicts=True)

        logger.info("[yfinance] Historique %s : %d bougies créées", ticker, len(a_creer))
        return len(a_creer)
