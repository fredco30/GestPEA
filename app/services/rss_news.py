"""
app/services/rss_news.py
--------------------------
Collecte d'articles via flux RSS gratuits (sans clé API).
Sources :
  - Google News RSS : actualités financières FR illimitées
  - Boursorama : articles spécialisés bourse FR
  - Zonebourse : analyses et news financières FR

Première exécution : récupère jusqu'à 1 an d'historique.
Exécutions suivantes : uniquement les nouveaux articles.

Usage :
    from app.services.rss_news import RSSCollector
    collector = RSSCollector()
    nb = collector.import_all_sources(['MC.PA', 'AI.PA'])
"""

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

import requests
from django.db import transaction
from django.utils import timezone

from app.models import Article, Titre

logger = logging.getLogger(__name__)

TIMEOUT_SEC = 15
PAUSE_INTER_REQ = 0.5


class RSSCollector:
    """Collecteur d'articles via flux RSS financiers."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'PEA-Dashboard/1.0',
            'Accept': 'application/rss+xml, application/xml, text/xml',
        })
        self._req_count = 0

    @property
    def nb_requetes_session(self) -> int:
        return self._req_count

    # ------------------------------------------------------------------
    # Parsing RSS
    # ------------------------------------------------------------------

    def _fetch_rss(self, url: str) -> list[dict]:
        """Récupère et parse un flux RSS. Retourne une liste d'articles."""
        try:
            resp = self.session.get(url, timeout=TIMEOUT_SEC)
            self._req_count += 1
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            articles = []

            # Format RSS 2.0
            for item in root.iter('item'):
                article = {
                    'title': (item.findtext('title') or '').strip(),
                    'url': (item.findtext('link') or '').strip(),
                    'description': (item.findtext('description') or '').strip(),
                    'pub_date': item.findtext('pubDate'),
                    'source': (item.findtext('source') or '').strip(),
                }
                articles.append(article)

            # Format Atom (fallback)
            if not articles:
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                for entry in root.findall('.//atom:entry', ns):
                    link_el = entry.find('atom:link', ns)
                    article = {
                        'title': (entry.findtext('atom:title', '', ns) or '').strip(),
                        'url': link_el.get('href', '') if link_el is not None else '',
                        'description': (entry.findtext('atom:summary', '', ns) or '').strip(),
                        'pub_date': entry.findtext('atom:published', '', ns) or entry.findtext('atom:updated', '', ns),
                        'source': '',
                    }
                    articles.append(article)

            time.sleep(PAUSE_INTER_REQ)
            return articles

        except Exception as e:
            logger.error("RSS fetch %s : %s", url[:80], e)
            return []

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse une date RSS (RFC 2822 ou ISO 8601)."""
        if not date_str:
            return None
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            pass
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Google News RSS
    # ------------------------------------------------------------------

    def _google_news_urls(self, nom_recherche: str, historique: bool = False) -> list[str]:
        """
        Construit les URLs Google News RSS pour un titre.
        historique=True : recherche sur 1 an (12 requêtes mensuelles).
        historique=False : recherche sur 7 derniers jours.
        """
        from urllib.parse import quote
        base = "https://news.google.com/rss/search"
        query = f"{nom_recherche} bourse action"
        encoded_q = quote(query)

        if not historique:
            return [f"{base}?q={encoded_q}+when:7d&hl=fr&gl=FR&ceid=FR:fr"]

        # Historique 1 an : recherche par trimestres (Google News garde ~1 an)
        urls = []
        now = datetime.now()
        for months_ago in range(0, 12, 3):
            after = (now - timedelta(days=30 * (months_ago + 3))).strftime('%Y-%m-%d')
            before = (now - timedelta(days=30 * months_ago)).strftime('%Y-%m-%d')
            urls.append(
                f"{base}?q={encoded_q}+after:{after}+before:{before}&hl=fr&gl=FR&ceid=FR:fr"
            )
        return urls

    def import_google_news(self, tickers: list[str], historique: bool = False) -> int:
        """
        Importe les articles Google News pour chaque titre.
        historique=True pour la première exécution (1 an).
        """
        titres_map = {
            t.ticker: t
            for t in Titre.objects.filter(ticker__in=tickers)
        }

        urls_connues = set(
            Article.objects.filter(source='google_news')
            .values_list('url', flat=True)
        )

        total_crees = 0

        for ticker, titre_obj in titres_map.items():
            nom = titre_obj.nom_court or titre_obj.nom or ticker.split('.')[0]
            urls = self._google_news_urls(nom, historique=historique)

            a_creer = []
            for rss_url in urls:
                articles = self._fetch_rss(rss_url)

                for raw in articles:
                    url = (raw.get('url') or '')[:500]
                    if not url or url in urls_connues:
                        continue

                    date_pub = self._parse_date(raw.get('pub_date'))
                    if not date_pub:
                        date_pub = timezone.now()

                    titre_art = (raw.get('title') or '')[:300]
                    # Vérifier pertinence : le nom doit apparaître dans le titre
                    if not self._est_pertinent(nom, titre_art):
                        continue

                    a_creer.append(Article(
                        titre=titre_obj,
                        date_pub=date_pub,
                        source='google_news',
                        url=url,
                        titre_art=titre_art,
                        extrait=(raw.get('description') or '')[:2000],
                        auteur=(raw.get('source') or 'Google News')[:100],
                    ))
                    urls_connues.add(url)

            if a_creer:
                with transaction.atomic():
                    Article.objects.bulk_create(a_creer, ignore_conflicts=True)
                total_crees += len(a_creer)

            logger.info("Google News %s : %d articles créés (historique=%s)",
                        ticker, len(a_creer), historique)

        return total_crees

    # ------------------------------------------------------------------
    # Boursorama RSS
    # ------------------------------------------------------------------

    def import_boursorama(self, tickers: list[str]) -> int:
        """
        Importe les articles Boursorama via leur flux RSS.
        Boursorama utilise le code ISIN ou le mnémo pour ses flux.
        """
        titres_map = {
            t.ticker: t
            for t in Titre.objects.filter(ticker__in=tickers)
        }

        urls_connues = set(
            Article.objects.filter(source='boursorama')
            .values_list('url', flat=True)
        )

        total_crees = 0

        for ticker, titre_obj in titres_map.items():
            # Boursorama utilise le code sans exchange (ex: "1rPAB" pour AB.PA)
            code = ticker.split('.')[0]
            isin = titre_obj.isin

            # Essayer le flux RSS Boursorama par ISIN puis par code
            rss_urls = []
            if isin:
                rss_urls.append(f"https://www.boursorama.com/bourse/action/graph/rss/actualites/{isin}")
            rss_urls.append(f"https://www.boursorama.com/bourse/action/graph/rss/actualites/1rP{code}")

            a_creer = []
            for rss_url in rss_urls:
                articles = self._fetch_rss(rss_url)
                if not articles:
                    continue

                for raw in articles:
                    url = (raw.get('url') or '')[:500]
                    if not url or url in urls_connues:
                        continue

                    date_pub = self._parse_date(raw.get('pub_date'))
                    if not date_pub:
                        date_pub = timezone.now()

                    a_creer.append(Article(
                        titre=titre_obj,
                        date_pub=date_pub,
                        source='boursorama',
                        url=url,
                        titre_art=(raw.get('title') or '')[:300],
                        extrait=(raw.get('description') or '')[:2000],
                        auteur='Boursorama',
                    ))
                    urls_connues.add(url)

                break  # Premier flux qui fonctionne suffit

            if a_creer:
                with transaction.atomic():
                    Article.objects.bulk_create(a_creer, ignore_conflicts=True)
                total_crees += len(a_creer)

            logger.info("Boursorama %s : %d articles créés", ticker, len(a_creer))

        return total_crees

    # ------------------------------------------------------------------
    # Zonebourse RSS
    # ------------------------------------------------------------------

    def import_zonebourse(self, tickers: list[str]) -> int:
        """
        Importe les articles Zonebourse via flux RSS recherche.
        """
        titres_map = {
            t.ticker: t
            for t in Titre.objects.filter(ticker__in=tickers)
        }

        urls_connues = set(
            Article.objects.filter(source='zonebourse')
            .values_list('url', flat=True)
        )

        total_crees = 0

        for ticker, titre_obj in titres_map.items():
            nom = titre_obj.nom_court or titre_obj.nom or ticker.split('.')[0]
            from urllib.parse import quote
            rss_url = f"https://www.zonebourse.com/recherche/rss/?q={quote(nom)}"

            articles = self._fetch_rss(rss_url)
            a_creer = []

            for raw in articles:
                url = (raw.get('url') or '')[:500]
                if not url or url in urls_connues:
                    continue

                date_pub = self._parse_date(raw.get('pub_date'))
                if not date_pub:
                    date_pub = timezone.now()

                a_creer.append(Article(
                    titre=titre_obj,
                    date_pub=date_pub,
                    source='zonebourse',
                    url=url,
                    titre_art=(raw.get('title') or '')[:300],
                    extrait=(raw.get('description') or '')[:2000],
                    auteur='Zonebourse',
                ))
                urls_connues.add(url)

            if a_creer:
                with transaction.atomic():
                    Article.objects.bulk_create(a_creer, ignore_conflicts=True)
                total_crees += len(a_creer)

            logger.info("Zonebourse %s : %d articles créés", ticker, len(a_creer))

        return total_crees

    # ------------------------------------------------------------------
    # Import global toutes sources RSS
    # ------------------------------------------------------------------

    def import_all_sources(self, tickers: list[str], historique: bool = False) -> dict:
        """
        Lance l'import depuis toutes les sources RSS.
        historique=True pour le premier lancement (Google News 1 an).
        """
        resultats = {}

        # Google News (toujours disponible)
        resultats['google_news'] = self.import_google_news(tickers, historique=historique)

        # Boursorama
        resultats['boursorama'] = self.import_boursorama(tickers)

        # Zonebourse
        resultats['zonebourse'] = self.import_zonebourse(tickers)

        total = sum(resultats.values())
        logger.info("RSS total : %d articles — %s", total, resultats)
        return resultats

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _est_pertinent(nom_recherche: str, titre_article: str) -> bool:
        """Vérifie que l'article mentionne bien le titre recherché."""
        nom_lower = nom_recherche.lower()
        titre_lower = titre_article.lower()

        # Recherche exacte du nom
        if nom_lower in titre_lower:
            return True

        # Recherche de chaque mot du nom (min 3 chars)
        mots = [m for m in nom_lower.split() if len(m) >= 3]
        if mots and all(m in titre_lower for m in mots):
            return True

        return False
