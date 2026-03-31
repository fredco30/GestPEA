/**
 * frontend/src/hooks/useTitre.js
 * --------------------------------
 * Hook principal pour la fiche d'un titre.
 * Gere le chargement des donnees, le rafraichissement et les mutations.
 */

import { useState, useEffect, useCallback } from 'react'
import { getTitreDetail, getOHLC, updateTitre } from '../api/client'

export function useTitre(ticker) {
  const [titre,      setTitre]      = useState(null)
  const [ohlc,       setOhlc]       = useState(null)
  const [periode,    setPeriode]    = useState('1A')
  const [loading,    setLoading]    = useState(!!ticker)  // FIX: true when ticker provided
  const [loadingOhlc,setLoadingOhlc] = useState(false)
  const [error,      setError]      = useState(null)

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

  useEffect(() => { chargerTitre() }, [chargerTitre])
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
