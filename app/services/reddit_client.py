"""
app/services/reddit_client.py
-------------------------------
Collecte de posts Reddit depuis r/bourse, r/vosfinances et r/investir.
Utilise l'API JSON publique Reddit (pas besoin d'OAuth pour la lecture).

Usage :
    from app.services.reddit_client import RedditCollector
    collector = RedditCollector()
    nb = collector.import_reddit_posts(['MC.PA', 'AI.PA'])
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from django.db import transaction
from django.utils import timezone

from app.models import Article, Titre

logger = logging.getLogger(__name__)

# Subreddits FR finance
SUBREDDITS = ['bourse', 'vosfinances', 'investir']
TIMEOUT_SEC = 15
PAUSE_INTER_REQ = 2.0  # Reddit rate limit: 1 req/2s sans OAuth
MAX_POSTS_PAR_SUB = 100


class RedditCollector:
    """Collecteur de posts Reddit via l'API JSON publique."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'PEA-Dashboard/1.0 (by /u/pea_bot)',
            'Accept': 'application/json',
        })
        self._req_count = 0

    @property
    def nb_requetes_session(self) -> int:
        return self._req_count

    # ------------------------------------------------------------------
    # API Reddit JSON
    # ------------------------------------------------------------------

    def _search_subreddit(self, subreddit: str, query: str,
                           sort: str = 'new', limit: int = 25,
                           time_filter: str = 'year') -> list[dict]:
        """
        Recherche dans un subreddit via l'API JSON publique.
        time_filter : hour, day, week, month, year, all
        """
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {
            'q': query,
            'restrict_sr': 'on',
            'sort': sort,
            'limit': min(limit, MAX_POSTS_PAR_SUB),
            't': time_filter,
        }

        try:
            resp = self.session.get(url, params=params, timeout=TIMEOUT_SEC)
            self._req_count += 1

            if resp.status_code == 429:
                logger.warning("Reddit rate limit atteint — pause 10s")
                time.sleep(10)
                return []

            resp.raise_for_status()
            data = resp.json()

            posts = []
            for child in data.get('data', {}).get('children', []):
                post = child.get('data', {})
                posts.append({
                    'title': post.get('title', ''),
                    'url': f"https://www.reddit.com{post.get('permalink', '')}",
                    'selftext': post.get('selftext', '')[:2000],
                    'author': post.get('author', ''),
                    'score': post.get('score', 0),
                    'num_comments': post.get('num_comments', 0),
                    'created_utc': post.get('created_utc', 0),
                    'subreddit': subreddit,
                })

            time.sleep(PAUSE_INTER_REQ)
            return posts

        except Exception as e:
            logger.error("Reddit search r/%s '%s' : %s", subreddit, query[:30], e)
            return []

    # ------------------------------------------------------------------
    # Import en base
    # ------------------------------------------------------------------

    def import_reddit_posts(self, tickers: list[str],
                             historique: bool = False) -> int:
        """
        Pour chaque titre, recherche les posts Reddit mentionnant le nom
        dans les subreddits FR finance.

        historique=True : recherche sur 1 an (time_filter='year').
        historique=False : recherche sur 1 semaine (time_filter='week').
        """
        titres_map = {
            t.ticker: t
            for t in Titre.objects.filter(ticker__in=tickers)
        }

        urls_connues = set(
            Article.objects.filter(source='reddit')
            .values_list('url', flat=True)
        )

        time_filter = 'year' if historique else 'week'
        limit = 50 if historique else 25
        total_crees = 0

        for ticker, titre_obj in titres_map.items():
            nom = titre_obj.nom_court or titre_obj.nom or ticker.split('.')[0]

            a_creer = []
            for subreddit in SUBREDDITS:
                posts = self._search_subreddit(
                    subreddit=subreddit,
                    query=nom,
                    limit=limit,
                    time_filter=time_filter,
                )

                for post in posts:
                    url = (post.get('url') or '')[:500]
                    if not url or url in urls_connues:
                        continue

                    # Vérifier pertinence
                    titre_post = post.get('title', '')
                    if not self._est_pertinent(nom, titre_post):
                        continue

                    created = post.get('created_utc', 0)
                    date_pub = (
                        datetime.utcfromtimestamp(created).replace(tzinfo=timezone.utc)
                        if created else timezone.now()
                    )

                    a_creer.append(Article(
                        titre=titre_obj,
                        date_pub=date_pub,
                        source='reddit',
                        url=url,
                        titre_art=titre_post[:300],
                        extrait=post.get('selftext', '')[:2000],
                        auteur=f"u/{post.get('author', '?')} · r/{post.get('subreddit', '')}",
                    ))
                    urls_connues.add(url)

            if a_creer:
                with transaction.atomic():
                    Article.objects.bulk_create(a_creer, ignore_conflicts=True)
                total_crees += len(a_creer)

            logger.info("Reddit %s : %d posts créés (historique=%s)",
                        ticker, len(a_creer), historique)

        return total_crees

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _est_pertinent(nom_recherche: str, titre_post: str) -> bool:
        """Vérifie que le post mentionne bien le titre."""
        nom_lower = nom_recherche.lower()
        titre_lower = titre_post.lower()

        if nom_lower in titre_lower:
            return True

        mots = [m for m in nom_lower.split() if len(m) >= 3]
        if mots and all(m in titre_lower for m in mots):
            return True

        return False
