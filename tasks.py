"""
tasks/fetch_cours.py
--------------------
Tâches Celery pour la collecte des données de marché.

Planning (défini dans settings.py CELERY_BEAT_SCHEDULE) :

  fetch_cours_eod_task        → chaque soir 18h30, lun-ven
  fetch_fondamentaux_lot_task → lun+mer (lot A) / mar+jeu (lot B) à 19h00
  fetch_news_task             → chaque soir 20h00, lun-ven
  run_indicateurs_task        → chaque soir 21h00, lun-ven (après cours + calcul)
  update_eligibles_pea_task   → 1er vendredi du mois, 8h00
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
    Récupère la bougie EOD du jour pour TOUS les titres actifs (portefeuille + surveillance).
    1 seul appel API mutualisé (batch EODHD).
    Planifié : lun-ven à 18h30 (après clôture Euronext 17h35).
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

        logger.info(f"[Task] fetch_cours_eod : {len(tickers)} tickers → {tickers}")

        client = EODHDClient()
        resultats = client.fetch_cours_eod(tickers)

        return {
            'status':    'ok',
            'tickers':   len(tickers),
            'mis_a_jour': len(resultats),
            'quota':     client.statut_quota(),
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
    1 requête API par titre du lot.

    Argument : lot = 'A' ou 'B'
    """
    from app.models import Titre
    from app.services.eodhd import EODHDClient

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

        client  = EODHDClient()
        succes  = 0
        echecs  = []

        for ticker in tickers:
            # Vérifier quota avant chaque appel
            quota = client.statut_quota()
            if quota['restants'] <= 0:
                logger.warning(
                    f"[Task] Quota EODHD épuisé après {succes} fondamentaux. "
                    f"Reprise demain."
                )
                break

            ok = client.fetch_fondamentaux(ticker)
            if ok:
                succes += 1
            else:
                echecs.append(ticker)

        return {
            'status':  'ok',
            'lot':     lot,
            'succes':  succes,
            'echecs':  echecs,
            'quota':   client.statut_quota(),
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
    Collecte les actualités pour tous les titres actifs en une seule requête EODHD.
    Déclenche ensuite le scoring LLM sur les articles non scorés.
    Planifié : lun-ven à 20h00.
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
            return {'status': 'skip', 'raison': 'aucun titre'}

        client = EODHDClient()
        articles = client.fetch_news(tickers, limit=30)

        # Déclencher le scoring LLM en tâche asynchrone séparée
        if articles:
            ids = [a.id for a in articles]
            scorer_articles_task.delay(ids)
            logger.info(f"[Task] fetch_news : {len(articles)} articles → scoring LLM lancé")

        return {
            'status':            'ok',
            'articles_collectes': len(articles),
            'quota':             client.statut_quota(),
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
    Stocke les résultats dans PrixJournalier (champs pré-calculés).
    Planifié : lun-ven à 21h00 (après fetch_cours_eod).
    """
    from app.models import Titre
    from app.services.indicators import calculate_indicators

    try:
        titres = Titre.objects.filter(actif=True).exclude(statut='archive')
        total  = 0

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
    Parcourt les dernières bougies et détecte les signaux techniques :
    croisements MM, zones RSI, signaux MACD, proximité Bollinger, pics volume.
    Crée les objets Signal en base.
    Enchaîne run_confluence_task si des signaux sont trouvés.
    """
    from datetime import date
    from app.models import PrixJournalier, Signal, Titre

    aujourd_hui = date.today()
    nb_signaux  = 0

    titres = Titre.objects.filter(actif=True).exclude(statut='archive')

    for titre in titres:
        # Récupérer les 2 dernières bougies pour détecter les croisements
        bougies = list(
            titre.prix_journaliers
            .filter(date__lte=aujourd_hui)
            .order_by('-date')[:2]
        )

        if not bougies:
            continue

        b_today = bougies[0]
        b_prev  = bougies[1] if len(bougies) > 1 else None

        signaux_detectes = []

        # --- RSI ---
        if b_today.rsi_14 is not None:
            rsi = float(b_today.rsi_14)
            if rsi < 40:
                signaux_detectes.append({
                    'type_signal': 'rsi_survente',
                    'direction':   'haussier',
                    'valeur':      rsi,
                    'description': f"RSI(14) à {rsi:.1f} — zone de survente (<40)",
                })
            elif rsi > 65:
                signaux_detectes.append({
                    'type_signal': 'rsi_surachat',
                    'direction':   'baissier',
                    'valeur':      rsi,
                    'description': f"RSI(14) à {rsi:.1f} — zone de surachat (>65)",
                })

        # --- MACD croisement ---
        if b_today.macd_hist is not None and b_prev and b_prev.macd_hist is not None:
            hist_today = float(b_today.macd_hist)
            hist_prev  = float(b_prev.macd_hist)
            if hist_prev < 0 and hist_today >= 0:
                signaux_detectes.append({
                    'type_signal': 'macd_haussier',
                    'direction':   'haussier',
                    'valeur':      float(b_today.macd),
                    'description': "MACD croisement haussier (histogramme passe positif)",
                })
            elif hist_prev > 0 and hist_today <= 0:
                signaux_detectes.append({
                    'type_signal': 'macd_baissier',
                    'direction':   'baissier',
                    'valeur':      float(b_today.macd),
                    'description': "MACD croisement baissier (histogramme passe négatif)",
                })

        # --- Golden / Death cross MM20/MM50 ---
        if (b_today.mm_20 and b_today.mm_50 and
                b_prev and b_prev.mm_20 and b_prev.mm_50):
            mm20_today = float(b_today.mm_20)
            mm50_today = float(b_today.mm_50)
            mm20_prev  = float(b_prev.mm_20)
            mm50_prev  = float(b_prev.mm_50)

            if mm20_prev < mm50_prev and mm20_today >= mm50_today:
                signaux_detectes.append({
                    'type_signal': 'mm_golden_cross',
                    'direction':   'haussier',
                    'valeur':      mm20_today,
                    'description': f"Golden cross MM20({mm20_today:.2f}) > MM50({mm50_today:.2f})",
                })
            elif mm20_prev > mm50_prev and mm20_today <= mm50_today:
                signaux_detectes.append({
                    'type_signal': 'mm_death_cross',
                    'direction':   'baissier',
                    'valeur':      mm20_today,
                    'description': f"Death cross MM20({mm20_today:.2f}) < MM50({mm50_today:.2f})",
                })

        # --- Bollinger ---
        if b_today.boll_inf and b_today.boll_sup:
            cloture = float(b_today.cloture)
            boll_inf = float(b_today.boll_inf)
            boll_sup = float(b_today.boll_sup)
            marge_pct = (boll_sup - boll_inf) / boll_inf * 100 if boll_inf else 0

            if cloture <= boll_inf * 1.01:
                signaux_detectes.append({
                    'type_signal': 'boll_inf',
                    'direction':   'haussier',
                    'valeur':      boll_inf,
                    'description': f"Prix ({cloture:.2f}) proche bande Bollinger basse ({boll_inf:.2f})",
                })
            elif cloture >= boll_sup * 0.99:
                signaux_detectes.append({
                    'type_signal': 'boll_sup',
                    'direction':   'baissier',
                    'valeur':      boll_sup,
                    'description': f"Prix ({cloture:.2f}) proche bande Bollinger haute ({boll_sup:.2f})",
                })

        # --- Pic de volume ---
        if b_today.volume_ratio is not None:
            ratio = float(b_today.volume_ratio)
            if ratio >= 1.5:
                signaux_detectes.append({
                    'type_signal': 'volume_spike',
                    'direction':   'haussier' if float(b_today.cloture) >= float(b_today.ouverture) else 'baissier',
                    'valeur':      ratio,
                    'description': f"Volume anormal : {ratio:.1f}x la moyenne 20 jours",
                })

        # Créer les signaux en base (éviter doublons du jour)
        for s in signaux_detectes:
            _, created = Signal.objects.get_or_create(
                titre=titre,
                date=aujourd_hui,
                type_signal=s['type_signal'],
                defaults={
                    'direction':   s['direction'],
                    'valeur':      s['valeur'],
                    'description': s['description'],
                    'actif':       True,
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
    Le texte de l'alerte est généré par le LLM (scorer_alerte_task).
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
            titre   = Titre.objects.get(pk=titre_id)
            config  = AlerteConfig.objects.get(titre=titre, actif=True)
        except (Titre.DoesNotExist, AlerteConfig.DoesNotExist):
            continue

        signaux = Signal.objects.filter(titre=titre, date=aujourd_hui, actif=True)

        # Score de confluence (simplifié — sera enrichi dans scoring_llm.py)
        score = _calculer_score_confluence(titre, signaux)

        if score < float(config.score_min_declenchement):
            logger.debug(
                f"[Confluence] {titre.ticker} — score {score:.1f} "
                f"< seuil {config.score_min_declenchement} → pas d'alerte"
            )
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
                'score_confluence':  score,
                'niveau':            niveau,
                'cours_au_signal':   derniere_bougie.cloture if derniere_bougie else 0,
                'rsi_au_signal':     derniere_bougie.rsi_14 if derniere_bougie else None,
                'sentiment_au_signal': dernier_sentiment.score if dernier_sentiment else None,
                'texte_ia':          '...',  # sera remplacé par le LLM
                'statut':            'nouvelle',
            }
        )

        if created:
            alerte.signaux.set(signaux)
            alertes_creees += 1
            logger.info(
                f"[Confluence] Alerte {niveau} créée pour {titre.ticker} "
                f"(score {score:.1f})"
            )
            # Générer le texte IA
            scorer_alerte_task.delay(alerte.id)

    logger.info(f"[Task] run_confluence : {alertes_creees} alertes créées")
    return {'status': 'ok', 'alertes': alertes_creees}


def _calculer_score_confluence(titre, signaux) -> float:
    """
    Calcule le score de confluence 0-10 pour un titre.
    Pondération : 60% fondamentaux / 40% technique (profil PEA long terme).
    """
    from app.models import Fondamentaux, ProfilInvestisseur

    profil = ProfilInvestisseur.objects.first()
    poids_fond = (profil.poids_fondamentaux / 100) if profil else 0.6
    poids_tech = (profil.poids_technique / 100)    if profil else 0.4

    # Score technique : nb signaux × direction
    nb_haussiers = signaux.filter(direction='haussier').count()
    nb_baissiers = signaux.filter(direction='baissier').count()
    nb_total     = signaux.count()

    if nb_total == 0:
        return 0.0

    # Score technique /10 basé sur la proportion de signaux haussiers
    score_tech = (nb_haussiers / nb_total) * 10

    # Score fondamental : property score_qualite du dernier fondamentaux
    score_fond = 5.0  # neutre par défaut si pas de données
    dernier_fond = (
        Fondamentaux.objects.filter(titre=titre)
        .order_by('-date_maj')
        .first()
    )
    if dernier_fond and dernier_fond.score_qualite is not None:
        score_fond = float(dernier_fond.score_qualite)

    # Score final pondéré
    score = (score_fond * poids_fond) + (score_tech * poids_tech)

    # Bonus : +0.5 si 3 signaux ou plus convergent
    if nb_total >= 3 and nb_haussiers > nb_baissiers:
        score = min(10.0, score + 0.5)

    return round(score, 1)


# ---------------------------------------------------------------------------
# 7. SCORING LLM — articles et alertes
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def scorer_articles_task(self, article_ids: list[int]):
    """
    Appelle Claude API pour scorer le sentiment de chaque article.
    Appelé après fetch_news_task.
    """
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
    Appelé après run_confluence_task.
    """
    from app.services.scoring_llm import generer_texte_alerte
    try:
        generer_texte_alerte(alerte_id)
    except Exception as exc:
        logger.error(f"[Task] scorer_alerte — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# 8. IMPORT HISTORIQUE — déclenché manuellement
# ---------------------------------------------------------------------------

@shared_task(bind=True)
def import_historique_task(self, ticker: str):
    """
    Importe l'historique OHLCV complet d'un titre depuis EODHD.
    À déclencher manuellement depuis l'admin Django ou la vue d'ajout de titre.

    Exemple : import_historique_task.delay('MC.PA')
    """
    from app.services.eodhd import EODHDClient
    try:
        client = EODHDClient()
        nb     = client.fetch_historique_bulk(ticker)
        logger.info(f"[Task] import_historique {ticker} : {nb} bougies importées")

        # Calculer immédiatement les indicateurs
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
# 9. SCREENER PEA — 1er vendredi du mois
# ---------------------------------------------------------------------------

@shared_task(bind=True)
def update_eligibles_pea_task(self):
    """
    Met à jour l'éligibilité PEA de tous les titres en base.
    Planifié : 1er vendredi du mois à 8h00.
    """
    from app.services.eodhd import EODHDClient
    try:
        client = EODHDClient()
        nb     = client.update_eligibles_pea()
        return {'status': 'ok', 'titres_maj': nb, 'quota': client.statut_quota()}
    except Exception as exc:
        logger.error(f"[Task] update_eligibles_pea — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)
