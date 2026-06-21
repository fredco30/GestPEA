#!/usr/bin/env python3
"""
scripts/gestpea_ta_bridge.py
----------------------------
Pont GestPEA → TradingAgents.

Ce script est EXÉCUTÉ PAR LE VENV DE TRADINGAGENTS (Python 3.12 dédié), pas par
celui de Django : TradingAgents et GestPEA ont des dépendances incompatibles.
GestPEA le lance en sous-processus et lit le JSON émis sur stdout.

Usage :
    /var/www/tradingagents/venv/bin/python scripts/gestpea_ta_bridge.py TICKER YYYY-MM-DD

Sortie : un JSON encadré par la sentinelle ===GESTPEA_TA_JSON=== sur stdout
(le reste du flux — logs éventuels — est ignoré par l'appelant).

Config :
  - GESTPEA_TA_ENV : chemin du .env TradingAgents (défaut /var/www/tradingagents/.env)
    → y lire MISTRAL_API_KEY, FRED_API_KEY (optionnel), TRADINGAGENTS_* éventuels.
"""
import os
import sys
import json

SENTINEL = "===GESTPEA_TA_JSON==="


def _emit(payload: dict, code: int = 0):
    """Émet le JSON encadré par les sentinelles et termine."""
    print(SENTINEL)
    print(json.dumps(payload, ensure_ascii=False))
    print(SENTINEL)
    sys.exit(code)


def main():
    if len(sys.argv) < 3:
        _emit({"ok": False, "error": "usage: gestpea_ta_bridge.py TICKER YYYY-MM-DD"}, 2)

    ticker = sys.argv[1].strip()
    analyse_date = sys.argv[2].strip()

    ta_env = os.environ.get("GESTPEA_TA_ENV", "/var/www/tradingagents/.env")

    # Se placer dans le dossier de TradingAgents AVANT tout import : certaines
    # dépendances (pydantic-settings / dotenv) cherchent un ".env" en remontant
    # depuis le cwd — sans ça, lancé depuis /var/www/pea, le sous-processus tente
    # de lire le .env de Django (interdit à www-data → PermissionError).
    ta_home = os.path.dirname(ta_env) or "."
    try:
        if os.path.isdir(ta_home):
            os.chdir(ta_home)
    except Exception:
        pass

    # Charger le .env TradingAgents (clés Mistral / FRED)
    try:
        from dotenv import load_dotenv
        load_dotenv(ta_env)
    except Exception:
        pass  # python-dotenv absent ou .env introuvable → on tente avec l'env courant

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"]            = os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "mistral")
    config["deep_think_llm"]          = os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "mistral-large-latest")
    config["quick_think_llm"]         = os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "mistral-small-latest")
    config["max_debate_rounds"]       = int(os.environ.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"))
    config["max_risk_discuss_rounds"] = 1

    # L'analyste "news" appelle un outil macro (FRED). Sans clé FRED, on l'écarte
    # pour éviter FredNotConfiguredError tout en gardant une analyse exploitable.
    analystes = ("market", "social", "news", "fundamentals")
    if not os.environ.get("FRED_API_KEY"):
        analystes = ("market", "social", "fundamentals")

    ta = TradingAgentsGraph(selected_analysts=analystes, debug=False, config=config)
    final_state, decision = ta.propagate(ticker, analyse_date)

    def section(cle):
        val = final_state.get(cle) if isinstance(final_state, dict) else None
        if val is None:
            return ""
        return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)

    _emit({
        "ok": True,
        "ticker": ticker,
        "date": analyse_date,
        "note": str(decision).strip(),
        "analystes": list(analystes),
        "market_report":       section("market_report"),
        "fundamentals_report": section("fundamentals_report"),
        "sentiment_report":    section("sentiment_report"),
        "news_report":         section("news_report"),
        "trader_plan":         section("trader_investment_plan"),
        "final_decision":      section("final_trade_decision"),
    })


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}"}, 1)
