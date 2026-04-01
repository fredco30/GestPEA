"""
services/chat_ia.py
--------------------
Service de chat IA contextuel (Phase 2 — étape 29).

Permet à l'utilisateur de poser des questions en langage naturel.
L'IA (Mistral large) accède aux données du titre sélectionné
(cours, indicateurs, fondamentaux, sentiment, articles, alertes, position)
et au profil investisseur pour répondre de manière contextuelle.

Contraintes :
  - Pas de conseil d'investissement
  - Disclaimer obligatoire
  - Réponse en français, ton professionnel
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings

from app.models import (
    Titre, PrixJournalier, Fondamentaux, ScoreSentiment,
    Article, Signal, Alerte, ProfilInvestisseur,
)
from app.services.scoring_llm import _get_client

logger = logging.getLogger(__name__)

MODEL_CHAT = "mistral-large-latest"
MAX_TOKENS_CHAT = 1500

DISCLAIMER = (
    "Cette réponse ne constitue pas un conseil d'investissement. "
    "Elle repose sur des observations factuelles et des indicateurs techniques."
)

SYSTEM_PROMPT = """Tu es l'assistant IA du dashboard PEA. Tu aides l'utilisateur à comprendre \
ses titres et son portefeuille. Tu as accès aux données en temps réel ci-dessous.

RÈGLES ABSOLUES :
- Tu ne donnes JAMAIS de conseil d'investissement (ni acheter, ni vendre, ni investir).
- Tu utilises : "point d'entrée potentiel", "opportunité à étudier", "signal à surveiller".
- Tu termines TOUJOURS par : "⚠️ Cette observation ne constitue pas un conseil d'investissement."
- Tu réponds en français, ton professionnel mais accessible.
- Tu cites les chiffres précis quand ils sont disponibles.
- Si tu n'as pas assez de données, dis-le honnêtement.

CONTEXTE UTILISATEUR :
{contexte}
"""


def _decimal_to_str(val):
    """Convertit Decimal en string pour le prompt."""
    if val is None:
        return "N/A"
    if isinstance(val, Decimal):
        return str(round(float(val), 4))
    return str(val)


def _build_titre_context(ticker):
    """Construit le contexte complet d'un titre."""
    try:
        titre = Titre.objects.get(ticker=ticker, actif=True)
    except Titre.DoesNotExist:
        return f"Titre {ticker} non trouvé."

    lines = [f"## Titre : {titre.nom} ({titre.ticker}) — {titre.secteur or 'Secteur inconnu'}"]
    lines.append(f"Statut : {titre.statut}")

    # Position portefeuille
    if titre.nb_actions and titre.nb_actions > 0:
        lines.append(f"Position : {titre.nb_actions} actions, PRU {_decimal_to_str(titre.prix_revient_moyen)} €")
        lines.append(f"Valeur position : {_decimal_to_str(titre.valeur_position)} €")
        lines.append(f"Plus/Moins-value : {_decimal_to_str(titre.plus_moins_value)} €")

    # Cours récents (5 derniers jours)
    prix = PrixJournalier.objects.filter(titre=titre).order_by('-date')[:5]
    if prix:
        lines.append("\n### Cours récents (5 derniers jours)")
        for p in prix:
            lines.append(
                f"  {p.date} : clôture {_decimal_to_str(p.cloture)} € | "
                f"RSI {_decimal_to_str(p.rsi_14)} | MACD hist {_decimal_to_str(p.macd_hist)} | "
                f"MM50 {_decimal_to_str(p.mm_50)} | MM200 {_decimal_to_str(p.mm_200)}"
            )

    # Fondamentaux
    fonda = Fondamentaux.objects.filter(titre=titre).order_by('-date_maj').first()
    if fonda:
        lines.append("\n### Fondamentaux")
        lines.append(f"  PER {_decimal_to_str(fonda.per)} | ROE {_decimal_to_str(fonda.roe)}% | "
                     f"Dette/EBITDA {_decimal_to_str(fonda.dette_nette_ebitda)} | "
                     f"Marge nette {_decimal_to_str(fonda.marge_nette)}%")
        lines.append(f"  Croissance BPA 3 ans {_decimal_to_str(fonda.croissance_bpa_3ans)}% | "
                     f"Dividende {_decimal_to_str(fonda.rendement_dividende)}% | "
                     f"Consensus {fonda.consensus or 'N/A'} | "
                     f"Score qualité {_decimal_to_str(fonda.score_qualite)}/10")

    # Sentiment (7 derniers jours)
    sentiments = ScoreSentiment.objects.filter(
        titre=titre, date__gte=date.today() - timedelta(days=7)
    ).order_by('-date')[:5]
    if sentiments:
        lines.append("\n### Sentiment récent")
        for s in sentiments:
            lines.append(f"  {s.date} [{s.source}] : {_decimal_to_str(s.score)} ({s.label})")
            if s.resume_ia:
                lines.append(f"    → {s.resume_ia[:200]}")

    # Articles récents (5)
    articles = Article.objects.filter(titre=titre).order_by('-date_pub')[:5]
    if articles:
        lines.append("\n### Articles récents")
        for a in articles:
            score_str = _decimal_to_str(a.score_sentiment) if a.score_sentiment else "non scoré"
            lines.append(f"  [{a.source}] {a.titre_art} (sentiment: {score_str})")

    # Signaux actifs
    signaux = Signal.objects.filter(titre=titre, actif=True).order_by('-date')[:10]
    if signaux:
        lines.append("\n### Signaux techniques actifs")
        for sig in signaux:
            lines.append(f"  {sig.date} : {sig.get_type_signal_display()} — {sig.direction} ({sig.description or ''})")

    # Alertes récentes
    alertes = Alerte.objects.filter(titre=titre).order_by('-date_detection')[:3]
    if alertes:
        lines.append("\n### Alertes récentes")
        for al in alertes:
            lines.append(f"  {al.date_detection} : score {_decimal_to_str(al.score_confluence)}/10 "
                         f"[{al.niveau}] — {al.statut}")
            if al.texte_ia:
                lines.append(f"    → {al.texte_ia[:200]}")

    return "\n".join(lines)


