"""
app/api/views.py
-----------------
Vues Django REST Framework pour l'API du projet PEA.

Endpoints :

  Titres
    GET    /api/titres/                    -> liste tous les titres actifs
    POST   /api/titres/                    -> ajouter un titre
    GET    /api/titres/{ticker}/           -> fiche complete
    PATCH  /api/titres/{ticker}/           -> modifier statut, notes, nb_actions...
    DELETE /api/titres/{ticker}/           -> archiver (soft delete)
    GET    /api/titres/{ticker}/ohlc/      -> bougies OHLC pour Lightweight Charts
    POST   /api/titres/{ticker}/importer/  -> declencer import historique bulk

  Alertes
    GET    /api/alertes/                   -> toutes les alertes (filtrables)
    GET    /api/alertes/{id}/              -> detail d'une alerte
    PATCH  /api/alertes/{id}/statut/       -> marquer vue/archivee + note

  Sentiment
    GET    /api/sentiment/{ticker}/        -> scores sentiment + articles recents

  Dashboard
    GET    /api/dashboard/                 -> donnees agregees page d'accueil

  Profil
    GET    /api/profil/                    -> profil investisseur PEA
    PATCH  /api/profil/                    -> modifier le profil

  Quota
    GET    /api/quota/                     -> etat des quotas API du jour

  Config alertes
    GET    /api/titres/{ticker}/config/    -> config alertes pour un titre
    PATCH  /api/titres/{ticker}/config/    -> modifier la config
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from app.models import (
    Alerte, AlerteConfig, ApiQuota, Article,
    Fondamentaux, ProfilInvestisseur, ScoreSentiment,
    Signal, Titre,
)
from app.api.serializers import (
    AlerteDetailSerializer, AlerteListSerializer, AlerteStatutSerializer,
    AlerteConfigSerializer, ApiQuotaSerializer,
    DashboardSerializer, FondamentauxSerializer,
    ProfilInvestisseurSerializer, ScoreSentimentSerializer,
    TitreCreateSerializer, TitreDetailSerializer, TitreListSerializer,
    PrixJournalierOHLCSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TITRES
# ---------------------------------------------------------------------------

class TitreViewSet(ViewSet):
    """
    ViewSet principal pour les titres.
    Gere portefeuille ET surveillance via le champ `statut`.
    """

    def list(self, request):
        """
        GET /api/titres/
        Parametres query : ?statut=portefeuille|surveillance|tous
        """
        statut = request.query_params.get('statut', 'tous')

        qs = Titre.objects.filter(actif=True).order_by('nom')

        if statut in ('portefeuille', 'surveillance'):
            qs = qs.filter(statut=statut)
        elif statut != 'tous':
            return Response(
                {'erreur': "statut doit etre 'portefeuille', 'surveillance' ou 'tous'"},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = TitreListSerializer(qs, many=True)
        return Response(serializer.data)

    def create(self, request):
        """
        POST /api/titres/
        Ajoute un titre avec auto-remplissage IA.
        Accepte : ticker (MC.PA), ISIN (FR0010557264), nom (AB Science),
        ou ISIN+code (FR0010557264 AB).
        """
        # --- Resolution ticker depuis ISIN, nom ou ticker direct ---
        from app.services.auto_fill import resoudre_ticker, auto_remplir_titre, seuils_alerte_pour_secteur

        saisie = (request.data.get('ticker') or '').strip()
        ticker_resolu = resoudre_ticker(saisie)

        # Verifier si un titre archive existe deja → le reactiver
        titre_archive = Titre.objects.filter(ticker=ticker_resolu, actif=False).first()
        if titre_archive:
            titre_archive.actif = True
            titre_archive.statut = request.data.get('statut', 'surveillance')
            titre_archive.save(update_fields=['actif', 'statut'])
            logger.info(f"[API] Titre reactive : {ticker_resolu}")
            from app.tasks import analyse_complete_task
            analyse_complete_task.delay(ticker_resolu)
            return Response(
                TitreDetailSerializer(titre_archive).data,
                status=status.HTTP_201_CREATED
            )

        data = request.data.copy()
        data['ticker'] = ticker_resolu

        serializer = TitreCreateSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # --- Auto-remplissage IA via EODHD/FMP ---
        ticker = serializer.validated_data['ticker']
        metadata = auto_remplir_titre(ticker)

        # Injecter les metadonnees auto-remplies (sans ecraser les valeurs fournies)
        for champ, valeur in metadata.items():
            if champ not in serializer.validated_data or not serializer.validated_data[champ]:
                serializer.validated_data[champ] = valeur

        titre = serializer.save()

        # --- Creer une AlerteConfig avec seuils adaptes au secteur ---
        seuils = seuils_alerte_pour_secteur(titre.secteur)
        AlerteConfig.objects.get_or_create(
            titre=titre,
            defaults={
                'score_min_declenchement': seuils['score_min'],
                'seuil_drawdown': seuils['seuil_drawdown'],
                'alertes_renforcement': True,
                'ignorer_court_terme': True,
            }
        )

        # Lancer l'analyse complete en tache Celery (import + indicateurs + news + sentiment)
        from app.tasks import analyse_complete_task
        analyse_complete_task.delay(titre.ticker)

        auto_filled = list(metadata.keys())
        logger.info(
            f"[API] Titre ajoute : {titre.ticker} — "
            f"auto-fill: {', '.join(auto_filled)} — import historique lance"
        )

        return Response(
            {
                **TitreDetailSerializer(titre).data,
                'auto_fill': {
                    'champs_remplis': auto_filled,
                    'source': 'eodhd' if metadata.get('nom') else 'fmp',
                }
            },
            status=status.HTTP_201_CREATED
        )

    def retrieve(self, request, pk=None):
        """
        GET /api/titres/{ticker}/
        Fiche complete : cours 90j, fondamentaux, sentiment 30j, alertes, articles.
        """
        titre = get_object_or_404(Titre, ticker=pk.upper(), actif=True)
        serializer = TitreDetailSerializer(titre)
        return Response(serializer.data)

    def partial_update(self, request, pk=None):
        """
        PATCH /api/titres/{ticker}/
        Modifier statut, notes, nb_actions, prix_revient_moyen, lot...
        """
        titre = get_object_or_404(Titre, ticker=pk.upper(), actif=True)

        # Champs autorises en modification
        champs_autorises = {
            'statut', 'notes', 'nb_actions',
            'prix_revient_moyen', 'date_premier_achat', 'lot',
        }
        data_filtree = {k: v for k, v in request.data.items() if k in champs_autorises}

        serializer = TitreCreateSerializer(titre, data=data_filtree, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        titre = serializer.save()
        return Response(TitreDetailSerializer(titre).data)

    def destroy(self, request, pk=None):
        """
        DELETE /api/titres/{ticker}/
        Suppression reelle du titre et de toutes ses donnees associees.
        """
        titre = get_object_or_404(Titre, ticker=pk.upper())
        ticker = titre.ticker
        titre.delete()
        logger.info(f"[API] Titre supprime : {ticker}")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'], url_path='ohlc')
    def ohlc(self, request, pk=None):
        """
        GET /api/titres/{ticker}/ohlc/
        Bougies OHLC au format Lightweight Charts.
        Parametres : ?periode=1S|1M|3M|6M|1A|3A|MAX  (defaut 1A)
        """
        titre = get_object_or_404(Titre, ticker=pk.upper(), actif=True)

        periode = request.query_params.get('periode', '1A')
        jours_map = {
            '1S': 7, '1M': 30, '3M': 90,
            '6M': 180, '1A': 365, '3A': 1095, 'MAX': 99999
        }
        nb_jours = jours_map.get(periode.upper(), 365)
        depuis   = date.today() - timedelta(days=nb_jours)

        bougies = titre.prix_journaliers.filter(
            date__gte=depuis
        ).order_by('date')

        # Format separe : OHLC pour les chandeliers + volumes + indicateurs
        ohlc_data   = PrixJournalierOHLCSerializer(bougies, many=True).data

        # Indicateurs en series separees (format Lightweight Charts line series)
        indicateurs = {
            'mm20':      [],
            'mm50':      [],
            'mm200':     [],
            'boll_sup':  [],
            'boll_mid':  [],
            'boll_inf':  [],
            'rsi':       [],
            'macd':      [],
            'macd_signal': [],
            'macd_hist': [],
        }

        for b in bougies:
            d = str(b.date)
            if b.mm_20:       indicateurs['mm20'].append({'time': d, 'value': float(b.mm_20)})
            if b.mm_50:       indicateurs['mm50'].append({'time': d, 'value': float(b.mm_50)})
            if b.mm_200:      indicateurs['mm200'].append({'time': d, 'value': float(b.mm_200)})
            if b.boll_sup:    indicateurs['boll_sup'].append({'time': d, 'value': float(b.boll_sup)})
            if b.boll_mid:    indicateurs['boll_mid'].append({'time': d, 'value': float(b.boll_mid)})
            if b.boll_inf:    indicateurs['boll_inf'].append({'time': d, 'value': float(b.boll_inf)})
            if b.rsi_14:      indicateurs['rsi'].append({'time': d, 'value': float(b.rsi_14)})
            if b.macd:        indicateurs['macd'].append({'time': d, 'value': float(b.macd)})
            if b.macd_signal: indicateurs['macd_signal'].append({'time': d, 'value': float(b.macd_signal)})
            if b.macd_hist:   indicateurs['macd_hist'].append({'time': d, 'value': float(b.macd_hist)})

        return Response({
            'ticker':      titre.ticker,
            'periode':     periode,
            'nb_bougies':  len(ohlc_data),
            'ohlc':        ohlc_data,
            'indicateurs': indicateurs,
        })

    @action(detail=True, methods=['post'], url_path='importer')
    def importer(self, request, pk=None):
        """
        POST /api/titres/{ticker}/importer/
        Relance l'import historique bulk (utile si donnees manquantes).
        """
        titre = get_object_or_404(Titre, ticker=pk.upper(), actif=True)
        from app.tasks import import_historique_task
        import_historique_task.delay(titre.ticker)
        return Response({
            'message': f"Import historique lance pour {titre.ticker}",
            'ticker':  titre.ticker,
        })

    @action(detail=True, methods=['post'], url_path='analyser')
    def analyser(self, request, pk=None):
        """
        POST /api/titres/{ticker}/analyser/
        Lance l'analyse complete IA d'un titre (synchrone) :
          1. Calcul des indicateurs techniques
          2. Collecte des news (NewsAPI + Google News + Boursorama + Zonebourse + Reddit)
          3. Scoring sentiment des articles (Mistral)
          4. Generation du sentiment mixte (technique + presse + rapport IA)
        Retourne le resultat complet de l'analyse.
        """
        titre = get_object_or_404(Titre, ticker=pk.upper(), actif=True)
        resultats = {'ticker': titre.ticker, 'etapes': {}}

        # 1. Indicateurs techniques
        try:
            import os
            os.environ.setdefault('NUMBA_DISABLE_JIT', '1')
            from app.services.indicators import calculate_indicators
            nb = calculate_indicators(titre)
            resultats['etapes']['indicateurs'] = f'{nb} bougies calculees'
        except Exception as e:
            resultats['etapes']['indicateurs'] = f'Erreur : {e}'

        # 2. Collecte news NewsAPI
        nb_articles = 0
        try:
            from django.conf import settings
            if settings.NEWSAPI_KEY:
                from app.services.newsapi_client import NewsAPIClient
                client = NewsAPIClient()
                nb_articles = client.import_news_pour_titres([titre.ticker])
                client.maj_quota()
                resultats['etapes']['newsapi'] = f'{nb_articles} articles importes'
            else:
                resultats['etapes']['newsapi'] = 'Cle NewsAPI non configuree'
        except Exception as e:
            resultats['etapes']['newsapi'] = f'Erreur : {e}'

        # 2b. RSS (Google News + Boursorama + Zonebourse) — historique 1 an si premier lancement
        try:
            from app.services.rss_news import RSSCollector
            rss = RSSCollector()
            # Historique si le titre a moins de 10 articles
            nb_articles_existants = Article.objects.filter(titre=titre).count()
            historique = nb_articles_existants < 10
            rss_result = rss.import_all_sources([titre.ticker], historique=historique)
            resultats['etapes']['rss'] = {k: f'{v} articles' for k, v in rss_result.items()}
        except Exception as e:
            resultats['etapes']['rss'] = f'Erreur : {e}'

        # 2c. Reddit (r/bourse, r/vosfinances)
        try:
            from app.services.reddit_client import RedditCollector
            reddit = RedditCollector()
            nb_articles_existants = Article.objects.filter(titre=titre, source='reddit').count()
            historique = nb_articles_existants < 5
            nb_reddit = reddit.import_reddit_posts([titre.ticker], historique=historique)
            resultats['etapes']['reddit'] = f'{nb_reddit} posts importes'
        except Exception as e:
            resultats['etapes']['reddit'] = f'Erreur : {e}'

        # 3. Scoring sentiment articles
        nb_scores = 0
        try:
            ids = list(
                Article.objects.filter(
                    titre=titre, score_sentiment__isnull=True
                ).values_list('id', flat=True)
            )
            if ids:
                from app.services.scoring_llm import scorer_articles
                scorer_articles(ids)
                nb_scores = len(ids)
            resultats['etapes']['scoring'] = f'{nb_scores} articles scores'
        except Exception as e:
            resultats['etapes']['scoring'] = f'Erreur : {e}'

        # 4. Sentiment mixte (technique + presse + rapport IA)
        try:
            from app.services.scoring_llm import generer_sentiment_mixte
            sentiment = generer_sentiment_mixte(titre.ticker)
            if sentiment:
                resultats['etapes']['sentiment'] = {
                    'technique': sentiment['score_technique'],
                    'presse': sentiment['score_presse'],
                    'global': sentiment['score_global'],
                    'resume_ia': sentiment['resume_ia'][:300],
                }
            else:
                resultats['etapes']['sentiment'] = 'Aucune donnee'
        except Exception as e:
            resultats['etapes']['sentiment'] = f'Erreur : {e}'

        logger.info(f"[API] Analyse complete {titre.ticker} : {resultats['etapes']}")

        return Response(resultats)

    @action(detail=True, methods=['get', 'patch'], url_path='config')
    def config_alertes(self, request, pk=None):
        """
        GET/PATCH /api/titres/{ticker}/config/
        Consulter ou modifier la configuration des alertes pour ce titre.
        """
        titre  = get_object_or_404(Titre, ticker=pk.upper(), actif=True)
        config, _ = AlerteConfig.objects.get_or_create(titre=titre)

        if request.method == 'GET':
            return Response(AlerteConfigSerializer(config).data)

        serializer = AlerteConfigSerializer(config, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# ALERTES
# ---------------------------------------------------------------------------

class AlerteListView(APIView):
    """
    GET /api/alertes/
    Parametres : ?statut=nouvelle|vue|archivee  ?niveau=forte|moderee|surveillance
                 ?ticker=MC.PA  ?depuis=YYYY-MM-DD  ?limit=20
    """

    def get(self, request):
        qs = Alerte.objects.select_related('titre').order_by('-date_detection')

        # Filtres
        statut = request.query_params.get('statut')
        niveau = request.query_params.get('niveau')
        ticker = request.query_params.get('ticker')
        depuis = request.query_params.get('depuis')

        # FIX: proteger int() contre les valeurs invalides
        try:
            limit = int(request.query_params.get('limit', 50))
        except (ValueError, TypeError):
            limit = 50

        if statut:
            qs = qs.filter(statut=statut)
        if niveau:
            qs = qs.filter(niveau=niveau)
        if ticker:
            qs = qs.filter(titre__ticker=ticker.upper())
        if depuis:
            try:
                qs = qs.filter(date_detection__date__gte=date.fromisoformat(depuis))
            except ValueError:
                pass

        qs = qs[:limit]
        return Response(AlerteListSerializer(qs, many=True).data)


class AlerteDetailView(APIView):
    """GET /api/alertes/{id}/"""

    def get(self, request, pk):
        alerte = get_object_or_404(Alerte, pk=pk)
        return Response(AlerteDetailSerializer(alerte).data)


class AlerteStatutView(APIView):
    """PATCH /api/alertes/{id}/statut/ — marquer vue, archivee, ajouter une note"""

    def patch(self, request, pk):
        alerte     = get_object_or_404(Alerte, pk=pk)
        serializer = AlerteStatutSerializer(alerte, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# SENTIMENT
# ---------------------------------------------------------------------------

class SentimentView(APIView):
    """
    GET /api/sentiment/{ticker}/
    Retourne les scores sentiment + articles recents scores.
    Parametres : ?jours=7|14|30 (defaut 14)
    """

    def get(self, request, ticker):
        titre  = get_object_or_404(Titre, ticker=ticker.upper(), actif=True)

        # FIX: proteger int() contre les valeurs invalides
        try:
            nb_jours = int(request.query_params.get('jours', 14))
        except (ValueError, TypeError):
            nb_jours = 14

        depuis = date.today() - timedelta(days=nb_jours)

        # Scores par source sur la periode
        scores_presse = ScoreSentiment.objects.filter(
            titre=titre, source='presse', date__gte=depuis
        ).order_by('date')

        scores_social = ScoreSentiment.objects.filter(
            titre=titre, source='social', date__gte=depuis
        ).order_by('date')

        scores_global = ScoreSentiment.objects.filter(
            titre=titre, source='global', date__gte=depuis
        ).order_by('date')

        # Dernier score global
        dernier_global = scores_global.order_by('-date').first()

        # Articles recents scores
        articles = Article.objects.filter(
            titre=titre,
            date_pub__date__gte=depuis,
            score_sentiment__isnull=False,
        ).order_by('-date_pub')[:20]

        # Topics les plus frequents
        from collections import Counter
        tous_tags = []
        for a in articles:
            tous_tags.extend(a.tags or [])
        topics_frequents = [t for t, _ in Counter(tous_tags).most_common(5)]

        return Response({
            'ticker':        titre.ticker,
            'nom':           titre.nom_court or titre.nom,
            'periode_jours': nb_jours,
            'score_actuel':  {
                'score':        float(dernier_global.score) if dernier_global else None,
                'label':        dernier_global.label if dernier_global else 'Inconnu',
                'couleur':      dernier_global.couleur if dernier_global else 'warning',
                'variation_24h': float(dernier_global.variation_24h) if dernier_global and dernier_global.variation_24h else None,
                'nb_articles':  dernier_global.nb_articles if dernier_global else 0,
            },
            'historique': {
                'presse': ScoreSentimentSerializer(scores_presse, many=True).data,
                'social': ScoreSentimentSerializer(scores_social, many=True).data,
                'global': ScoreSentimentSerializer(scores_global, many=True).data,
            },
            'articles':        list(reversed([{
                'id':         a.id,
                'titre':      a.titre_art,
                'source':     a.source,
                'date_pub':   a.date_pub.isoformat(),
                'score':      float(a.score_sentiment) if a.score_sentiment else None,
                'tags':       a.tags,
                'url':        a.url,
            } for a in articles])),
            'topics_frequents': topics_frequents,
        })


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

class DashboardView(APIView):
    """
    GET /api/dashboard/
    Donnees agregees pour la page d'accueil.
    """

    def get(self, request):
        titres_pf = Titre.objects.filter(statut='portefeuille', actif=True)
        titres_sv = Titre.objects.filter(statut='surveillance', actif=True)

        # Valeur totale du portefeuille
        valeur_totale   = Decimal('0')
        variation_totale = Decimal('0')
        for t in titres_pf:
            vp = t.valeur_position
            if vp:
                valeur_totale += Decimal(str(vp))
            # Variation du jour : utiliser cloture_veille si disponible (plus fiable)
            derniere = t.prix_journaliers.order_by('-date').first()
            if derniere and t.nb_actions:
                if derniere.cloture_veille:
                    variation_totale += (
                        Decimal(str(derniere.cloture)) - Decimal(str(derniere.cloture_veille))
                    ) * t.nb_actions
                else:
                    # Fallback : comparer avec la bougie précédente
                    avant_hier = t.prix_journaliers.order_by('-date')[1:2].first()
                    if avant_hier:
                        variation_totale += (
                            Decimal(str(derniere.cloture)) - Decimal(str(avant_hier.cloture))
                        ) * t.nb_actions

        # Alertes
        alertes_nouvelles = Alerte.objects.filter(statut='nouvelle')
        alertes_fortes    = alertes_nouvelles.filter(niveau='forte')
        derniere_alerte   = alertes_nouvelles.order_by('-date_detection').first()

        # Sentiment moyen portefeuille
        scores_today = []
        for t in titres_pf:
            s = t.scores_sentiment.filter(source='global').order_by('-date').first()
            if s:
                scores_today.append(float(s.score))
        sentiment_moyen = (
            round(sum(scores_today) / len(scores_today), 3)
            if scores_today else None
        )

        # Quotas API du jour
        quotas = ApiQuota.objects.filter(date=date.today())

        data = {
            'valeur_totale_portefeuille':  valeur_totale if valeur_totale else None,
            'variation_jour_portefeuille': variation_totale if variation_totale else None,
            'nb_titres_portefeuille':      titres_pf.count(),
            'nb_titres_surveillance':      titres_sv.count(),
            'nb_alertes_nouvelles':        alertes_nouvelles.count(),
            'nb_alertes_fortes':           alertes_fortes.count(),
            'derniere_alerte':             derniere_alerte,
            'sentiment_portefeuille_moyen': sentiment_moyen,
            'quotas':                      quotas,
        }

        serializer = DashboardSerializer(data)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# PROFIL INVESTISSEUR
# ---------------------------------------------------------------------------

class ProfilView(APIView):
    """
    GET  /api/profil/ — consulter le profil
    PATCH /api/profil/ — modifier le profil
    """

    def _get_profil(self):
        profil, _ = ProfilInvestisseur.objects.get_or_create(pk=1)
        return profil

    def get(self, request):
        return Response(ProfilInvestisseurSerializer(self._get_profil()).data)

    def patch(self, request):
        profil     = self._get_profil()
        serializer = ProfilInvestisseurSerializer(profil, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# QUOTA API
# ---------------------------------------------------------------------------

class QuotaView(APIView):
    """GET /api/quota/ — etat des quotas API du jour"""

    def get(self, request):
        quotas = ApiQuota.objects.filter(date=date.today())

        # Ajouter les APIs sans enregistrement (quota = 0)
        apis_connues  = {'eodhd', 'fmp', 'newsapi'}
        apis_presentes = set(quotas.values_list('api', flat=True))

        data = list(ApiQuotaSerializer(quotas, many=True).data)

        for api in apis_connues - apis_presentes:
            limites = {'eodhd': 20, 'fmp': 250, 'newsapi': 100}
            data.append({
                'api':         api,
                'date':        str(date.today()),
                'nb_requetes': 0,
                'restantes':   limites.get(api, 0),
                'pct_utilise': 0.0,
            })

        return Response(sorted(data, key=lambda x: x['api']))


# -----------------------------------------------------------------------
# Chat IA
# -----------------------------------------------------------------------

class ChatView(APIView):
    """POST /api/chat/ — Chat IA contextuel."""

    def post(self, request):
        question = request.data.get('question', '').strip()
        if not question:
            return Response(
                {'error': 'Le champ "question" est requis.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticker = request.data.get('ticker', None)

        from app.services.chat_ia import chat_ia, DISCLAIMER
        reponse = chat_ia(question, ticker=ticker)

        return Response({
            'reponse': reponse,
            'ticker': ticker,
            'disclaimer': DISCLAIMER,
        })


# -----------------------------------------------------------------------
# Documents par titre
# -----------------------------------------------------------------------

class DocumentListView(APIView):
    """GET/POST /api/titres/<ticker>/documents/"""
    from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, ticker):
        from app.models import DocumentTitre
        docs = DocumentTitre.objects.filter(titre__ticker=ticker).order_by('-date_upload')
        from app.api.serializers import DocumentTitreSerializer
        return Response(DocumentTitreSerializer(docs, many=True, context={'request': request}).data)

    def post(self, request, ticker):
        from app.models import Titre, DocumentTitre
        from rest_framework.parsers import MultiPartParser, FormParser

        try:
            titre = Titre.objects.get(ticker=ticker, actif=True)
        except Titre.DoesNotExist:
            return Response({'error': f'Titre {ticker} introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        fichier = request.FILES.get('fichier')
        if not fichier:
            return Response({'error': 'Aucun fichier fourni.'}, status=status.HTTP_400_BAD_REQUEST)

        # Vérifier l'extension
        import os
        ext = os.path.splitext(fichier.name)[1].lower()
        extensions_ok = {'.pdf', '.docx', '.xlsx', '.xls', '.png', '.jpg', '.jpeg', '.gif', '.txt', '.csv'}
        if ext not in extensions_ok:
            return Response(
                {'error': f'Format non supporté ({ext}). Formats acceptés : PDF, Word, Excel, images, texte.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Créer le document
        doc = DocumentTitre.objects.create(
            titre=titre,
            fichier=fichier,
            nom=request.data.get('nom', fichier.name),
            type_doc=request.data.get('type_doc', 'autre'),
            taille=fichier.size,
            notes=request.data.get('notes', ''),
        )

        # Lancer l'extraction + résumé en arrière-plan
        from app.services.document_service import traiter_document
        try:
            traiter_document(doc.id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Erreur traitement document %d: %s", doc.id, e)

        from app.api.serializers import DocumentTitreSerializer
        return Response(
            DocumentTitreSerializer(doc, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class DocumentDetailView(APIView):
    """GET/DELETE /api/titres/<ticker>/documents/<id>/"""

    def get(self, request, ticker, pk):
        from app.models import DocumentTitre
        try:
            doc = DocumentTitre.objects.get(pk=pk, titre__ticker=ticker)
        except DocumentTitre.DoesNotExist:
            return Response({'error': 'Document introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        from app.api.serializers import DocumentTitreSerializer
        data = DocumentTitreSerializer(doc, context={'request': request}).data
        data['texte_extrait'] = doc.texte_extrait  # Inclure le texte complet en détail
        return Response(data)

    def delete(self, request, ticker, pk):
        from app.models import DocumentTitre
        try:
            doc = DocumentTitre.objects.get(pk=pk, titre__ticker=ticker)
        except DocumentTitre.DoesNotExist:
            return Response({'error': 'Document introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        # Supprimer le fichier physique
        if doc.fichier:
            doc.fichier.delete(save=False)
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# -----------------------------------------------------------------------
# Patterns graphiques (étape 31)
# -----------------------------------------------------------------------

class PatternListView(APIView):
    """GET /api/titres/<ticker>/patterns/ — patterns graphiques détectés."""

    def get(self, request, ticker):
        from app.models import PatternDetecte
        from app.api.serializers import PatternDetecteSerializer

        statut = request.query_params.get('statut')
        qs = PatternDetecte.objects.filter(titre__ticker=ticker)
        if statut:
            qs = qs.filter(statut=statut)
        qs = qs.order_by('-date_detection')[:10]
        return Response(PatternDetecteSerializer(qs, many=True).data)


# -----------------------------------------------------------------------
# Veille sectorielle (étape 35)
# -----------------------------------------------------------------------

class VeilleSectorielleView(APIView):
    """GET /api/veille-sectorielle/ — articles sectoriels avec impact IA."""

    def get(self, request):
        from app.models import ArticleSectoriel
        from app.api.serializers import ArticleSectorielSerializer

        secteur = request.query_params.get('secteur')
        limit = int(request.query_params.get('limit', 20))

        qs = ArticleSectoriel.objects.all()
        if secteur:
            qs = qs.filter(secteur=secteur)
        qs = qs.order_by('-date_pub')[:limit]
        return Response(ArticleSectorielSerializer(qs, many=True).data)
