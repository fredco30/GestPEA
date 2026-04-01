# Claude PEA — Journal de projet

> Fichier de référence généré et maintenu au fil des échanges avec Claude.  
> Objectif : disposer d'un historique complet des décisions d'architecture pour lancer le codage sans avoir à tout réexpliquer.  
> **Dernière mise à jour : 1er avril 2026 — DEPLOYE SUR VPS ✅ — Phase 1 terminée — Phase 2 IA avancée en cours**

---

## 1. Vision du projet

Application web personnelle d'aide à la gestion d'un **PEA (Plan d'Épargne en Actions)** combinant :

- Suivi du **sentiment de marché** (presse financière, réseaux sociaux, forums)
- **Analyse technique graphique** des cours (chandeliers, indicateurs)
- **Analyse fondamentale** des titres (PER, BPA, dividende, dette, FCF)
- **Alertes IA croisées** sentiment + technique + fondamentaux
- Profil investisseur long terme strictement orienté PEA

L'IA ne donne **pas de conseils d'investissement** — elle observe, mesure et signale. La décision reste à l'utilisateur. Formulation systématique : *"Cette observation ne constitue pas un conseil d'investissement."*

---

## 2. Profil investisseur

| Paramètre | Valeur |
|---|---|
| Enveloppe | PEA classique |
| Plafond légal versements | 150 000 € |
| Versements effectués | ~42 000 € |
| Capacité restante | ~108 000 € |
| Date d'ouverture | Mars 2019 (fiscalité pleine — exonération IR après 5 ans) |
| Fiscalité actuelle | PFU 17,2% (prélèvements sociaux uniquement) |
| Horizon | 7–15 ans |
| Style | Croissance (actions européennes de qualité) |
| Tolérance risque | Modérée (3/5) |
| Mode | Accumulation — pas de day trading, pas de swing |

### Contraintes PEA intégrées dans l'app

- **Univers éligible uniquement** : actions UE/EEE + ETF avec ≥75% actions européennes. Les titres US, obligations, REITs américains sont invisibles dans l'interface.
- **Aucune suggestion de retrait** avant 5 ans (clôturerait le PEA).
- **Surveillance du plafond** de versement — alerte si une opportunité nécessite un apport dépassant la capacité restante.
- **Alertes orientées renforcement** — l'IA signale les points d'entrée, pas les sorties.
- **Fréquence** : alertes hebdomadaires ou événementielles (pas de notifications intraday).
- **Pondération score** : 60% fondamentaux / 40% technique (vs 50/50 par défaut).

---

## 3. Architecture fonctionnelle (4 couches)

```
[ Sources de données ]
  Presse financière · Réseaux sociaux · Forums · Données marché · Rapports officiels
        ↓ collecte & scraping (Celery)
[ Moteur IA (backend) ]
  Analyse sentiment · Détection tendances · Corrélation marché · Alertes & signaux
        ↓ scores & signaux
[ Interface utilisateur (dashboard) ]
  Sentiment Gauge · News Feed IA · Heatmap secteurs · Mon portefeuille · Chat IA
        ↓
[ Aide à la décision ]
  Score de risque · Observations IA · Historique signaux · Backtesting sentiment
```

---

## 4. Structure du dashboard

### Onglet 1 — Actions en portefeuille

Pour chaque titre PEA détenu :
- Sélecteur de titres en haut de page (tabs)
- En-tête : nom, place, cours actuel, variation jour, badge sentiment
- **Métriques clés** : RSI(14), MACD, écart MM50j, score sentiment
- **Graphique technique** (Lightweight Charts TradingView) :
  - Chandeliers japonais OHLC
  - Indicateurs activables : MM20, MM50, Bollinger, RSI, MACD
  - Annotations IA superposées sur le graphique (marqueurs de confluence)
  - Sélecteur de période : 1S / 1M / 3M / 1A
- **Panneau sentiment** : barre presse, barre réseaux sociaux, signaux techniques (croisements, zones RSI, signal MACD, niveaux Bollinger)
- **Feed actualités** : articles résumés par IA avec score de sentiment (-1 à +1) et code couleur

### Onglet 2 — Surveillance

Vue liste/tableau de tous les titres suivis sans les posséder :
- Cours, variation, score sentiment du jour, mini-sparkline 7 jours
- Bouton "Créer une alerte" avec critères combinés (ex : RSI < 40 ET sentiment < 0)
- Ajout/suppression de titres de la liste de surveillance

### Navigation latérale

- Mes actions (portefeuille)
- Surveillance
- Heatmap (secteurs)
- Flux global (news)
- Chat IA
- Backtesting

---

## 5. Moteur d'alertes IA

### Principe de confluence

L'alerte se déclenche uniquement quand **plusieurs signaux indépendants convergent** simultanément. Score de confluence calculé de 0 à 10, pondéré par l'historique de fiabilité du pattern sur ce titre spécifique.

### Sources de signaux

| Signal | Type | Description |
|---|---|---|
| Score sentiment presse | Sentiment | NLP/LLM sur articles financiers |
| Score sentiment réseaux sociaux | Sentiment | Twitter/X, Reddit, StockTwits |
| RSI(14) | Technique | Zone survente (<40) ou surachat (>65) |
| MACD croisement | Technique | Signal ligne signal |
| MM20/MM50 croisement | Technique | Golden/death cross |
| Bollinger bandes | Technique | Distance aux bandes |
| Anomalie volume | Volume | Écart vs moyenne 20 jours |

### Niveaux d'alerte

- **Forte** : score 8-10, 3 signaux ou plus — notification immédiate
- **Modérée** : score 5-7, 2 signaux — notification quotidienne
- **Surveillance** : score 3-4 — digest hebdomadaire

### Format type d'une alerte (profil PEA long terme)

```
Air Liquide · Opportunité de renforcement · Score 7,4/10

Signaux détectés :
- RSI à 41 (zone de survente modérée) — rebond depuis 38
- Sentiment presse stable +0,60 sur 7 jours
- Volume +22% vs moyenne 20j

Contexte IA : "Ce profil de signaux s'est présenté 5 fois sur AI.PA
ces 18 derniers mois. Dans 4 cas sur 5, le titre a progressé de
+3% à +6% dans les 10 jours suivants."

Fiabilité historique de ce pattern sur ce titre : 80%
Éligible PEA · Plafond disponible : 108 000 €

— Cette observation ne constitue pas un conseil d'investissement.
```

### Canaux de notification

- Notification in-app
- Email
- Telegram (bot)
- Webhook (Zapier, n8n)

---

## 6. Stack technique retenu

### Backend

| Composant | Technologie |
|---|---|
| Framework web | Django + Django REST Framework |
| Tâches asynchrones | Celery + Redis |
| Base de données | PostgreSQL |
| Scheduler | Celery Beat (crontab) |
| LLM scoring sentiment | Mistral AI (mistral-small-latest + mistral-large-latest) |
| Analyse technique | pandas-ta ou ta-lib (Python) |

### Frontend

| Composant | Technologie |
|---|---|
| Framework | React |
| Graphiques financiers | Lightweight Charts (TradingView — open source) |
| Graphiques analytics | Recharts |
| Styles | Tailwind CSS |

### Infrastructure

- VPS OVH (Ubuntu) — déjà utilisé par Fred pour geoclic.fr
- Nginx + SSL Let's Encrypt
- Celery workers pour la collecte périodique

---

## 7. APIs financières retenues

### Stratégie générale : **tiers gratuits suffisants** pour usage personnel

| API | Usage | Quota gratuit | Coût si upgrade |
|---|---|---|---|
| **EODHD** | Cours OHLC + fondamentaux EU + screener PEA + news | 20 req/jour | ~20 €/mois |
| **FMP** (Financial Modeling Prep) | Fondamentaux complémentaires + ratios calculés + calendrier dividendes | 250 req/jour | ~15 $/mois |
| **NewsAPI** | Presse française (Les Échos, BFM, Le Monde) | 100 req/jour | ~49 $/mois |
| **yfinance** (Python lib) | Prototypage local uniquement — NE PAS utiliser en production | Gratuit | — |
| **Reddit API** | Sentiment réseaux sociaux | Gratuit | — |
| **StockTwits API** | Sentiment investisseurs retail | Gratuit | — |

### Pourquoi EODHD en principal

- Meilleure couverture actions européennes (Euronext Paris, Amsterdam, Bruxelles, Milan, Madrid, Xetra, LSE)
- Screener avec champ `country` → filtre éligibilité PEA natif
- Données OHLC + fondamentaux + news dans un seul abonnement
- Téléchargement bulk : 1 seul appel pour tout l'historique d'un titre (idéal pour l'import initial)
- Fondamentaux : PER, BPA, ROE, dette nette/EBITDA, rendement dividende, FCF

---

## 8. Stratégie de rotation des appels API (quota EODHD 20 req/jour)

### Principe validé

> "On n'est pas obligé de rafraîchir tous les fondamentaux le même jour.  
> 50% lundi+mercredi, 50% mardi+jeudi → on reste sous les 20 req/jour."

### Planning Celery Beat hebdomadaire

| Jour | Tâches | Requêtes estimées |
|---|---|---|
| Lundi | Cours EOD tous titres + Fondamentaux **lot A** (50%) + News mutualisée | ~14 req |
| Mardi | Cours EOD tous titres + Fondamentaux **lot B** (50%) + News mutualisée | ~14 req |
| Mercredi | Cours EOD tous titres + Fondamentaux **lot A** (50%) + News mutualisée | ~14 req |
| Jeudi | Cours EOD tous titres + Fondamentaux **lot B** (50%) + News mutualisée | ~14 req |
| Vendredi | Cours EOD tous titres + News mutualisée + Screener PEA (1x/mois) | ~11 req |
| Sam/Dim | Rien (marché fermé) | 0 req |

**Lot A** = titres index pairs, **Lot B** = titres index impairs (champ `lot` en base).

### Règles de cache (économie de requêtes)

1. **Import historique OHLC** : 1 seul appel bulk par titre au premier lancement → stocké en base. Ensuite : 1 appel/titre/soir uniquement pour la bougie du jour.
2. **News mutualisée** : 1 seule requête avec `symbols=MC.PA,AI.PA,TTE.PA,…` (opérateur multi-tickers EODHD) → compte pour 1 appel quelle que soit la taille de la liste.
3. **Screener PEA** : 1 appel le 1er vendredi du mois uniquement pour mettre à jour la table `titres_eligibles_pea`.
4. **Fondamentaux** : données trimestrielles → inutile de les rafraîchir plus de 2x/semaine.

### Code Celery Beat (extrait)

```python
# settings.py
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'cours-eod': {
        'task': 'app.tasks.fetch_cours_eod',
        'schedule': crontab(hour=18, minute=30, day_of_week='1-5'),
    },
    'fondamentaux-lot-a': {
        'task': 'app.tasks.fetch_fondamentaux',
        'args': ['A'],
        'schedule': crontab(hour=19, minute=0, day_of_week='1,3'),
    },
    'fondamentaux-lot-b': {
        'task': 'app.tasks.fetch_fondamentaux',
        'args': ['B'],
        'schedule': crontab(hour=19, minute=0, day_of_week='2,4'),
    },
    'news-sentiment': {
        'task': 'app.tasks.fetch_news',
        'schedule': crontab(hour=20, minute=0, day_of_week='1-5'),
    },
    'screener-pea': {
        'task': 'app.tasks.update_eligibles_pea',
        'schedule': crontab(hour=8, minute=0, day_of_week='5', day_of_month='1-7'),
    },
}
```

---

## 9. Modèles Django (`models.py`) ✅

Fichier : `app/models.py` — 8 modèles définis.

### Vue d'ensemble des modèles

| Modèle | Rôle | Clé technique |
|---|---|---|
| `Titre` | Référentiel des titres suivis | Champ `lot` (A/B) pour rotation Celery · champ `eligible_pea` mis à jour par screener mensuel |
| `PrixJournalier` | OHLCV journalier + indicateurs techniques | `unique_together (titre, date)` · indicateurs pré-calculés stockés (RSI, MACD, MM20/50/200, Bollinger, volume_ratio) |
| `Fondamentaux` | Données fondamentales trimestrielles | `score_qualite` property calculé sur 10 (ROE, dette, croissance BPA, marge, dividende) |
| `ScoreSentiment` | Score LLM par source (presse / social / global) | Score de -1 à +1 · `label` et `couleur` properties |
| `Article` | Articles et posts individuels scorés | Tags JSON (topics détectés) · lien vers Titre |
| `Signal` | Signal technique ou sentiment détecté | 12 types de signaux · direction haussier/baissier/neutre |
| `AlerteConfig` | Seuils et canaux configurés par titre | OneToOne avec Titre · canaux : app, email, Telegram, webhook |
| `Alerte` | Alerte déclenchée par le moteur de confluence | Score 0-10 · ManyToMany vers Signal · texte IA · fiabilité historique |
| `ProfilInvestisseur` | Paramètres PEA globaux | Pondération 60/40 fondamentaux/technique · `fiscalite_pleine` property · `capacite_versement_restante` |

### Décisions de conception importantes

- **`Titre.lot`** (A ou B) : assigné à l'ajout, pilote la rotation Celery pour les fondamentaux.
- **`PrixJournalier`** stocke les indicateurs techniques pré-calculés (pas recalculés à la volée) → performances front optimales.
- **`Fondamentaux.score_qualite`** : property Python (pas un champ DB) — calculé à la lecture sur 5 critères pondérés.
- **`Alerte.texte_ia`** : rédigé par Claude API · toujours suivi de `disclaimer` = *"Cette observation ne constitue pas un conseil d'investissement."*
- **`ProfilInvestisseur`** : singleton (un seul enregistrement pour usage personnel).
- **`ScoreSentiment.source`** : 3 valeurs — `presse`, `social`, `global` (global = pondération des deux).
- **`Article.tags`** : JSONField liste de topics détectés par le LLM (ex: `["résultats", "dividende", "acquisition"]`).

### Structure des répertoires Django prévue

```
pea_project/
├── manage.py
├── .env                        ✅ (depuis env_example.txt)
├── pea_project/
│   ├── settings.py             ✅ Fait
│   ├── urls.py                 ✅ Fait
│   └── celery.py               ⬜ À faire
├── app/
│   ├── models.py               ✅ Fait (9 modèles + ApiQuota)
│   ├── tasks.py                ✅ Fait (9 tâches Celery chaînées)
│   ├── services/
│   │   ├── eodhd.py            ✅ Fait
│   │   ├── indicators.py       ✅ Fait
│   │   └── scoring_llm.py      ✅ Fait
│   ├── api/
│   │   ├── serializers.py      ✅ Fait (12 serializers)
│   │   ├── views.py            ✅ Fait (17 endpoints)
│   │   └── urls.py             ✅ Fait
│   └── admin.py                ⬜ À faire
└── frontend/                   ✅ Fait (React)
    ├── package.json
    └── src/
        ├── index.js / App.jsx / index.css  (gabarits dans package.json)
        ├── api/client.js
        ├── hooks/useTitre.js + useTitres.js
        ├── pages/Dashboard.jsx
        └── components/
            ├── GraphiqueTechnique.jsx
            ├── FicheTitre.jsx
            └── (BadgeSentiment, CarteSignaux, FeedArticles,
               CarteAlertes, ListeSurveillance,
               PanneauAlertes, QuotaBadge) → utilitaires.jsx
```

---

## 10. Fichiers livrés à ce jour

| Fichier | Contenu | Emplacement dans le projet |
|---|---|---|
| `models.py` | 9 modèles Django (Titre, PrixJournalier, Fondamentaux, ScoreSentiment, Article, Signal, AlerteConfig, Alerte, ProfilInvestisseur) | `app/models.py` |
| `models_apiquota.py` | Modèle ApiQuota — compteur quota journalier par API | À coller en bas de `app/models.py` |
| `eodhd.py` | Client EODHD : cours EOD, historique bulk, fondamentaux, news, screener PEA | `app/services/eodhd.py` |
| `tasks.py` | 9 tâches Celery chaînées (cours → fondamentaux → news → indicateurs → signaux → confluence → LLM) | `app/tasks.py` |
| `indicators.py` | Calcul RSI, MACD, MM20/50/200, Bollinger, volume ratio via pandas-ta | `app/services/indicators.py` |
| `scoring_llm.py` | Scoring sentiment articles (Haiku) + rédaction texte alertes (Sonnet) + digest hebdo | `app/services/scoring_llm.py` |
| `serializers.py` | 12 serializers DRF (list/detail/write + Dashboard agrégé) | `app/api/serializers.py` |
| `views.py` | 17 endpoints REST (titres, alertes, sentiment, dashboard, profil, quota) | `app/api/views.py` |
| `urls.py` | Routing URL complet API + pea_project/urls.py | `app/api/urls.py` + `pea_project/urls.py` |
| `env_example.txt` | Template variables d'environnement (renommer en `.env`) | `.env` à la racine |
| `celery_app.py` | Configuration Celery + autodiscovery | `pea_project/celery.py` |
| `proj_init.py` | `__init__.py` projet pour charger Celery | `pea_project/__init__.py` |
| `client.js` | Client HTTP React — toutes les fonctions fetch | `frontend/src/api/client.js` |
| `hooks.js` | Hooks `useTitre` et `useTitres` | `frontend/src/hooks/` |
| `Dashboard.jsx` | Page principale — sidebar + onglets + stats | `frontend/src/pages/Dashboard.jsx` |
| `GraphiqueTechnique.jsx` | Graphique Lightweight Charts — chandeliers + indicateurs | `frontend/src/components/` |
| `FicheTitre.jsx` | Fiche complète titre — métriques, graphique, fondamentaux | `frontend/src/components/` |
| `utilitaires.jsx` | 7 composants : BadgeSentiment, CarteSignaux, FeedArticles, CarteAlertes, ListeSurveillance, PanneauAlertes, QuotaBadge | `frontend/src/components/` |
| `package.json` | Dépendances + structure complète + gabarits index.js/App.jsx/index.css | `frontend/package.json` |
| `notifications.py` | Service email HTML + Telegram bot + webhook JSON | `app/services/notifications.py` |
| `tasks_notif.py` | Tâches `notifier_alerte_task` et `digest_hebdomadaire_task` | À ajouter dans `app/tasks.py` |
| `notifications_config.txt` | Guide : variables .env, settings, bot Telegram, mot de passe Gmail | Référence |
| `pea_deploy.py` | Script Python de déploiement intelligent — scan ports, détection geoclic, config auto | Racine du projet |
| `update.sh` | Script mise à jour + commandes de maintenance | Racine du projet |
| `newsapi_client.py` | Client NewsAPI : recherche articles FR, headlines, import en base, quota | `app/services/newsapi_client.py` |
| `fmp.py` | Client FMP (stable API) : profil, ratios TTM, métriques clés, objectif analystes | `app/services/fmp.py` |
| `auto_fill.py` | Auto-remplissage titre : résolution ticker/ISIN/nom, métadonnées EODHD/FMP, éligibilité PEA, seuils alerte par secteur, nom court intelligent | `app/services/auto_fill.py` |
| `rss_news.py` | Collecteur RSS : Google News (1 an historique), Boursorama, Zonebourse — gratuit, illimité | `app/services/rss_news.py` |
| `reddit_client.py` | Collecteur Reddit : r/bourse, r/vosfinances, r/investir — API JSON publique, sans OAuth | `app/services/reddit_client.py` |

### Détails scoring LLM (`scoring_llm.py`)

- **Modèle scoring articles** : `mistral-small-latest` — rapide et économique
- **Modèle rédaction alertes** : `mistral-large-latest` — meilleure qualité narrative
- **Batch de 5 articles par appel** → 4 appels pour 20 articles au lieu de 20
- **Pondération sentiment global** : 65% presse / 35% social (profil PEA long terme)
- **Disclaimer garanti par le code** — ajouté programmatiquement si absent de la réponse LLM
- **Fiabilité historique** : calcule le % de patterns similaires ayant été suivis d'une hausse >2% en 10 jours
- **Digest hebdomadaire** : synthèse vendredi soir — `generer_digest_hebdomadaire()`

### Chaîne Celery complète (planning journalier)

```
09h00  fetch_news_gratuites_task   RSS (Google News, Boursorama, Zonebourse) + Reddit
                                    → scorer_articles_task → generer_sentiment_mixte
13h00  fetch_news_gratuites_task   idem (2e passage)
18h30  fetch_cours_eod_task        1 req EODHD batch (toutes les bougies du jour)
19h00  fetch_fondamentaux_lot_task  EODHD lot A/B + FMP complément
20h00  fetch_news_task             EODHD + NewsAPI + RSS + Reddit
                                    → scorer_articles_task → generer_sentiment_mixte
21h00  run_indicateurs_task        pandas-ta en local (RSI, MACD, MM, Bollinger)
         └→ detect_signaux_task   détecte signaux techniques
              └→ run_confluence_task  calcule score et crée Alerte
                   └→ scorer_alerte_task  génère texte IA via Mistral
Ven 19h  digest_hebdomadaire_task  synthèse semaine par Mistral
```

### Tâches déclenchées manuellement

```
analyse_complete_task(ticker)   à la création d'un titre — import OHLCV + indicateurs
                                + news 1 an (toutes sources) + scoring + rapport IA
POST /api/titres/{ticker}/analyser/   bouton "Analyser IA" dans le dashboard
```

---

## 11. Prochaines étapes validées

| # | Étape | Statut |
|---|---|---|
| 1 | Vision & architecture générale | ✅ Fait |
| 2 | Maquette dashboard (portefeuille + surveillance) | ✅ Fait |
| 3 | Moteur d'alertes & niveaux de confluence | ✅ Fait |
| 4 | Profil investisseur PEA + contraintes | ✅ Fait |
| 5 | Choix APIs financières | ✅ Fait |
| 6 | Stratégie quota gratuit + rotation Celery | ✅ Fait |
| 7 | **Modèles Django** (`models.py`) — 9 modèles | ✅ Fait |
| 8 | **Service EODHD** (`services/eodhd.py`) — collecteur avec quota + cache | ✅ Fait |
| 9 | **Tâches Celery** (`tasks/`) — 9 tâches chaînées | ✅ Fait |
| 10 | **Calcul indicateurs techniques** (`services/indicators.py`) — pandas-ta | ✅ Fait |
| 11 | **Scoring LLM** (`services/scoring_llm.py`) — sentiment + texte alertes | ✅ Fait |
| 12 | **Settings Django** + `.env.example` — toutes les variables configurées | ✅ Fait |
| 13 | **API REST Django** — 12 serializers · 17 endpoints · urls.py | ✅ Fait |
| 14 | **Frontend React** — Dashboard, FicheTitre, GraphiqueTechnique, tous composants | ✅ Fait |
| 15 | **Frontend React** — ListeSurveillance, PanneauAlertes, client API, hooks | ✅ Fait |
| 16 | **Notifications** — email HTML + Telegram bot + webhook générique | ✅ Fait |
| 17 | **Déploiement VPS OVH** — Nginx + Gunicorn + Celery + SSL Let's Encrypt | ✅ Fait |
| 18 | **Migration LLM** — Anthropic → Mistral AI (scoring_llm.py, settings, requirements) | ✅ Fait |
| 19 | **Déploiement VPS** — 51.210.8.158, Gunicorn:8002, Redis:6380, PostgreSQL partagé | ✅ Fait |
| 20 | **Auto-remplissage titre par IA** — saisir juste le ticker, l'IA complète place, pays, secteur, éligibilité PEA et seuils d'alerte automatiquement | ✅ Fait |
| 21 | **Intégration APIs serveur** — services NewsAPI + FMP + auto-fill déployés sur VPS | ✅ Fait |
| 22 | **Multi-sources news** — Google News RSS (1 an historique) + Boursorama + Zonebourse + Reddit (r/bourse, r/vosfinances) | ✅ Fait |
| 23 | **Sentiment technique IA** — score basé sur RSI/MACD/MM/Bollinger + rapport mixte (tech+presse) écrit par Mistral | ✅ Fait |
| 24 | **Bouton Analyser IA** — analyse complète manuelle (indicateurs + toutes sources news + scoring + rapport IA) | ✅ Fait |
| 25 | **Analyse auto à la création** — analyse_complete_task lancée automatiquement à l'ajout d'un titre (historique 1 an) | ✅ Fait |
| 26 | **Gestion titres dans le dashboard** — ajout (ticker/ISIN/nom), suppression réelle, déplacement portefeuille↔surveillance | ✅ Fait |
| 27 | **Position éditable** — nb actions + PRU + calcul PV/MV temps réel dans la fiche titre | ✅ Fait |
| 28 | **News gratuites planifiées** — RSS + Reddit à 9h et 13h lun-ven (illimité, sans quota) | ✅ Fait |

---

## Phase 2 — Intelligence artificielle avancée

### Objectif

Exploiter pleinement l'IA (Mistral) pour transformer les données brutes en aide à la décision actionnable. L'IA ne donne toujours **pas de conseils d'investissement** — elle observe, analyse, compare et signale. Formulation systématique : *"Cette observation ne constitue pas un conseil d'investissement."*

### Étapes Phase 2

| # | Étape | Description | Statut |
|---|---|---|---|
| 29 | **Chat IA contextuel** | Chat dans le dashboard pour poser des questions en langage naturel. L'IA accède à toutes les données du titre (cours, indicateurs, articles, fondamentaux, alertes, position). Ex : *"Est-ce le bon moment pour renforcer LVMH ?"*, *"Compare AB Science et GenSight"*, *"Résume la semaine pour mon portefeuille"*. Endpoint `/api/chat/` + composant React ChatIA. | ⬜ À faire |
| 30 | **Analyse fondamentale IA** | Quand les fondamentaux sont récupérés (PER, ROE, dette, croissance BPA, dividende), l'IA rédige une analyse qualitative : forces, faiblesses, positionnement sectoriel. Stocké dans `Fondamentaux.analyse_ia`. Mis à jour 2x/semaine avec les fondamentaux. | ⬜ À faire |
| 31 | **Détection de patterns graphiques** | L'IA analyse les séries de chandeliers et détecte les patterns classiques : double bottom, tête-épaules, triangle, canal, drapeau. Annotations visuelles (marqueurs) sur le graphique Lightweight Charts. Nouveau modèle `PatternDetecte`. | ⬜ À faire |
| 32 | **Recommandation de renforcement intelligent** | Quand un titre en portefeuille baisse significativement avec des fondamentaux solides, l'IA génère une observation contextuelle : *"AB Science a baissé de 15% ce mois. RSI à 35, fondamentaux stables. Historiquement, 4 rebonds sur 5 dans cette configuration sur ce titre."* Intégré dans les alertes de type `renforcement`. | ⬜ À faire |
| 33 | **Digest hebdomadaire enrichi** | Vendredi soir : synthèse IA de la semaine — faits marquants par titre, mouvements significatifs, opportunités détectées, risques identifiés, performance du portefeuille. Email HTML + notification Telegram. Remplace le digest basique existant. | ⬜ À faire |
| 34 | **Score de conviction IA** | Pour chaque titre, score 0-100 "conviction IA" combinant : technique (25%) + fondamentaux (35%) + sentiment presse (20%) + historique patterns (20%). Mis à jour quotidiennement. Affiché dans la fiche titre et la liste sidebar. Accompagné d'une explication 2-3 phrases. | ⬜ À faire |
| 35 | **Veille sectorielle** | L'IA surveille le secteur de chaque titre (Healthcare, Consumer Cyclical, etc.) et alerte si : un concurrent annonce des résultats impactants, une réglementation change, un événement macro affecte le secteur. Source : Google News par secteur. Nouveau type d'alerte `sectorielle`. | ⬜ À faire |

### Architecture technique Phase 2

```
┌─────────────────────────────────────────────────────────────┐
│                   CHAT IA (étape 29)                         │
│  React: ChatIA.jsx → POST /api/chat/                        │
│  Backend: services/chat_ia.py → Mistral large                │
│  Contexte injecté : cours, indicateurs, fondamentaux,        │
│  articles, alertes, position, profil investisseur            │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│              ANALYSES IA ENRICHIES (étapes 30-35)           │
│                                                               │
│  scoring_llm.py étendu :                                     │
│    + analyse_fondamentale_ia(ticker)      → Fondamentaux     │
│    + detecter_patterns(ticker)            → PatternDetecte   │
│    + evaluer_renforcement(ticker)         → Alerte           │
│    + generer_digest_enrichi()            → Notification      │
│    + calculer_score_conviction(ticker)   → ScoreConviction   │
│    + veille_sectorielle(secteur)         → Alerte            │
│                                                               │
│  Modèles Mistral :                                           │
│    mistral-small  → scoring batch, patterns                  │
│    mistral-large  → chat, analyses qualitatives, digest      │
└─────────────────────────────────────────────────────────────┘
```

### Ordre d'implémentation recommandé

1. **Chat IA** (étape 29) — le plus impactant, rend tout exploitable
2. **Score de conviction** (étape 34) — donne une vision synthétique rapide
3. **Analyse fondamentale IA** (étape 30) — enrichit la fiche titre
4. **Digest enrichi** (étape 33) — valeur ajoutée hebdomadaire
5. **Recommandation renforcement** (étape 32) — alertes actionnables
6. **Détection patterns** (étape 31) — visuel sur le graphique
7. **Veille sectorielle** (étape 35) — surveillance élargie

---

## 12. Décisions clés à ne pas oublier

- **Jamais de conseil d'investissement** : l'IA formule toujours en termes d'observation et de probabilité historique, jamais d'injonction.
- **Lightweight Charts** (TradingView, open source) pour les graphiques en chandeliers — pas Chart.js qui ne gère pas nativement l'OHLC.
- **yfinance interdit en production** — scraping non officiel, rate limiting, bans IP fréquents.
- **Le champ `lot`** sur le modèle `Titre` (`'A'` ou `'B'`) est assigné à l'ajout du titre et pilote la rotation Celery.
- **News mutualisée** : toujours 1 seul appel API avec tous les tickers en paramètre, jamais 1 appel par ticker.
- **L'import historique OHLC** se fait en 1 seul appel bulk EODHD au premier lancement — jamais re-téléchargé, seulement enrichi chaque soir de la bougie du jour.
- **Screener PEA** : 1 appel le 1er vendredi du mois pour mettre à jour la liste des titres éligibles — la table sert de filtre dans toute l'interface.
- **Deux modèles LLM distincts** : mistral-small-latest pour le scoring en lot (économie), mistral-large-latest pour la rédaction des alertes (qualité).
- **Le disclaimer** est ajouté programmatiquement dans `scoring_llm.py` — ne dépend pas du LLM.
- **Variables d'environnement** : toutes les clés API dans `.env` (ne jamais commiter). Clé LLM = `MISTRAL_API_KEY`.
- **Bot Telegram** : créer via @BotFather, récupérer le CHAT_ID via `getUpdates`, tester avec `_envoyer_telegram_texte()`.
- **Gmail SMTP** : utiliser un mot de passe d'application (pas le vrai mot de passe) — générer sur myaccount.google.com/apppasswords.
- **Déploiement intelligent** : `pea_deploy.py --install --domain X --email Y [--no-ssl]` scanne tous les ports occupés avant d'installer. Gunicorn PEA sur port 8002 (8000 docker, 8001 geoclic). Redis PEA sur 6380. Nginx testé (`nginx -t`) avant tout rechargement — geoclic ne peut pas être cassé.
- **Mise à jour** : `bash update.sh` — git pull + migrate + collectstatic + rebuild React + restart services.
- **VPS actuel** : ubuntu@51.210.8.158 (vps-78e9c3c9) — HTTP seul (pas de SSL), `default_server` Nginx sur l'IP.
- **API en AllowAny** : app mono-utilisateur personnelle, pas d'authentification — CSRF désactivé (SessionAuthentication retirée).
- **Sources news gratuites** : Google News RSS + Boursorama RSS + Zonebourse RSS + Reddit JSON = illimité. Planifié 9h + 13h lun-ven.
- **Analyse complète auto** : à la création d'un titre, `analyse_complete_task` lance : import OHLCV + indicateurs + news 1 an + scoring + rapport IA.
- **Suppression réelle** : `DELETE /api/titres/` fait un vrai delete (plus de soft delete) pour éviter les conflits à la re-création.
- **NUMBA_DISABLE_JIT=1** : requis dans supervisor pour pandas-ta (numba cache corrompu sur www-data).
- **EODHD tier gratuit** : 20 req/jour + historique limité à 1 an (256 bougies). Boutons période : 1S/1M/3M/6M/1A.
- **FMP stable API** : endpoints migrés de `/api/v3/` vers `/stable/` (symbol en query param).
- **Mistral import** : `from mistralai.client import Mistral` (pas `from mistralai import Mistral`) sur la version installée.
- **CSG 2026** : surveiller la hausse proposée de 9,2% à 10,6% (prélèvements sociaux de 17,2% → 18,6% si votée).

---

*Ce fichier est mis à jour à chaque session de travail. Pour reprendre le codage, partager ce fichier en contexte avec Claude et indiquer l'étape souhaitée.*
