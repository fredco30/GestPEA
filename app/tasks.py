"""
app/tasks.py
------------
Tâches Celery unifiées pour le projet PEA.

Planning (défini dans settings.py CELERY_BEAT_SCHEDULE) :
  fetch_cours_eod_task        → chaque soir 18h30, lun-ven
  fetch_fondamentaux_lot_task → lun+mer (lot A) / mar+jeu (lot B) à 19h00
  fetch_news_task             → chaque soir 20h00, lun-ven
  run_indicateurs_task        → chaque soir 21h00, lun-ven
  update_eligibles_pea_task   → 1er vendredi du mois, 8h00
  digest_hebdomadaire_task    → vendredi soir 19h00
  import_historique_task      → déclenchée manuellement à l'ajout d'un titre
"""

import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. COURS EOD — chaque soir 18h30
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def fetch_cours_eod_task(self):
    """
    Récupère la bougie EOD du jour pour TOUS les titres actifs.
    Utilise maj_cours_du_jour() en boucle (1 requête par titre).
    """
    from app.models import Titre
    from app.services.eodhd import EODHDClient

    try:
        tickers = list(
            Titre.objects.filter(actif=True, eligible_pea=True)
            .exclude(statut='archive')
            .values_list('ticker', flat=True)
        )

        if not tickers:
            logger.info("[Task] fetch_cours_eod : aucun titre actif.")
            return {'status': 'skip', 'raison': 'aucun titre'}

        client = EODHDClient()

        if not client.bourse_ouverte():
            logger.info("[Task] fetch_cours_eod : marché fermé aujourd'hui.")
            return {'status': 'skip', 'raison': 'marché fermé'}

        logger.info(f"[Task] fetch_cours_eod : {len(tickers)} tickers → {tickers}")

        ok, ko = [], []
        for ticker in tickers:
            try:
                prix = client.maj_cours_du_jour(ticker)
                if prix:
                    ok.append(ticker)
            except Exception as e:
                logger.error(f"[Task] Erreur cours {ticker} : {e}")
                ko.append(ticker)

        return {
            'status': 'ok',
            'ok': ok,
            'ko': ko,
            'requetes': client.nb_requetes_session,
        }

    except Exception as exc:
        logger.error(f"[Task] fetch_cours_eod — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 2. FONDAMENTAUX — lot A (lun/mer) ou lot B (mar/jeu) à 19h00
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def fetch_fondamentaux_lot_task(self, lot: str):
    """
    Récupère les fondamentaux pour le lot A ou B.
    Rotation : lot A lundi+mercredi, lot B mardi+jeudi.
    Source principale : EODHD. Complément : FMP (champs manquants).
    """
    from app.models import Titre
    from app.services.eodhd import EODHDClient
    from app.services.fmp import FMPClient

    if lot not in ('A', 'B'):
        logger.error(f"[Task] fetch_fondamentaux_lot : lot invalide '{lot}'")
        return {'status': 'error', 'raison': f"lot invalide : {lot}"}

    try:
        tickers = list(
            Titre.objects.filter(actif=True, lot=lot, eligible_pea=True)
            .exclude(statut='archive')
            .values_list('ticker', flat=True)
        )

        if not tickers:
            logger.info(f"[Task] fetch_fondamentaux lot {lot} : aucun titre.")
            return {'status': 'skip', 'raison': 'aucun titre dans ce lot'}

        logger.info(f"[Task] fetch_fondamentaux lot {lot} : {len(tickers)} tickers")

        # --- Source 1 : EODHD (principale) ---
        client_eodhd = EODHDClient()
        succes_eodhd = 0
        echecs = []

        for ticker in tickers:
            try:
                fond = client_eodhd.maj_fondamentaux(ticker)
                if fond:
                    succes_eodhd += 1
            except Exception as e:
                logger.error(f"[Task] Erreur fondamentaux EODHD {ticker} : {e}")
                echecs.append(ticker)

        # --- Source 2 : FMP (complément, champs manquants) ---
        succes_fmp = 0
        from django.conf import settings
        if settings.FMP_API_KEY:
            try:
                client_fmp = FMPClient()
                for ticker in tickers:
                    try:
                        fond = client_fmp.maj_fondamentaux(ticker)
                        if fond:
                            succes_fmp += 1
                    except Exception as e:
                        logger.error(f"[Task] Erreur fondamentaux FMP {ticker} : {e}")
                client_fmp.maj_quota()
            except Exception as e:
                logger.error(f"[Task] FMP global — erreur : {e}")

        return {
            'status': 'ok',
            'lot': lot,
            'succes_eodhd': succes_eodhd,
            'succes_fmp': succes_fmp,
            'echecs': echecs,
            'requetes_eodhd': client_eodhd.nb_requetes_session,
        }

    except Exception as exc:
        logger.error(f"[Task] fetch_fondamentaux lot {lot} — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 3. NEWS MUTUALISÉE — chaque soir 20h00
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def fetch_news_task(self):
    """
    Collecte les actualités pour tous les titres actifs via EODHD + NewsAPI.
    Déclenche ensuite le scoring LLM sur les articles non scorés.
    """
    from app.models import Article, Titre
    from app.services.eodhd import EODHDClient
    from app.services.newsapi_client import NewsAPIClient

    try:
        tickers = list(
            Titre.objects.filter(actif=True, eligible_pea=True)
            .exclude(statut='archive')
            .values_list('ticker', flat=True)
        )

        if not tickers:
            return {'status': 'skip', 'raison': 'aucun titre'}

        nb_total = 0

        # --- Source 1 : EODHD (news mutualisée, 1 requête) ---
        client_eodhd = EODHDClient()
        nb_eodhd = client_eodhd.import_news(tickers)
        nb_total += nb_eodhd
        logger.info(f"[Task] fetch_news EODHD : {nb_eodhd} articles créés")

        # --- Source 2 : NewsAPI (recherche par titre, presse FR) ---
        from django.conf import settings
        if settings.NEWSAPI_KEY:
            try:
                client_newsapi = NewsAPIClient()
                nb_newsapi = client_newsapi.import_news_pour_titres(tickers)
                nb_total += nb_newsapi
                client_newsapi.maj_quota()
                logger.info(f"[Task] fetch_news NewsAPI : {nb_newsapi} articles créés")
            except Exception as e:
                logger.error(f"[Task] fetch_news NewsAPI — erreur : {e}")

        # --- Source 3 : RSS (Google News + Boursorama + Zonebourse) ---
        try:
            from app.services.rss_news import RSSCollector
            rss = RSSCollector()
            rss_result = rss.import_all_sources(tickers, historique=False)
            nb_rss = sum(rss_result.values())
            nb_total += nb_rss
            logger.info(f"[Task] fetch_news RSS : {nb_rss} articles — {rss_result}")
        except Exception as e:
            logger.error(f"[Task] fetch_news RSS — erreur : {e}")

        # --- Source 4 : Reddit (r/bourse, r/vosfinances) ---
        try:
            from app.services.reddit_client import RedditCollector
            reddit = RedditCollector()
            nb_reddit = reddit.import_reddit_posts(tickers, historique=False)
            nb_total += nb_reddit
            logger.info(f"[Task] fetch_news Reddit : {nb_reddit} posts créés")
        except Exception as e:
            logger.error(f"[Task] fetch_news Reddit — erreur : {e}")

        # Déclencher le scoring LLM sur les articles non scorés
        if nb_total > 0:
            article_ids = list(
                Article.objects.filter(score_sentiment__isnull=True)
                .values_list('id', flat=True)
            )
            if article_ids:
                scorer_articles_task.delay(article_ids)
                logger.info(f"[Task] fetch_news : {nb_total} articles créés → scoring LLM lancé")

        return {
            'status': 'ok',
            'articles_crees': nb_total,
            'sources': {'eodhd': nb_eodhd, 'newsapi': nb_total - nb_eodhd},
            'requetes_eodhd': client_eodhd.nb_requetes_session,
        }

    except Exception as exc:
        logger.error(f"[Task] fetch_news — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 4. CALCUL INDICATEURS TECHNIQUES — chaque soir 21h00
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=1)
def run_indicateurs_task(self):
    """
    Calcule RSI, MACD, MM20/50/200, Bollinger et ratio volume
    sur l'historique de chaque titre actif.
    """
    from app.models import Titre
    from app.services.indicators import calculate_indicators

    try:
        titres = Titre.objects.filter(actif=True).exclude(statut='archive')
        total = 0

        for titre in titres:
            nb = calculate_indicators(titre)
            total += nb
            if nb:
                logger.debug(f"[Task] Indicateurs {titre.ticker} : {nb} bougies recalculées")

        logger.info(f"[Task] run_indicateurs : {total} bougies mises à jour au total")

        # Enchaîner la détection des signaux
        detect_signaux_task.delay()

        return {'status': 'ok', 'bougies_maj': total}

    except Exception as exc:
        logger.error(f"[Task] run_indicateurs — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 5. DÉTECTION DES SIGNAUX — après run_indicateurs
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=1)
def detect_signaux_task(self):
    """
    Parcourt les dernières bougies et détecte les signaux techniques.
    Enchaîne run_confluence_task si des signaux sont trouvés.
    """
    from datetime import date
    from app.models import PrixJournalier, Signal, Titre

    aujourd_hui = date.today()
    nb_signaux = 0

    titres = Titre.objects.filter(actif=True).exclude(statut='archive')

    for titre in titres:
        bougies = list(
            titre.prix_journaliers
            .filter(date__lte=aujourd_hui)
            .order_by('-date')[:2]
        )

        if not bougies:
            continue

        b_today = bougies[0]
        b_prev = bougies[1] if len(bougies) > 1 else None

        signaux_detectes = []

        # --- RSI ---
        if b_today.rsi_14 is not None:
            rsi = float(b_today.rsi_14)
            if rsi < 40:
                signaux_detectes.append({
                    'type_signal': 'rsi_survente',
                    'direction': 'haussier',
                    'valeur': rsi,
                    'description': f"RSI(14) à {rsi:.1f} — zone de survente (<40)",
                })
            elif rsi > 65:
                signaux_detectes.append({
                    'type_signal': 'rsi_surachat',
                    'direction': 'baissier',
                    'valeur': rsi,
                    'description': f"RSI(14) à {rsi:.1f} — zone de surachat (>65)",
                })

        # --- MACD croisement ---
        if b_today.macd_hist is not None and b_prev and b_prev.macd_hist is not None:
            hist_today = float(b_today.macd_hist)
            hist_prev = float(b_prev.macd_hist)
            if hist_prev < 0 and hist_today >= 0:
                signaux_detectes.append({
                    'type_signal': 'macd_haussier',
                    'direction': 'haussier',
                    'valeur': float(b_today.macd) if b_today.macd else None,
                    'description': "MACD croisement haussier (histogramme passe positif)",
                })
            elif hist_prev > 0 and hist_today <= 0:
                signaux_detectes.append({
                    'type_signal': 'macd_baissier',
                    'direction': 'baissier',
                    'valeur': float(b_today.macd) if b_today.macd else None,
                    'description': "MACD croisement baissier (histogramme passe négatif)",
                })

        # --- Golden / Death cross MM20/MM50 ---
        if (b_today.mm_20 is not None and b_today.mm_50 is not None and
                b_prev and b_prev.mm_20 is not None and b_prev.mm_50 is not None):
            mm20_today = float(b_today.mm_20)
            mm50_today = float(b_today.mm_50)
            mm20_prev = float(b_prev.mm_20)
            mm50_prev = float(b_prev.mm_50)

            if mm20_prev < mm50_prev and mm20_today >= mm50_today:
                signaux_detectes.append({
                    'type_signal': 'mm_golden_cross',
                    'direction': 'haussier',
                    'valeur': mm20_today,
                    'description': f"Golden cross MM20({mm20_today:.2f}) > MM50({mm50_today:.2f})",
                })
            elif mm20_prev > mm50_prev and mm20_today <= mm50_today:
                signaux_detectes.append({
                    'type_signal': 'mm_death_cross',
                    'direction': 'baissier',
                    'valeur': mm20_today,
                    'description': f"Death cross MM20({mm20_today:.2f}) < MM50({mm50_today:.2f})",
                })

        # --- Bollinger ---
        if b_today.boll_inf is not None and b_today.boll_sup is not None:
            cloture = float(b_today.cloture)
            boll_inf = float(b_today.boll_inf)
            boll_sup = float(b_today.boll_sup)

            if cloture <= boll_inf * 1.01:
                signaux_detectes.append({
                    'type_signal': 'boll_inf',
                    'direction': 'haussier',
                    'valeur': boll_inf,
                    'description': f"Prix ({cloture:.2f}) proche bande Bollinger basse ({boll_inf:.2f})",
                })
            elif cloture >= boll_sup * 0.99:
                signaux_detectes.append({
                    'type_signal': 'boll_sup',
                    'direction': 'baissier',
                    'valeur': boll_sup,
                    'description': f"Prix ({cloture:.2f}) proche bande Bollinger haute ({boll_sup:.2f})",
                })

        # --- Pic de volume ---
        if b_today.volume_ratio is not None:
            ratio = float(b_today.volume_ratio)
            if ratio >= 1.5:
                signaux_detectes.append({
                    'type_signal': 'volume_spike',
                    'direction': 'haussier' if float(b_today.cloture) >= float(b_today.ouverture) else 'baissier',
                    'valeur': ratio,
                    'description': f"Volume anormal : {ratio:.1f}x la moyenne 20 jours",
                })

        # Créer les signaux en base (éviter doublons du jour)
        for s in signaux_detectes:
            _, created = Signal.objects.get_or_create(
                titre=titre,
                date=aujourd_hui,
                type_signal=s['type_signal'],
                defaults={
                    'direction': s['direction'],
                    'valeur': s['valeur'],
                    'description': s['description'],
                    'actif': True,
                }
            )
            if created:
                nb_signaux += 1

    logger.info(f"[Task] detect_signaux : {nb_signaux} nouveaux signaux détectés")

    if nb_signaux > 0:
        run_confluence_task.delay()

    return {'status': 'ok', 'signaux': nb_signaux}


# ---------------------------------------------------------------------------
# 6. MOTEUR DE CONFLUENCE — après détection signaux
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=1)
def run_confluence_task(self):
    """
    Pour chaque titre ayant des signaux actifs aujourd'hui,
    calcule le score de confluence et crée une Alerte si le seuil est atteint.
    """
    from datetime import date
    from app.models import AlerteConfig, Signal, Titre

    aujourd_hui = date.today()
    alertes_creees = 0

    titres_avec_signaux = (
        Signal.objects.filter(date=aujourd_hui, actif=True)
        .values_list('titre_id', flat=True)
        .distinct()
    )

    for titre_id in titres_avec_signaux:
        try:
            titre = Titre.objects.get(pk=titre_id)
            config = AlerteConfig.objects.get(titre=titre, actif=True)
        except (Titre.DoesNotExist, AlerteConfig.DoesNotExist):
            continue

        signaux = Signal.objects.filter(titre=titre, date=aujourd_hui, actif=True)

        score = _calculer_score_confluence(titre, signaux)

        if score < float(config.score_min_declenchement):
            continue

        # Niveau
        if score >= 8:
            niveau = 'forte'
        elif score >= 5:
            niveau = 'moderee'
        else:
            niveau = 'surveillance'

        # Cours et sentiment au moment de l'alerte
        derniere_bougie = titre.prix_journaliers.order_by('-date').first()
        dernier_sentiment = (
            titre.scores_sentiment
            .filter(source='global')
            .order_by('-date')
            .first()
        )

        from app.models import Alerte
        alerte, created = Alerte.objects.get_or_create(
            titre=titre,
            date_signal=aujourd_hui,
            defaults={
                'score_confluence': score,
                'niveau': niveau,
                'cours_au_signal': derniere_bougie.cloture if derniere_bougie else 0,
                'rsi_au_signal': derniere_bougie.rsi_14 if derniere_bougie else None,
                'sentiment_au_signal': dernier_sentiment.score if dernier_sentiment else None,
                'texte_ia': '...',
                'statut': 'nouvelle',
            }
        )

        if created:
            alerte.signaux.set(signaux)
            alertes_creees += 1
            logger.info(
                f"[Confluence] Alerte {niveau} créée pour {titre.ticker} "
                f"(score {score:.1f})"
            )
            # Générer le texte IA puis notifier
            scorer_alerte_task.delay(alerte.id)

    logger.info(f"[Task] run_confluence : {alertes_creees} alertes créées")

    # Enchaîner le calcul des scores de conviction
    calculer_convictions_task.delay()

    return {'status': 'ok', 'alertes': alertes_creees}


def _calculer_score_confluence(titre, signaux) -> float:
    """Score de confluence 0-10 (60% fondamentaux / 40% technique)."""
    from app.models import Fondamentaux, ProfilInvestisseur

    profil = ProfilInvestisseur.objects.first()
    poids_fond = (profil.poids_fondamentaux / 100) if profil else 0.6
    poids_tech = (profil.poids_technique / 100) if profil else 0.4

    nb_haussiers = signaux.filter(direction='haussier').count()
    nb_baissiers = signaux.filter(direction='baissier').count()
    nb_total = signaux.count()

    if nb_total == 0:
        return 0.0

    score_tech = (nb_haussiers / nb_total) * 10

    score_fond = 5.0
    dernier_fond = (
        Fondamentaux.objects.filter(titre=titre)
        .order_by('-date_maj')
        .first()
    )
    if dernier_fond and dernier_fond.score_qualite is not None:
        score_fond = float(dernier_fond.score_qualite)

    score = (score_fond * poids_fond) + (score_tech * poids_tech)

    if nb_total >= 3 and nb_haussiers > nb_baissiers:
        score = min(10.0, score + 0.5)

    return round(score, 1)


# ---------------------------------------------------------------------------
# 6b. SCORE DE CONVICTION IA — après confluence
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=1)
def calculer_convictions_task(self):
    """
    Calcule le score de conviction IA (0-100) pour tous les titres actifs.
    Enchaîné automatiquement après run_confluence_task.
    """
    from app.models import Titre
    from app.services.conviction import calculer_score_conviction

    try:
        tickers = list(
            Titre.objects.filter(actif=True)
            .exclude(statut='archive')
            .values_list('ticker', flat=True)
        )

        ok, ko = [], []
        for ticker in tickers:
            try:
                result = calculer_score_conviction(ticker)
                if result:
                    ok.append(f"{ticker}={result['score']}")
                else:
                    ko.append(f"{ticker}=no_data")
            except Exception as e:
                logger.error(f"[Task] conviction {ticker} : {e}")
                ko.append(ticker)

        logger.info(f"[Task] calculer_convictions : {len(ok)} OK, {len(ko)} KO")
        return {'status': 'ok', 'ok': ok, 'ko': ko}

    except Exception as exc:
        logger.error(f"[Task] calculer_convictions — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 7. SCORING LLM — articles et alertes
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def scorer_articles_task(self, article_ids: list[int]):
    """Score le sentiment des articles via Claude API."""
    from app.services.scoring_llm import scorer_articles
    try:
        scorer_articles(article_ids)
    except Exception as exc:
        logger.error(f"[Task] scorer_articles — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def scorer_alerte_task(self, alerte_id: int):
    """
    Génère le texte narratif d'une alerte via Claude API.
    Puis déclenche l'envoi des notifications.
    """
    from app.services.scoring_llm import generer_texte_alerte
    try:
        generer_texte_alerte(alerte_id)
        # Déclencher les notifications après génération du texte
        notifier_alerte_task.delay(alerte_id)
    except Exception as exc:
        logger.error(f"[Task] scorer_alerte — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 8. NOTIFICATIONS
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def notifier_alerte_task(self, alerte_id: int):
    """Envoie une alerte sur tous les canaux configurés (email, Telegram, webhook)."""
    from app.services.notifications import notifier_alerte
    try:
        resultats = notifier_alerte(alerte_id)
        return {'status': 'ok', 'alerte_id': alerte_id, 'canaux': resultats}
    except Exception as exc:
        logger.error(f"[Task] notifier_alerte {alerte_id} — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=1)
def digest_hebdomadaire_task(self):
    """Génère et envoie le digest hebdomadaire (vendredi soir 19h00)."""
    from app.services.scoring_llm import generer_digest_hebdomadaire
    from app.services.notifications import notifier_digest
    try:
        texte = generer_digest_hebdomadaire()
        resultats = notifier_digest(texte)
        logger.info(f"[Task] digest_hebdomadaire envoyé — canaux : {resultats}")
        return {'status': 'ok', 'canaux': resultats}
    except Exception as exc:
        logger.error(f"[Task] digest_hebdomadaire — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 9. IMPORT HISTORIQUE — déclenché manuellement
# ---------------------------------------------------------------------------

@shared_task(bind=True)
def import_historique_task(self, ticker: str):
    """
    Importe l'historique OHLCV complet d'un titre depuis EODHD.
    Exemple : import_historique_task.delay('MC.PA')
    """
    from app.services.eodhd import EODHDClient
    try:
        client = EODHDClient()
        nb = client.import_historique_bulk(ticker)
        logger.info(f"[Task] import_historique {ticker} : {nb} bougies importées")

        if nb > 0:
            from app.models import Titre
            titre = Titre.objects.get(ticker=ticker)
            from app.services.indicators import calculate_indicators
            calculate_indicators(titre)

        return {'status': 'ok', 'ticker': ticker, 'bougies': nb}

    except Exception as exc:
        logger.error(f"[Task] import_historique {ticker} — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 10. SCREENER PEA — 1er vendredi du mois
# ---------------------------------------------------------------------------

@shared_task(bind=True)
def update_eligibles_pea_task(self):
    """Met à jour l'éligibilité PEA de tous les titres en base."""
    from app.services.eodhd import EODHDClient
    try:
        client = EODHDClient()
        stats = client.maj_eligibilite_tous_titres()
        return {'status': 'ok', 'stats': stats, 'requetes': client.nb_requetes_session}
    except Exception as exc:
        logger.error(f"[Task] update_eligibles_pea — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 11. ANALYSE COMPLETE — déclenchée à la création d'un titre
# ---------------------------------------------------------------------------

@shared_task(bind=True)
def analyse_complete_task(self, ticker: str):
    """
    Analyse complète d'un titre nouvellement ajouté :
      1. Import historique OHLCV (EODHD)
      2. Calcul indicateurs techniques
      3. Collecte news toutes sources (historique 1 an)
      4. Scoring sentiment articles (Mistral)
      5. Sentiment mixte + rapport IA
    """
    import os
    os.environ.setdefault('NUMBA_DISABLE_JIT', '1')

    from app.models import Article, Titre
    from app.services.eodhd import EODHDClient

    try:
        titre = Titre.objects.get(ticker=ticker)
    except Titre.DoesNotExist:
        logger.error(f"[Task] analyse_complete : {ticker} introuvable")
        return {'status': 'error', 'raison': 'titre introuvable'}

    resultats = {'ticker': ticker}

    # 1. Import historique OHLCV
    try:
        client = EODHDClient()
        nb = client.import_historique_bulk(ticker)
        resultats['bougies'] = nb
        logger.info(f"[Task] analyse_complete {ticker} : {nb} bougies importées")
    except Exception as e:
        logger.error(f"[Task] analyse_complete {ticker} import : {e}")
        resultats['bougies'] = 0

    # 2. Indicateurs techniques
    try:
        from app.services.indicators import calculate_indicators
        calculate_indicators(titre)
        resultats['indicateurs'] = 'ok'
    except Exception as e:
        logger.error(f"[Task] analyse_complete {ticker} indicateurs : {e}")
        resultats['indicateurs'] = str(e)

    # 3. Collecte news toutes sources (historique 1 an)
    nb_articles = 0

    # NewsAPI (7 jours max en gratuit)
    try:
        from django.conf import settings
        if settings.NEWSAPI_KEY:
            from app.services.newsapi_client import NewsAPIClient
            newsapi = NewsAPIClient()
            nb_articles += newsapi.import_news_pour_titres([ticker])
            newsapi.maj_quota()
    except Exception as e:
        logger.error(f"[Task] analyse_complete {ticker} newsapi : {e}")

    # RSS (Google News 1 an + Boursorama + Zonebourse)
    try:
        from app.services.rss_news import RSSCollector
        rss = RSSCollector()
        rss_result = rss.import_all_sources([ticker], historique=True)
        nb_articles += sum(rss_result.values())
    except Exception as e:
        logger.error(f"[Task] analyse_complete {ticker} rss : {e}")

    # Reddit (1 an)
    try:
        from app.services.reddit_client import RedditCollector
        reddit = RedditCollector()
        nb_articles += reddit.import_reddit_posts([ticker], historique=True)
    except Exception as e:
        logger.error(f"[Task] analyse_complete {ticker} reddit : {e}")

    resultats['articles'] = nb_articles

    # 4. Scoring sentiment
    try:
        ids = list(
            Article.objects.filter(titre=titre, score_sentiment__isnull=True)
            .values_list('id', flat=True)
        )
        if ids:
            from app.services.scoring_llm import scorer_articles
            scorer_articles(ids)
        resultats['scoring'] = len(ids)
    except Exception as e:
        logger.error(f"[Task] analyse_complete {ticker} scoring : {e}")

    # 5. Sentiment mixte + rapport IA
    try:
        from app.services.scoring_llm import generer_sentiment_mixte
        generer_sentiment_mixte(ticker)
        resultats['sentiment'] = 'ok'
    except Exception as e:
        logger.error(f"[Task] analyse_complete {ticker} sentiment : {e}")

    logger.info(f"[Task] analyse_complete {ticker} terminée : {resultats}")
    return resultats


# ---------------------------------------------------------------------------
# 12. NEWS SOURCES GRATUITES — 9h00 et 13h00 lun-ven
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=1)
def fetch_news_gratuites_task(self):
    """
    Collecte les actualités depuis les sources gratuites illimitées :
      - Google News RSS (7 derniers jours)
      - Boursorama RSS
      - Zonebourse RSS
      - Reddit (1 semaine)
    Puis score les nouveaux articles et met à jour le sentiment.
    """
    from app.models import Article, Titre

    try:
        tickers = list(
            Titre.objects.filter(actif=True)
            .exclude(statut='archive')
            .values_list('ticker', flat=True)
        )
        if not tickers:
            return {'status': 'skip', 'raison': 'aucun titre'}

        nb_total = 0

        # RSS
        try:
            from app.services.rss_news import RSSCollector
            rss = RSSCollector()
            rss_result = rss.import_all_sources(tickers, historique=False)
            nb_rss = sum(rss_result.values())
            nb_total += nb_rss
            logger.info(f"[Task] news_gratuites RSS : {nb_rss} — {rss_result}")
        except Exception as e:
            logger.error(f"[Task] news_gratuites RSS : {e}")

        # Reddit
        try:
            from app.services.reddit_client import RedditCollector
            reddit = RedditCollector()
            nb_reddit = reddit.import_reddit_posts(tickers, historique=False)
            nb_total += nb_reddit
            logger.info(f"[Task] news_gratuites Reddit : {nb_reddit}")
        except Exception as e:
            logger.error(f"[Task] news_gratuites Reddit : {e}")

        # Scorer les nouveaux articles
        if nb_total > 0:
            ids = list(
                Article.objects.filter(score_sentiment__isnull=True)
                .values_list('id', flat=True)
            )
            if ids:
                from app.services.scoring_llm import scorer_articles
                scorer_articles(ids)
                logger.info(f"[Task] news_gratuites : {len(ids)} articles scorés")

            # Mettre à jour le sentiment mixte
            for ticker in tickers:
                try:
                    from app.services.scoring_llm import generer_sentiment_mixte
                    generer_sentiment_mixte(ticker)
                except Exception as e:
                    logger.error(f"[Task] news_gratuites sentiment {ticker} : {e}")

        return {'status': 'ok', 'articles': nb_total}

    except Exception as exc:
        logger.error(f"[Task] news_gratuites — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)
