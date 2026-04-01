"""
services/chat_ia.py
--------------------
Service de chat IA contextuel (Phase 2 — étape 29).

Permet à l'utilisateur de poser des questions en langage naturel.
L'IA (Mistral large) accède à TOUTES les données du dashboard :
portefeuille, surveillance, alertes, articles, sentiment, fondamentaux,
indicateurs techniques, position et profil investisseur.

Contraintes :
  - Pas de conseil d'investissement
  - Disclaimer obligatoire
  - Réponse en français, ton professionnel
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Max

from app.models import (
    Titre, PrixJournalier, Fondamentaux, ScoreSentiment,
    Article, Signal, Alerte, ProfilInvestisseur, DocumentTitre,
)
from app.services.scoring_llm import _get_client

logger = logging.getLogger(__name__)

MODEL_CHAT = "mistral-large-latest"
MAX_TOKENS_CHAT = 2000

DISCLAIMER = (
    "Cette réponse ne constitue pas un conseil d'investissement. "
    "Elle repose sur des observations factuelles et des indicateurs techniques."
)

SYSTEM_PROMPT = """Tu es l'assistant IA du dashboard PEA. Tu as accès à TOUTES les données \
du dashboard de l'utilisateur : portefeuille, liste de surveillance, alertes, actualités, \
indicateurs techniques, fondamentaux, sentiment et profil investisseur.

L'UTILISATEUR EST UN DÉBUTANT — il n'a AUCUNE connaissance technique en bourse.

RÈGLES ABSOLUES :
- Tu ne donnes JAMAIS de conseil d'investissement (ni acheter, ni vendre, ni investir).
- Tu utilises : "point d'entrée potentiel", "opportunité à étudier", "signal à surveiller".
- Tu termines TOUJOURS par : "⚠️ Cette observation ne constitue pas un conseil d'investissement."
- Tu réponds en français, ton professionnel mais ACCESSIBLE pour un débutant.
- TOUJOURS indiquer des NIVEAUX DE PRIX CONCRETS EN EUROS :
  • "Zone de support (plancher) autour de XX €" (calculé à partir de MM50, MM200 ou Bollinger bas)
  • "Zone de résistance (plafond) vers XX €" (calculé à partir de Bollinger haut ou plus hauts récents)
  • "Zone d'entrée potentielle entre XX € et XX €" quand pertinent
  • "Objectif des analystes : XX €" si disponible
- PAS de jargon technique brut (pas "RSI à 35", mais "le titre semble survendu, il se rapproche d'un plancher technique autour de XX €")
- Traduire chaque indicateur en ce que ça signifie concrètement pour le prix
- Si tu n'as pas assez de données, dis-le honnêtement.
- Quand l'utilisateur parle d'un titre en particulier, concentre-toi dessus mais utilise
  le contexte global (portefeuille, autres titres, alertes) pour enrichir ta réponse.

