/**
 * frontend/src/hooks/useTitre.js
 * --------------------------------
 * Hook principal pour la fiche d'un titre.
 * Gere le chargement des donnees, le rafraichissement et les mutations.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { getTitreDetail, getOHLC, updateTitre, refreshCours } from '../api/client'

export function useTitre(ticker) {
  const [titre,      setTitre]      = useState(null)
  const [ohlc,       setOhlc]       = useState(null)
  const [periode,    setPeriode]    = useState('1A')
  const [loading,    setLoading]    = useState(!!ticker)
  const [loadingOhlc,setLoadingOhlc] = useState(false)
  const [error,      setError]      = useState(null)
  const refreshedRef = useRef({})

  const chargerTitre = useCallback(async () => {
    if (!ticker) return
    setLoading(true)
    setError(null)
    try {
      const data = await getTitreDetail(ticker)
      setTitre(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [ticker])

  const chargerOhlc = useCallback(async (p = periode) => {
    if (!ticker) return
    setLoadingOhlc(true)
    try {
      const data = await getOHLC(ticker, p)
      setOhlc(data)
    } catch (e) {
      console.error('[useTitre] OHLC:', e)
    } finally {
      setLoadingOhlc(false)
    }
  }, [ticker, periode])

  // Au chargement : refresh yfinance en arrière-plan puis recharger les données
  useEffect(() => {
    if (!ticker) return
    chargerTitre()

    // Refresh yfinance max 1 fois par ticker par session (évite les appels répétés)
    const now = Date.now()
    const lastRefresh = refreshedRef.current[ticker] || 0
    if (now - lastRefresh > 60000) { // 1 minute minimum entre 2 refresh du même ticker
      refreshedRef.current[ticker] = now
      refreshCours(ticker)
        .then(() => {
          // Recharger les données avec le cours frais
          chargerTitre()
          chargerOhlc(periode)
        })
        .catch(() => {}) // silencieux si échec
    }
  }, [ticker]) // eslint-disable-line

  useEffect(() => { chargerOhlc(periode) }, [ticker, periode]) // eslint-disable-line

  // FIX: only set period state, let the useEffect handle the fetch (no direct chargerOhlc call)
  const changerPeriode = (p) => {
    setPeriode(p)
  }

  const modifierTitre = async (data) => {
    const updated = await updateTitre(ticker, data)
    setTitre(prev => ({ ...prev, ...updated }))
    return updated
  }

  return {
    titre, ohlc, periode, loading, loadingOhlc, error,
    changerPeriode, modifierTitre, rafraichir: chargerTitre,
  }
}
