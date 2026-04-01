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

        # Declencer l'import historique OHLC en tache Celery
        from app.tasks import import_historique_task
        import_historique_task.delay(titre.ticker)

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
        Soft delete : passe actif=False au lieu de supprimer.
        """
        titre = get_object_or_404(Titre, ticker=pk.upper())
        titre.actif  = False
        titre.statut = 'archive'
        titre.save(update_fields=['actif', 'statut'])
        logger.info(f"[API] Titre archive : {titre.ticker}")
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
            # Variation du jour
            derniere = t.prix_journaliers.order_by('-date').first()
            # FIX: utiliser [1:2].first() correctement — c'est deja correct ici
            avant_hier = t.prix_journaliers.order_by('-date')[1:2].first()
            if derniere and avant_hier and t.nb_actions:
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
