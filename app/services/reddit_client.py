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

        DÉSACTIVÉ : Reddit bloque les requêtes sans OAuth depuis 2024.
        L'API JSON publique retourne systématiquement 403.
        Les sources Google News + NewsAPI couvrent largement les besoins.
        """
        logger.info("Reddit désactivé (API publique bloquée 403). Utiliser Google News + NewsAPI.")
        return 0

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
