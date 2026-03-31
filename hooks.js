/**
 * frontend/src/hooks/useTitre.js
 * --------------------------------
 * Hook principal pour la fiche d'un titre.
 * Gère le chargement des données, le rafraîchissement et les mutations.
 */

import { useState, useEffect, useCallback } from 'react'
import { getTitreDetail, getOHLC, updateTitre } from '../api/client'

export function useTitre(ticker) {
  const [titre,      setTitre]    = useState(null)
  const [ohlc,       setOhlc]     = useState(null)
  const [periode,    setPeriode]  = useState('1A')
  const [loading,    setLoading]  = useState(false)
  const [loadingOhlc,setLoadingOhlc] = useState(false)
  const [error,      setError]    = useState(null)

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

  const changerPeriode = (p) => {
    setPeriode(p)
    chargerOhlc(p)
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

// ---------------------------------------------------------------------------

/**
 * frontend/src/hooks/useTitres.js
 * Hook pour la liste des titres (portefeuille ou surveillance)
 */
export function useTitres(statut = 'tous') {
  const [titres,  setTitres]  = useState([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  const { getTitres, createTitre, deleteTitre } = require('../api/client')

  const charger = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getTitres(statut)
      setTitres(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [statut])

  useEffect(() => { charger() }, [charger])

  const ajouter = async (data) => {
    const titre = await createTitre(data)
    setTitres(prev => [...prev, titre])
    return titre
  }

  const archiver = async (ticker) => {
    await deleteTitre(ticker)
    setTitres(prev => prev.filter(t => t.ticker !== ticker))
  }

  return { titres, loading, error, ajouter, archiver, rafraichir: charger }
}
