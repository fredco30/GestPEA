/**
 * frontend/src/hooks/useTitres.js
 * --------------------------------
 * Hook pour la liste des titres (portefeuille ou surveillance)
 */

import { useState, useEffect, useCallback } from 'react'
import { getTitres, createTitre, deleteTitre } from '../api/client'

export function useTitres(statut = 'tous') {
  const [titres,  setTitres]  = useState([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

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
