"""
app/services/eodhd.py
---------------------
Client Python pour l'API EODHD (End of Day Historical Data).
Gère : cours OHLCV, fondamentaux, news, screener éligibilité PEA.

Quota gratuit : 20 requêtes / jour.
Stratégie : cache systématique en base + rotation lots A/B pour les fondamentaux.

Usage :
    from app.services.eodhd import EODHDClient
    client = EODHDClient()
    prix = client.maj_cours_du_jour('MC.PA')
    fond = client.maj_fondamentaux('MC.PA')
"""

import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import Article, Fondamentaux, PrixJournalier, Titre

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

EODHD_BASE_URL  = "https://eodhd.com/api"
TIMEOUT_SEC     = 15
PAUSE_INTER_REQ = 1.2   # secondes — respecte le fair use du quota
MAX_RETRIES     = 3
RETRY_DELAY     = 5

# Codes ISO-3 des pays dont les actions sont éligibles PEA (siège UE/EEE)
PAYS_ELIGIBLES_PEA = {
    "FRA", "DEU", "NLD", "BEL", "ESP", "ITA", "PRT", "FIN", "IRL",
    "LUX", "AUT", "GRC", "DNK", "SWE", "POL", "CZE", "HUN", "ROU",
    "SVK", "SVN", "EST", "LVA", "LTU", "BGR", "HRV", "CYP", "MLT",
    "ISL", "LIE", "NOR",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EODHDError(Exception):
    """Erreur générique EODHD."""

class EODHDRateLimitError(EODHDError):
    """Quota journalier atteint (HTTP 429)."""

class EODHDNotFoundError(EODHDError):
    """Ticker introuvable (HTTP 404)."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class EODHDClient:
    """
    Client EODHD avec retry automatique, pause inter-requêtes
    et compteur de quota de session.
    """

    def __init__(self, api_key: str = None):
        self.api_key   = api_key or settings.EODHD_API_KEY
        self.session   = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._req_count = 0

    # -------------------------------------------------------------------
    # HTTP de base
    # -------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict = None) -> dict | list:
        """GET avec retry, pause et gestion des codes d'erreur EODHD."""
        url = f"{EODHD_BASE_URL}/{endpoint}"
        p   = params or {}
        p["api_token"] = self.api_key
        p["fmt"]       = "json"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=p, timeout=TIMEOUT_SEC)

                if resp.status_code == 429:
                    raise EODHDRateLimitError("Quota journalier EODHD atteint.")
                if resp.status_code == 404:
                    raise EODHDNotFoundError(f"Ticker introuvable : {endpoint}")
                if resp.status_code == 401:
                    raise EODHDError("Clé API EODHD invalide.")

                resp.raise_for_status()
                self._req_count += 1
                logger.info("EODHD req #%d — %s [%d]",
                            self._req_count, endpoint, resp.status_code)
                time.sleep(PAUSE_INTER_REQ)
                return resp.json()

            except (EODHDRateLimitError, EODHDNotFoundError, EODHDError):
                raise
            except requests.exceptions.Timeout:
                logger.warning("Timeout EODHD (tentative %d/%d)", attempt, MAX_RETRIES)
                if attempt == MAX_RETRIES:
                    raise EODHDError(f"Timeout après {MAX_RETRIES} tentatives")
                time.sleep(RETRY_DELAY)
            except requests.exceptions.RequestException as e:
                logger.warning("Erreur réseau EODHD (tentative %d/%d): %s",
                               attempt, MAX_RETRIES, e)
                if attempt == MAX_RETRIES:
                    raise EODHDError(f"Erreur réseau : {e}")
                time.sleep(RETRY_DELAY)

    # -------------------------------------------------------------------
    # Helpers de conversion
    # -------------------------------------------------------------------

    @staticmethod
    def _dec(val, default=None) -> Optional[Decimal]:
        if val is None or val in ("", "N/A", "None"):
            return default
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return default

    @staticmethod
    def _int(val, default=None) -> Optional[int]:
        try:
            return int(float(val)) if val not in (None, "", "N/A") else default
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _date(val) -> Optional[date]:
        if not val or val in ("0000-00-00", "N/A", ""):
            return None
        try:
            return date.fromisoformat(str(val)[:10])
        except ValueError:
            return None

    # -------------------------------------------------------------------
    # 1. COURS OHLCV
    # -------------------------------------------------------------------

    def get_cours_eod(self, ticker: str,
                      depuis: date = None, jusqu: date = None) -> list[dict]:
        """
        Récupère les données OHLCV brutes depuis EODHD.
        Sans date → historique complet (import bulk initial).
        Avec dates → fenêtre limitée (mise à jour quotidienne).

        Coût quota : 1 requête.
        """
        params = {}
        if depuis:
            params["from"] = depuis.isoformat()
        if jusqu:
            params["to"] = jusqu.isoformat()

        data = self._get(f"eod/{ticker}", params)
        if not isinstance(data, list):
            raise EODHDError(f"Réponse OHLCV inattendue pour {ticker}")
        return data

    def import_historique_bulk(self, ticker: str) -> int:
        """
        Import initial complet de l'historique OHLCV.
        À appeler UNE SEULE FOIS à l'ajout d'un titre.
        Les appels suivants utilisent maj_cours_du_jour (1 bougie/jour).

        Coût quota : 1 requête.
        Returns : nombre de bougies créées en base.
        """
        titre_obj = Titre.objects.get(ticker=ticker)
        logger.info("Import historique bulk : %s", ticker)

        donnees = self.get_cours_eod(ticker)
        if not donnees:
            logger.warning("Aucune donnée OHLCV pour %s", ticker)
            return 0

        dates_existantes = set(
            PrixJournalier.objects.filter(titre=titre_obj)
            .values_list("date", flat=True)
        )

        a_creer = []
        for row in donnees:
            try:
                d = date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue

            if d in dates_existantes:
                continue

            # EODHD fournit adjusted_close (ajusté splits/dividendes) — à privilégier
            cloture = self._dec(row.get("adjusted_close")) or self._dec(row.get("close"))

            a_creer.append(PrixJournalier(
                titre     = titre_obj,
                date      = d,
                ouverture = self._dec(row.get("open")),
                haut      = self._dec(row.get("high")),
                bas       = self._dec(row.get("low")),
                cloture   = cloture,
                volume    = self._int(row.get("volume"), 0),
            ))

        if a_creer:
            with transaction.atomic():
                PrixJournalier.objects.bulk_create(a_creer, ignore_conflicts=True)

        logger.info("Bulk %s : %d bougies créées", ticker, len(a_creer))
        return len(a_creer)

    def maj_cours_du_jour(self, ticker: str) -> Optional[PrixJournalier]:
        """
        Met à jour la bougie du jour après clôture (tâche soir 18h30).
        Récupère les 5 derniers jours comme filet de sécurité
        (jours fériés, retards de publication).

        Coût quota : 1 requête.
        Returns : PrixJournalier créé/mis à jour, ou None si marché fermé.
        """
        if date.today().weekday() >= 5:
            logger.debug("Week-end — pas de mise à jour cours pour %s", ticker)
            return None

        titre_obj = Titre.objects.get(ticker=ticker)
        depuis    = date.today() - timedelta(days=5)
        donnees   = self.get_cours_eod(ticker, depuis=depuis, jusqu=date.today())

        if not donnees:
            return None

        derniere = sorted(donnees, key=lambda x: x["date"])[-1]
        d        = date.fromisoformat(derniere["date"])
        cloture  = (self._dec(derniere.get("adjusted_close"))
                    or self._dec(derniere.get("close")))

        obj, created = PrixJournalier.objects.update_or_create(
            titre = titre_obj,
            date  = d,
            defaults={
                "ouverture": self._dec(derniere.get("open")),
                "haut":      self._dec(derniere.get("high")),
                "bas":       self._dec(derniere.get("low")),
                "cloture":   cloture,
                "volume":    self._int(derniere.get("volume"), 0),
            }
        )

        logger.info("Cours %s %s %s — clôture=%s",
                    ticker, d, "créé" if created else "màj", obj.cloture)
        return obj

    # -------------------------------------------------------------------
    # 2. FONDAMENTAUX
    # -------------------------------------------------------------------

    def get_fondamentaux(self, ticker: str) -> dict:
        """
        Récupère les données fondamentales brutes EODHD.
        Réponse JSON très riche (~500 champs) — on extrait les utiles
        dans maj_fondamentaux().

        Coût quota : 1 requête.
        """
        data = self._get(f"fundamentals/{ticker}")
        if not isinstance(data, dict):
            raise EODHDError(f"Réponse fondamentaux inattendue pour {ticker}")
        return data

    def maj_fondamentaux(self, ticker: str) -> Optional[Fondamentaux]:
        """
        Extrait les fondamentaux utiles et les persiste en base.
        Appelé par les tâches lot A (lun/mer) et lot B (mar/jeu).

        Coût quota : 1 requête.
        Returns : instance Fondamentaux ou None si erreur.
        """
        titre_obj = Titre.objects.get(ticker=ticker)

        try:
            raw = self.get_fondamentaux(ticker)
        except (EODHDNotFoundError, EODHDError) as e:
            logger.error("Fondamentaux %s : %s", ticker, e)
            return None

        # Navigation dans la réponse EODHD
        highlights = raw.get("Highlights", {}) or {}
        valuation  = raw.get("Valuation", {}) or {}
        analyst    = raw.get("AnalystRatings", {}) or {}
        financials = raw.get("Financials", {}) or {}
        balance    = financials.get("Balance_Sheet", {}) or {}
        income     = financials.get("Income_Statement", {}) or {}
        dividends  = raw.get("SplitsDividends", {}) or {}

        # --- Calcul dette nette / EBITDA ---
        dette_nette_ebitda = None
        ebitda     = self._dec(highlights.get("EBITDA"))
        annual_bs  = balance.get("annual") or balance.get("yearly") or {}
        if annual_bs and ebitda and ebitda != 0:
            last_year = sorted(annual_bs.keys())[-1]
            entry = annual_bs[last_year] if isinstance(annual_bs[last_year], dict) else {}
            total_debt = self._dec(entry.get("totalDebt") or entry.get("longTermDebt"))
            cash       = self._dec(entry.get("cashAndShortTermInvestments"))
            if total_debt is not None and cash is not None:
                dette_nette = total_debt - cash
                dette_nette_ebitda = round(dette_nette / abs(ebitda), 2)

        # --- Croissance BPA sur 3 ans ---
        croissance_bpa_3ans = None
        annual_inc = income.get("annual") or income.get("yearly") or {}
        if annual_inc and len(annual_inc) >= 4:
            annees = sorted(annual_inc.keys())
            def bpa(yr):
                e = annual_inc.get(yr, {})
                return self._dec(e.get("epsActual") or e.get("eps")) if isinstance(e, dict) else None
            bpa_rec = bpa(annees[-1])
            bpa_old = bpa(annees[-4])
            if bpa_rec and bpa_old and bpa_old > 0:
                croissance_bpa_3ans = round(
                    ((bpa_rec / bpa_old) ** Decimal("0.3333") - 1) * 100, 2
                )

        # --- Dates dividende ---
        date_ex_div = self._date(
            dividends.get("ExDividendDate") or highlights.get("ExDividendDate")
        )
        date_pmt = self._date(
            dividends.get("PaymentDate") or highlights.get("DividendDate")
        )

        fond, created = Fondamentaux.objects.update_or_create(
            titre    = titre_obj,
            date_maj = date.today(),
            defaults={
                # Valorisation
                "per":              self._dec(highlights.get("PERatio")),
                "per_forward":      self._dec(highlights.get("ForwardPE")),
                "peg":              self._dec(valuation.get("PriceToEarningsGrowthRatioTTM")),
                "p_book":           self._dec(valuation.get("PriceBookMRQ")),
                "ev_ebitda":        self._dec(valuation.get("EnterpriseValueEbitda")),
                "capitalisation":   self._int(highlights.get("MarketCapitalization")),
                # Rentabilité
                "roe":              self._dec(highlights.get("ReturnOnEquityTTM")),
                "roa":              self._dec(highlights.get("ReturnOnAssetsTTM")),
                "marge_nette":      self._dec(highlights.get("ProfitMargin")),
                "marge_operationnelle": self._dec(highlights.get("OperatingMarginTTM")),
                # Solidité bilan
                "dette_nette_ebitda":   dette_nette_ebitda,
                "cash_flow_libre":      self._int(highlights.get("FreeCashflow")),
                # Croissance
                "croissance_bpa_1an":   self._dec(highlights.get("EPSEstimateNextYear")),
                "croissance_bpa_3ans":  croissance_bpa_3ans,
                "croissance_ca_1an":    self._dec(highlights.get("QuarterlyRevenueGrowthYOY")),
                # Dividende
                "rendement_dividende":  self._dec(highlights.get("DividendYield")),
                "dividende_par_action": self._dec(highlights.get("DividendShare")),
                "payout_ratio":         self._dec(highlights.get("PayoutRatio")),
                "date_ex_dividende":    date_ex_div,
                "date_paiement":        date_pmt,
                # Analystes
                "objectif_cours_moyen": self._dec(
                    analyst.get("TargetPrice") or analyst.get("PriceTarget")
                ),
                "nb_analystes":  self._int(analyst.get("NumberOfAnalysts")),
                "consensus":     (analyst.get("Rating") or "")[:20],
                "source":        "eodhd",
            }
        )

        logger.info("Fondamentaux %s %s — score_qualite=%s",
                    ticker, "créés" if created else "màj", fond.score_qualite)

        # Met à jour les métadonnées de base du Titre si manquantes
        self._sync_titre(titre_obj, raw.get("General", {}) or {})
        return fond

    def _sync_titre(self, titre_obj: Titre, general: dict) -> None:
        """Complète les métadonnées du Titre depuis General EODHD."""
        champs = {}
        mapping = {
            "nom":         "Name",
            "nom_court":   "Code",
            "place":       "Exchange",
            "pays":        "CountryISO",
            "secteur":     "Sector",
            "sous_secteur": "Industry",
            "isin":        "ISIN",
        }
        for champ_model, champ_api in mapping.items():
            if general.get(champ_api) and not getattr(titre_obj, champ_model):
                champs[champ_model] = general[champ_api]

        if champs:
            Titre.objects.filter(pk=titre_obj.pk).update(**champs)

    # -------------------------------------------------------------------
    # 3. NEWS MUTUALISÉES
    # -------------------------------------------------------------------

    def get_news_mutualise(self, tickers: list[str],
                           nb_articles: int = 50) -> list[dict]:
        """
        Récupère les news pour TOUS les tickers en un seul appel.
        Économie clé : 1 req/jour quelle que soit la taille du portefeuille.

        Coût quota : 1 requête.
        """
        if not tickers:
            return []

        data = self._get("news", {
            "s":     ",".join(tickers),
            "limit": min(nb_articles, 50),
            "offset": 0,
        })
        return data if isinstance(data, list) else []

    def import_news(self, tickers: list[str]) -> int:
        """
        Récupère les news mutualisées et crée les Articles non encore en base.
        Détecte le titre concerné par chaque article (champ `symbols` EODHD).

        Coût quota : 1 requête.
        Returns : nombre d'articles créés.
        """
        articles_bruts = self.get_news_mutualise(tickers)
        if not articles_bruts:
            return 0

        titres_map = {
            t.ticker: t
            for t in Titre.objects.filter(ticker__in=tickers)
        }

        # URLs déjà en base (30 derniers jours) pour éviter les doublons
        urls_connues = set(
            Article.objects.filter(
                date_pub__gte=timezone.now() - timedelta(days=30)
            ).values_list("url", flat=True)
        )

        a_creer = []
        for raw in articles_bruts:
            url = (raw.get("link") or raw.get("url") or "")[:500]
            if url and url in urls_connues:
                continue

            # Ticker concerné : champ symbols EODHD, puis fallback sur le titre
            ticker_concerne = None
            for sym in (raw.get("symbols") or []):
                if sym in titres_map:
                    ticker_concerne = sym
                    break
            if not ticker_concerne:
                titre_art_upper = (raw.get("title") or "").upper()
                for tk in tickers:
                    if tk.split(".")[0] in titre_art_upper:
                        ticker_concerne = tk
                        break
            if not ticker_concerne:
                continue

            try:
                date_pub = datetime.fromisoformat(
                    raw.get("date", "").replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                date_pub = timezone.now()

            a_creer.append(Article(
                titre     = titres_map[ticker_concerne],
                date_pub  = date_pub,
                source    = "eodhd",
                url       = url,
                titre_art = (raw.get("title") or "")[:300],
                extrait   = (raw.get("content") or raw.get("summary") or "")[:2000],
                auteur    = (raw.get("author") or "")[:100],
            ))

        if a_creer:
            with transaction.atomic():
                Article.objects.bulk_create(a_creer, ignore_conflicts=True)

        logger.info("News : %d créés / %d reçus", len(a_creer), len(articles_bruts))
        return len(a_creer)

    # -------------------------------------------------------------------
    # 4. SCREENER ÉLIGIBILITÉ PEA
    # -------------------------------------------------------------------

    def verifier_eligibilite_pea(self, ticker: str) -> bool:
        """
        Vérifie l'éligibilité PEA d'un titre via son pays de siège (CountryISO).
        Coût quota : 1 requête.
        """
        try:
            raw     = self.get_fondamentaux(ticker)
            general = raw.get("General", {}) or {}
            pays    = general.get("CountryISO", "").upper()
            eligible = pays in PAYS_ELIGIBLES_PEA
            logger.info("Éligibilité PEA %s : pays=%s → %s", ticker, pays, eligible)
            return eligible
        except (EODHDNotFoundError, EODHDError) as e:
            logger.error("Éligibilité %s : %s", ticker, e)
            return False

    def maj_eligibilite_tous_titres(self) -> dict:
        """
        Met à jour l'éligibilité PEA de tous les titres actifs.
        Tâche mensuelle (1er vendredi du mois — screener-pea).
        Coût quota : 1 requête × nombre de titres actifs.
        """
        titres = Titre.objects.filter(actif=True)
        stats  = {"eligible": 0, "non_eligible": 0, "erreurs": 0}

        for titre in titres:
            try:
                eligible = self.verifier_eligibilite_pea(titre.ticker)
                Titre.objects.filter(pk=titre.pk).update(
                    eligible_pea=eligible,
                    date_verif_eligibilite=date.today()
                )
                stats["eligible" if eligible else "non_eligible"] += 1
            except Exception as e:
                logger.error("Erreur éligibilité %s : %s", titre.ticker, e)
                stats["erreurs"] += 1

        logger.info("Screener PEA terminé : %s", stats)
        return stats

    # -------------------------------------------------------------------
    # 5. UTILITAIRES
    # -------------------------------------------------------------------

    def quota_restant(self) -> dict:
        """
        Interroge l'endpoint /user pour connaître le quota restant.
        À appeler en début de journée si besoin de diagnostic.
        Coût quota : 1 requête.
        """
        try:
            data = self._get("user")
            return {
                "utilisees":  data.get("apiRequests"),
                "limite":     data.get("dailyRateLimit", 20),
                "restantes":  data.get("apiRequestsLeft"),
            }
        except EODHDError as e:
            logger.warning("Quota non disponible : %s", e)
            return {}

    def recherche_ticker(self, query: str) -> list[dict]:
        """
        Recherche un ticker par nom d'entreprise.
        Ex : recherche_ticker('Air Liquide') → [{'Code': 'AI', 'Exchange': 'PA', ...}]
        Coût quota : 1 requête.
        """
        data = self._get("search", {"q": query})
        return data if isinstance(data, list) else []

    @property
    def nb_requetes_session(self) -> int:
        """Nombre de requêtes effectuées dans cette instance du client."""
        return self._req_count

    @staticmethod
    def bourse_ouverte() -> bool:
        """True si aujourd'hui est un jour de semaine (lundi–vendredi)."""
        return date.today().weekday() < 5
