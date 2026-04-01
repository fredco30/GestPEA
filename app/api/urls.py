"""
app/api/urls.py
----------------
Routing URL pour l'API REST du projet PEA.
"""

from django.urls import path
from app.api import views

urlpatterns = [

    # --- Titres ---
    path('titres/',
         views.TitreViewSet.as_view({'get': 'list', 'post': 'create'}),
         name='titre-list'),

    path('titres/<str:pk>/',
         views.TitreViewSet.as_view({
             'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy'
         }),
         name='titre-detail'),

    path('titres/<str:pk>/ohlc/',
         views.TitreViewSet.as_view({'get': 'ohlc'}),
         name='titre-ohlc'),

    path('titres/<str:pk>/importer/',
         views.TitreViewSet.as_view({'post': 'importer'}),
         name='titre-importer'),

    path('titres/<str:pk>/config/',
         views.TitreViewSet.as_view({'get': 'config_alertes', 'patch': 'config_alertes'}),
         name='titre-config'),

    path('titres/<str:pk>/analyser/',
         views.TitreViewSet.as_view({'post': 'analyser'}),
         name='titre-analyser'),

    # --- Alertes ---
    path('alertes/',
         views.AlerteListView.as_view(),
         name='alerte-list'),

    path('alertes/<int:pk>/',
         views.AlerteDetailView.as_view(),
         name='alerte-detail'),

    path('alertes/<int:pk>/statut/',
         views.AlerteStatutView.as_view(),
         name='alerte-statut'),

    # --- Sentiment ---
    path('sentiment/<str:ticker>/',
         views.SentimentView.as_view(),
         name='sentiment'),

    # --- Dashboard ---
    path('dashboard/',
         views.DashboardView.as_view(),
         name='dashboard'),

    # --- Profil ---
    path('profil/',
         views.ProfilView.as_view(),
         name='profil'),

    # --- Quota ---
    path('quota/',
         views.QuotaView.as_view(),
         name='quota'),

    # --- Chat IA ---
    path('chat/',
         views.ChatView.as_view(),
         name='chat-ia'),

    # --- Documents ---
    path('titres/<str:ticker>/documents/',
         views.DocumentListView.as_view(),
         name='document-list'),

    path('titres/<str:ticker>/documents/<int:pk>/',
         views.DocumentDetailView.as_view(),
         name='document-detail'),
]
