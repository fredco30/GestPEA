"""
app/services/newsapi_client.py
-------------------------------
Client Python pour l'API NewsAPI (newsapi.org).
Collecte les actualités financières en complément d'EODHD.

Quota gratuit : 100 requêtes / jour.

Usage :
    from app.services.newsapi_client import NewsAPIClient
    client = NewsAPIClient()
    nb = client.import_news_pour_titres(['MC.PA', 'AI.PA'])
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import Article, Titre

logger = logging.getLogger(__name__)

NEWSAPI_BASE_URL = "https://newsapi.org/v2"
TIMEOUT_SEC = 15
PAUSE_INTER_REQ = 1.0
MAX_RETRIES = 3
RETRY_DELAY = 5


class NewsAPIError(Exception):
    """Erreur generique NewsAPI."""


class NewsAPIRateLimitError(NewsAPIError):
    """Quota journalier atteint."""


class NewsAPIClient:
    """
    Client NewsAPI avec retry automatique et compteur de quota.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.NEWSAPI_KEY
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "X-Api-Key": self.api_key,
        })
        self._req_count = 0

    @property
    def nb_requetes_session(self) -> int:
        return self._req_count

    # ------------------------------------------------------------------
    # Requete HTTP
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """GET avec retry et gestion d'erreurs."""
        url = f"{NEWSAPI_BASE_URL}/{endpoint}"
        params = params or {}

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=TIMEOUT_SEC)
                self._req_count += 1

                if resp.status_code == 429:
                    raise NewsAPIRateLimitError("Quota NewsAPI atteint (HTTP 429)")
                if resp.status_code == 401:
                    raise NewsAPIError("Cle API NewsAPI invalide (HTTP 401)")

                resp.raise_for_status()
                data = resp.json()

                if data.get("status") == "error":
                    raise NewsAPIError(data.get("message", "Erreur inconnue"))

                time.sleep(PAUSE_INTER_REQ)
                return data

            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == MAX_RETRIES:
                    raise NewsAPIError(f"Echec apres {MAX_RETRIES} tentatives : {e}")
                logger.warning("NewsAPI tentative %d/%d : %s", attempt, MAX_RETRIES, e)
                time.sleep(RETRY_DELAY * attempt)

        raise NewsAPIError("Echec inattendu")

    # ------------------------------------------------------------------
    # Recherche d'articles
    # ------------------------------------------------------------------

    def rechercher_articles(self, query: str, depuis_jours: int = 7,
                            langue: str = "fr", page_size: int = 20) -> list[dict]:
        """
        Recherche d'articles via /everything.
        Retourne la liste brute des articles.
        """
        date_depuis = (datetime.now() - timedelta(days=depuis_jours)).strftime("%Y-%m-%d")

        data = self._get("everything", {
            "q": query,
            "from": date_depuis,
            "language": langue,
            "sortBy": "publishedAt",
            "pageSize": min(page_size, 100),
        })

        return data.get("articles", [])

    def rechercher_headlines(self, query: str = None, pays: str = "fr",
                             categorie: str = "business", page_size: int = 20) -> list[dict]:
        """
        Headlines via /top-headlines (actualites principales).
        """
        params = {
            "country": pays,
            "category": categorie,
            "pageSize": min(page_size, 100),
        }
        if query:
            params["q"] = query

        data = self._get("top-headlines", params)
        return data.get("articles", [])

    # ------------------------------------------------------------------
    # Import en base
    # ------------------------------------------------------------------

    def import_news_pour_titres(self, tickers: list[str],
                                depuis_jours: int = 7) -> int:
        """
        Pour chaque titre, recherche les articles NewsAPI et les persiste.
        Utilise le nom court du titre comme requete de recherche.

        Returns : nombre total d'articles crees.
        """
        titres_map = {
            t.ticker: t
            for t in Titre.objects.filter(ticker__in=tickers)
        }

        # URLs deja en base (30 derniers jours) pour eviter les doublons
        urls_connues = set(
            Article.objects.filter(
                date_pub__gte=timezone.now() - timedelta(days=30)
            ).values_list("url", flat=True)
        )

        total_crees = 0

        for ticker, titre_obj in titres_map.items():
            # Requete avec le nom court ou le nom complet
            nom_recherche = titre_obj.nom_court or titre_obj.nom or ticker.split(".")[0]

            try:
                articles_bruts = self.rechercher_articles(
                    query=nom_recherche,
                    depuis_jours=depuis_jours,
                    langue="fr",
                    page_size=10,
                )
            except NewsAPIRateLimitError:
                logger.warning("NewsAPI quota atteint — arret de l'import")
                break
            except NewsAPIError as e:
                logger.error("NewsAPI erreur pour %s : %s", ticker, e)
                continue

            a_creer = []
            for raw in articles_bruts:
                url = (raw.get("url") or "")[:500]
                if not url or url in urls_connues:
                    continue

                try:
                    date_pub = datetime.fromisoformat(
                        raw.get("publishedAt", "").replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    date_pub = timezone.now()

                a_creer.append(Article(
                    titre=titre_obj,
                    date_pub=date_pub,
                    source="newsapi",
                    url=url,
                    titre_art=(raw.get("title") or "")[:300],
                    extrait=(raw.get("description") or raw.get("content") or "")[:2000],
                    auteur=(raw.get("author") or raw.get("source", {}).get("name", ""))[:100],
                ))
                urls_connues.add(url)

            if a_creer:
                with transaction.atomic():
                    Article.objects.bulk_create(a_creer, ignore_conflicts=True)
                total_crees += len(a_creer)

            logger.info("NewsAPI %s : %d crees / %d recus",
                        ticker, len(a_creer), len(articles_bruts))

        logger.info("NewsAPI total : %d articles crees", total_crees)
        return total_crees

    # ------------------------------------------------------------------
    # Quota
    # ------------------------------------------------------------------

    def maj_quota(self):
        """Met a jour le quota NewsAPI en base."""
        from app.models import ApiQuota
        from datetime import date as dt_date

        quota, _ = ApiQuota.objects.get_or_create(
            date=dt_date.today(),
            api='newsapi',
            defaults={'nb_requetes': 0},
        )
        quota.nb_requetes += self._req_count
        quota.save(update_fields=['nb_requetes'])
        return quota