DONNÉES COMPLÈTES DU DASHBOARD :
{contexte}
"""


def _decimal_to_str(val):
    """Convertit Decimal en string pour le prompt."""
    if val is None:
        return "N/A"
    if isinstance(val, Decimal):
        return str(round(float(val), 4))
    return str(val)


# ---------------------------------------------------------------------------
# Contexte d'un titre individuel (détaillé)
# ---------------------------------------------------------------------------

def _build_titre_detail(titre):
    """Construit le contexte détaillé d'un titre."""
    lines = [f"### {titre.nom} ({titre.ticker}) — {titre.secteur or 'Secteur inconnu'} — statut: {titre.statut}"]

    # Position portefeuille
    if titre.nb_actions and titre.nb_actions > 0:
        lines.append(f"  Position : {titre.nb_actions} actions, PRU {_decimal_to_str(titre.prix_revient_moyen)} €, "
                     f"valeur {_decimal_to_str(titre.valeur_position)} €, "
                     f"PV/MV {_decimal_to_str(titre.plus_moins_value)} €")

    # Cours récents (5 derniers jours)
    prix = PrixJournalier.objects.filter(titre=titre).order_by('-date')[:5]
    if prix:
        for p in prix:
            lines.append(
                f"  {p.date} : {_decimal_to_str(p.cloture)} € | "
                f"RSI {_decimal_to_str(p.rsi_14)} | MACD {_decimal_to_str(p.macd_hist)} | "
                f"MM50 {_decimal_to_str(p.mm_50)} | MM200 {_decimal_to_str(p.mm_200)} | "
                f"Boll [{_decimal_to_str(p.boll_inf)}-{_decimal_to_str(p.boll_sup)}] | "
                f"Vol ratio {_decimal_to_str(p.volume_ratio)}"
            )

    # Fondamentaux
    fonda = Fondamentaux.objects.filter(titre=titre).order_by('-date_maj').first()
    if fonda:
        lines.append(f"  Fondamentaux : PER {_decimal_to_str(fonda.per)} | PER fwd {_decimal_to_str(fonda.per_forward)} | "
                     f"ROE {_decimal_to_str(fonda.roe)}% | Marge nette {_decimal_to_str(fonda.marge_nette)}% | "
                     f"Dette/EBITDA {_decimal_to_str(fonda.dette_nette_ebitda)} | "
                     f"Croiss. BPA 3a {_decimal_to_str(fonda.croissance_bpa_3ans)}% | "
                     f"Div {_decimal_to_str(fonda.rendement_dividende)}% | "
                     f"Consensus {fonda.consensus or 'N/A'} ({fonda.nb_analystes or 0} analystes) | "
                     f"Objectif {_decimal_to_str(fonda.objectif_cours_moyen)} € | "
                     f"Score qualité {_decimal_to_str(fonda.score_qualite)}/10")

    # Sentiment récent
    sentiments = ScoreSentiment.objects.filter(
        titre=titre, date__gte=date.today() - timedelta(days=7)
    ).order_by('-date', 'source')[:6]
    for s in sentiments:
        resume = f" — {s.resume_ia[:150]}" if s.resume_ia else ""
        lines.append(f"  Sentiment {s.date} [{s.source}] : {_decimal_to_str(s.score)} ({s.label}){resume}")

    # Signaux actifs
    signaux = Signal.objects.filter(titre=titre, actif=True).order_by('-date')[:8]
    if signaux:
        sig_list = ", ".join(f"{sig.get_type_signal_display()} ({sig.direction})" for sig in signaux)
        lines.append(f"  Signaux actifs : {sig_list}")

    # Articles récents (5)
    articles = Article.objects.filter(titre=titre).order_by('-date_pub')[:5]
    if articles:
        for a in articles:
            score_str = _decimal_to_str(a.score_sentiment) if a.score_sentiment else "?"
            lines.append(f"  Article [{a.source}] {a.titre_art} (sent: {score_str})")

    # Documents uploadés (résumés IA)
    documents = DocumentTitre.objects.filter(titre=titre).order_by('-date_upload')[:5]
    if documents:
        lines.append("\n### Documents ajoutés par l'utilisateur")
        for doc in documents:
            lines.append(f"  [{doc.get_type_doc_display()}] {doc.nom} ({doc.date_upload.strftime('%d/%m/%Y')})")
            if doc.resume_ia:
                lines.append(f"    Résumé : {doc.resume_ia[:300]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Contexte résumé d'un titre (pour la vue globale)
# ---------------------------------------------------------------------------

def _build_titre_summary(titre):
    """Résumé court d'un titre pour le contexte global."""
    dernier = PrixJournalier.objects.filter(titre=titre).order_by('-date').first()
    cours = _decimal_to_str(dernier.cloture) if dernier else "?"
    rsi = _decimal_to_str(dernier.rsi_14) if dernier and dernier.rsi_14 else "?"
    variation = _decimal_to_str(dernier.variation_pct) if dernier else "?"

    sentiment = ScoreSentiment.objects.filter(titre=titre, source='global').order_by('-date').first()
    sent_str = f"{_decimal_to_str(sentiment.score)} ({sentiment.label})" if sentiment else "N/A"

    pos = ""
    if titre.nb_actions and titre.nb_actions > 0:
        pos = f" | {titre.nb_actions} actions, PV/MV {_decimal_to_str(titre.plus_moins_value)} €"

    return (f"  {titre.nom_court or titre.nom} ({titre.ticker}) : {cours} € "
            f"(var {variation}%) | RSI {rsi} | Sentiment {sent_str}{pos}")


# ---------------------------------------------------------------------------
# Blocs de contexte global
# ---------------------------------------------------------------------------

def _build_portfolio_context():
    """Contexte complet du portefeuille."""
    titres = Titre.objects.filter(statut='portefeuille', actif=True)
    if not titres.exists():
        return "## Portefeuille\nAucun titre en portefeuille."

    lines = ["## Portefeuille"]
    valeur_totale = Decimal('0')
    pv_totale = Decimal('0')

    for t in titres:
        val_pos = t.valeur_position or Decimal('0')
        pv = t.plus_moins_value or Decimal('0')
        valeur_totale += val_pos
        pv_totale += pv
        lines.append(_build_titre_summary(t))

    lines.append(f"  TOTAL : {_decimal_to_str(valeur_totale)} € | PV/MV globale : {_decimal_to_str(pv_totale)} €")
    return "\n".join(lines)


def _build_surveillance_context():
    """Contexte de la liste de surveillance."""
    titres = Titre.objects.filter(statut='surveillance', actif=True)
    if not titres.exists():
        return "## Surveillance\nAucun titre en surveillance."

    lines = ["## Surveillance"]
    for t in titres:
        lines.append(_build_titre_summary(t))
    return "\n".join(lines)


def _build_alertes_context():
    """Toutes les alertes récentes (14 jours)."""
    alertes = Alerte.objects.filter(
        date_detection__gte=date.today() - timedelta(days=14)
    ).select_related('titre').order_by('-date_detection')[:10]

    if not alertes:
        return "## Alertes récentes\nAucune alerte ces 14 derniers jours."

    lines = ["## Alertes récentes (14 jours)"]
    for al in alertes:
        ticker = al.titre.ticker if al.titre else "?"
        texte = f" — {al.texte_ia[:200]}" if al.texte_ia else ""
        lines.append(
            f"  {al.date_detection} {ticker} : score {_decimal_to_str(al.score_confluence)}/10 "
            f"[{al.niveau}] statut={al.statut}{texte}"
        )
    return "\n".join(lines)


def _build_articles_context():
    """Articles récents tous titres confondus (7 jours)."""
    articles = Article.objects.filter(
        date_pub__gte=date.today() - timedelta(days=7)
    ).select_related('titre').order_by('-date_pub')[:15]

    if not articles:
        return "## Actualités récentes\nAucun article ces 7 derniers jours."

    lines = ["## Actualités récentes (7 jours)"]
    for a in articles:
        ticker = a.titre.ticker if a.titre else "?"
        score_str = _decimal_to_str(a.score_sentiment) if a.score_sentiment else "?"
        lines.append(f"  [{a.source}] {ticker} : {a.titre_art} (sent: {score_str})")
    return "\n".join(lines)


def _build_profil_context():
    """Contexte du profil investisseur."""
    profil = ProfilInvestisseur.objects.first()
    if not profil:
        return "## Profil investisseur\nNon configuré."

    return (
        f"## Profil investisseur\n"
        f"  Enveloppe : {profil.get_enveloppe_display()} | "
        f"Horizon : {profil.horizon_min_ans}-{profil.horizon_max_ans} ans | "
        f"Style : {profil.get_style_display()} | "
        f"Risque : {profil.get_tolerance_risque_display()}\n"
        f"  Poids fondamentaux/technique : {profil.poids_fondamentaux}/{profil.poids_technique} | "
        f"Capacité versement : {_decimal_to_str(profil.capacite_versement_restante)} € | "
        f"Fiscalité pleine : {'Oui' if profil.fiscalite_pleine else 'Non'}"
    )


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def chat_ia(question, ticker=None):
    """
    Point d'entrée principal du chat IA.
    Injecte TOUJOURS le contexte complet du dashboard.
    Si un ticker est sélectionné, ajoute ses données détaillées en priorité.

    Args:
        question: La question de l'utilisateur en langage naturel.
        ticker: Ticker du titre actuellement sélectionné (optionnel).

    Returns:
        str: La réponse de l'IA.
    """
    # Toujours injecter le contexte global
    context_parts = [
        _build_profil_context(),
        _build_portfolio_context(),
        _build_surveillance_context(),
        _build_alertes_context(),
        _build_articles_context(),
    ]

    # Si un titre est sélectionné, ajouter son détail complet en premier
    if ticker:
        context_parts.insert(1, f"## Titre actuellement sélectionné (focus)\n{_build_titre_detail(Titre.objects.filter(ticker=ticker, actif=True).first())}"
                             if Titre.objects.filter(ticker=ticker, actif=True).exists()
                             else f"## Titre sélectionné : {ticker} (non trouvé)")

    contexte = "\n\n".join(context_parts)
    system_msg = SYSTEM_PROMPT.format(contexte=contexte)

    try:
        client = _get_client()
        response = client.chat.complete(
            model=MODEL_CHAT,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": question},
            ],
            max_tokens=MAX_TOKENS_CHAT,
            temperature=0.3,
        )
        return response.choices[0].message.content

    except Exception as e:
        logger.error("Erreur chat IA : %s", e, exc_info=True)
        return (
            "Désolé, je n'ai pas pu traiter votre question pour le moment. "
            "Veuillez réessayer dans quelques instants.\n\n"
            f"⚠️ {DISCLAIMER}"
        )