def _build_portfolio_context():
    """Construit le contexte de tout le portefeuille."""
    titres = Titre.objects.filter(statut='portefeuille', actif=True)
    if not titres.exists():
        return "Aucun titre en portefeuille."

    lines = ["## Portefeuille"]
    valeur_totale = Decimal('0')

    for t in titres:
        dernier = PrixJournalier.objects.filter(titre=t).order_by('-date').first()
        cours = _decimal_to_str(dernier.cloture) if dernier else "N/A"
        val_pos = t.valeur_position or Decimal('0')
        valeur_totale += val_pos

        sentiment = ScoreSentiment.objects.filter(
            titre=t, source='global'
        ).order_by('-date').first()
        sent_str = _decimal_to_str(sentiment.score) if sentiment else "N/A"

        lines.append(
            f"  {t.nom_court or t.nom} ({t.ticker}) : {t.nb_actions or 0} actions, "
            f"cours {cours} €, PV/MV {_decimal_to_str(t.plus_moins_value)} €, "
            f"sentiment {sent_str}"
        )

    lines.append(f"\nValeur totale portefeuille : {_decimal_to_str(valeur_totale)} €")
    return "\n".join(lines)


def _build_profil_context():
    """Contexte du profil investisseur."""
    profil = ProfilInvestisseur.objects.first()
    if not profil:
        return "Profil investisseur non configuré."

    return (
        f"## Profil investisseur\n"
        f"  Enveloppe : {profil.get_enveloppe_display()}\n"
        f"  Horizon : {profil.horizon_min_ans}-{profil.horizon_max_ans} ans\n"
        f"  Style : {profil.get_style_display()}\n"
        f"  Tolérance risque : {profil.get_tolerance_risque_display()}\n"
        f"  Poids fondamentaux/technique : {profil.poids_fondamentaux}/{profil.poids_technique}\n"
        f"  Capacité versement restante : {_decimal_to_str(profil.capacite_versement_restante)} €\n"
        f"  Fiscalité pleine (>5 ans) : {'Oui' if profil.fiscalite_pleine else 'Non'}"
    )


def chat_ia(question, ticker=None):
    """
    Point d'entrée principal du chat IA.

    Args:
        question: La question de l'utilisateur en langage naturel.
        ticker: Ticker du titre en contexte (optionnel).

    Returns:
        str: La réponse de l'IA.
    """
    # Construire le contexte
    context_parts = [_build_profil_context()]

    if ticker:
        context_parts.append(_build_titre_context(ticker))

    # Détection de questions portefeuille
    mots_portefeuille = ['portefeuille', 'portfolio', 'mes titres', 'mes actions', 'position']
    if any(mot in question.lower() for mot in mots_portefeuille):
        context_parts.append(_build_portfolio_context())

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
