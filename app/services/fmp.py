"""
app/services/fmp.py
-------------------
Client Python pour l'API Financial Modeling Prep (FMP).
Fournit des fondamentaux complementaires a EODHD.

Quota gratuit : 250 requetes / jour.

Usage :
    from app.services.fmp import FMPClient
    client = FMPClient()
    fond = client.maj_fondamentaux('MC.PA')
"""

import logging
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import Fondamentaux, Titre

logger = logging.getLogger(__name__)

FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"
TIMEOUT_SEC = 15
PAUSE_INTER_REQ = 0.5
MAX_RETRIES = 3
RETRY_DELAY = 5


class FMPError(Exception):
    """Erreur generique FMP."""


class FMPRateLimitError(FMPError):
    """Quota journalier atteint."""


class FMPClient:
    """
    Client FMP avec retry automatique et compteur de quota.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.FMP_API_KEY
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._req_count = 0

    @property
    def nb_requetes_session(self) -> int:
        return self._req_count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dec(val) -> Optional[Decimal]:
        """Convertit une valeur en Decimal, None si impossible."""
        if val is None:
            return None
        try:
            d = Decimal(str(val))
            return d if d.is_finite() else None
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _ticker_fmp(ticker: str) -> str:
        """
        Convertit un ticker EODHD (MC.PA) en ticker FMP (MC.PA).
        FMP utilise le meme format pour les marches europeens.
        """
        return ticker

    # ------------------------------------------------------------------
    # Requete HTTP
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict = None) -> dict | list:
        """GET avec retry et gestion d'erreurs."""
        url = f"{FMP_BASE_URL}/{endpoint}"
        params = params or {}
        params["apikey"] = self.api_key

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=TIMEOUT_SEC)
                self._req_count += 1

                if resp.status_code == 429:
                    raise FMPRateLimitError("Quota FMP atteint (HTTP 429)")
                if resp.status_code == 403:
                    raise FMPError("Cle API FMP invalide ou acces refuse (HTTP 403)")

                resp.raise_for_status()
                data = resp.json()

                # FMP retourne parfois {"Error Message": "..."}
                if isinstance(data, dict) and "Error Message" in data:
                    raise FMPError(data["Error Message"])

                time.sleep(PAUSE_INTER_REQ)
                return data

            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == MAX_RETRIES:
                    raise FMPError(f"Echec apres {MAX_RETRIES} tentatives : {e}")
                logger.warning("FMP tentative %d/%d : %s", attempt, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY * attempt)

        raise FMPError("Echec inattendu")

    # ------------------------------------------------------------------
    # Profil entreprise
    # ------------------------------------------------------------------

    def get_profil(self, ticker: str) -> Optional[dict]:
        """Recupere le profil d'une entreprise (secteur, capitalisation, etc.)."""
        sym = self._ticker_fmp(ticker)
        data = self._get(f"profile/{sym}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    # ------------------------------------------------------------------
    # Ratios financiers
    # ------------------------------------------------------------------

    def get_ratios(self, ticker: str) -> Optional[dict]:
        """Recupere les ratios financiers TTM (trailing twelve months)."""
        sym = self._ticker_fmp(ticker)
        data = self._get(f"ratios-ttm/{sym}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    def get_key_metrics(self, ticker: str) -> Optional[dict]:
        """Recupere les metriques cles TTM."""
        sym = self._ticker_fmp(ticker)
        data = self._get(f"key-metrics-ttm/{sym}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    # ------------------------------------------------------------------
    # Analyst consensus
    # ------------------------------------------------------------------

    def get_analyst_estimates(self, ticker: str) -> Optional[dict]:
        """Recupere le consensus analystes."""
        sym = self._ticker_fmp(ticker)
        data = self._get(f"analyst-estimates/{sym}", {"limit": 1})
        if isinstance(data, list) and data:
            return data[0]
        return None

    def get_price_target(self, ticker: str) -> Optional[dict]:
        """Recupere l'objectif de cours consensus."""
        sym = self._ticker_fmp(ticker)
        data = self._get(f"price-target-consensus/{sym}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    # ------------------------------------------------------------------
    # Mise a jour fondamentaux en base
    # ------------------------------------------------------------------

    def maj_fondamentaux(self, ticker: str) -> Optional[Fondamentaux]:
        """
        Recupere les fondamentaux FMP et les persiste en base.
        Complete les donnees EODHD (champs manquants uniquement).

        Cout quota : 2-4 requetes selon disponibilite.
        Returns : instance Fondamentaux ou None si erreur.
        """
        try:
            titre_obj = Titre.objects.get(ticker=ticker)
        except Titre.DoesNotExist:
            logger.error("FMP : titre %s introuvable en base", ticker)
            return None

        try:
            profil = self.get_profil(ticker)
            ratios = self.get_ratios(ticker)
        except FMPRateLimitError:
            logger.warning("FMP quota atteint pour %s", ticker)
            return None
        except FMPError as e:
            logger.error("FMP erreur pour %s : %s", ticker, e)
            return None

        if not profil and not ratios:
            logger.warning("FMP : aucune donnee pour %s", ticker)
            return None

        profil = profil or {}
        ratios = ratios or {}

        # Optionnel : metriques cles et objectif de cours
        key_metrics = None
        price_target = None
        try:
            key_metrics = self.get_key_metrics(ticker) or {}
            price_target = self.get_price_target(ticker) or {}
        except FMPError as e:
            logger.debug("FMP metriques optionnelles %s : %s", ticker, e)

        key_metrics = key_metrics or {}
        price_target = price_target or {}

        # Verifier s'il existe deja des fondamentaux EODHD pour aujourd'hui
        aujourd_hui = date.today()
        existant = (
            Fondamentaux.objects.filter(titre=titre_obj, date_maj=aujourd_hui)
            .first()
        )

        defaults = {}

        # Valorisation
        if self._dec(ratios.get("peRatioTTM")):
            defaults.setdefault("per", self._dec(ratios["peRatioTTM"]))
        if self._dec(ratios.get("pegRatioTTM")):
            defaults.setdefault("peg", self._dec(ratios["pegRatioTTM"]))
        if self._dec(ratios.get("priceToBookRatioTTM")):
            defaults.setdefault("p_book", self._dec(ratios["priceToBookRatioTTM"]))
        if self._dec(key_metrics.get("enterpriseValueOverEBITDATTM")):
            defaults.setdefault("ev_ebitda", self._dec(key_metrics["enterpriseValueOverEBITDATTM"]))
        if profil.get("mktCap"):
            defaults.setdefault("capitalisation", int(profil["mktCap"]))

        # Rentabilite
        if self._dec(ratios.get("returnOnEquityTTM")):
            defaults.setdefault("roe", self._dec(ratios["returnOnEquityTTM"]) * 100)
        if self._dec(ratios.get("returnOnAssetsTTM")):
            defaults.setdefault("roa", self._dec(ratios["returnOnAssetsTTM"]) * 100)
        if self._dec(ratios.get("netProfitMarginTTM")):
            defaults.setdefault("marge_nette", self._dec(ratios["netProfitMarginTTM"]) * 100)
        if self._dec(ratios.get("operatingProfitMarginTTM")):
            defaults.setdefault("marge_operationnelle",
                                self._dec(ratios["operatingProfitMarginTTM"]) * 100)

        # Solidite bilan
        if self._dec(key_metrics.get("debtToEquityTTM")):
            defaults.setdefault("dette_nette_ebitda",
                                self._dec(key_metrics["debtToEquityTTM"]))
        if self._dec(key_metrics.get("freeCashFlowPerShareTTM")):
            defaults.setdefault("cash_flow_libre",
                                int(float(key_metrics["freeCashFlowPerShareTTM"])))

        # Dividende
        if self._dec(ratios.get("dividendYieldTTM")):
            defaults.setdefault("rendement_dividende",
                                self._dec(ratios["dividendYieldTTM"]) * 100)
        if self._dec(ratios.get("payoutRatioTTM")):
            defaults.setdefault("payout_ratio",
                                self._dec(ratios["payoutRatioTTM"]) * 100)

        # Analystes
        if price_target.get("targetConsensus"):
            defaults.setdefault("objectif_cours_moyen",
                                self._dec(price_target["targetConsensus"]))

        if not defaults:
            logger.info("FMP %s : aucune donnee exploitable", ticker)
            return None

        if existant:
            # Completer uniquement les champs vides de l'enregistrement EODHD
            updated_fields = []
            for field, value in defaults.items():
                if getattr(existant, field) is None and value is not None:
                    setattr(existant, field, value)
                    updated_fields.append(field)
            if updated_fields:
                existant.save(update_fields=updated_fields)
                logger.info("FMP %s : %d champs completes (%s)",
                            ticker, len(updated_fields), ", ".join(updated_fields))
            return existant
        else:
            # Creer un nouvel enregistrement source FMP
            defaults["source"] = "fmp"
            fond = Fondamentaux.objects.create(
                titre=titre_obj,
                date_maj=aujourd_hui,
                **defaults,
            )
            logger.info("FMP %s : fondamentaux crees (source fmp)", ticker)
            return fond

    # ------------------------------------------------------------------
    # Quota
    # ------------------------------------------------------------------

    def maj_quota(self):
        """Met a jour le quota FMP en base."""
        from app.models import ApiQuota

        quota, _ = ApiQuota.objects.get_or_create(
            date=date.today(),
            api='fmp',
            defaults={'nb_requetes': 0},
        )
        quota.nb_requetes += self._req_count
        quota.save(update_fields=['nb_requetes'])
        return quota
