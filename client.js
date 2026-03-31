/**
 * frontend/src/api/client.js
 * --------------------------
 * Client HTTP centralisé pour tous les appels vers l'API Django REST.
 * Toutes les fonctions retournent des Promises.
 *
 * Usage :
 *   import { getTitres, getTitreDetail, getOHLC } from './api/client'
 *   const titres = await getTitres('portefeuille')
 */

const BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api'

// ---------------------------------------------------------------------------
// Utilitaire HTTP
// ---------------------------------------------------------------------------

async function request(endpoint, options = {}) {
  const url = `${BASE_URL}${endpoint}`
  const config = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    credentials: 'include', // session Django
    ...options,
  }

  const response = await fetch(url, config)

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw Object.assign(new Error(error.detail || 'Erreur API'), {
      status: response.status,
      data:   error,
    })
  }

  if (response.status === 204) return null
  return response.json()
}

const get    = (url, params = {}) => {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null)
  ).toString()
  return request(`${url}${qs ? '?' + qs : ''}`)
}
const post   = (url, body)  => request(url, { method: 'POST',   body: JSON.stringify(body) })
const patch  = (url, body)  => request(url, { method: 'PATCH',  body: JSON.stringify(body) })
const del    = (url)        => request(url, { method: 'DELETE' })

// ---------------------------------------------------------------------------
// TITRES
// ---------------------------------------------------------------------------

/** Liste tous les titres. statut = 'portefeuille' | 'surveillance' | 'tous' */
export const getTitres = (statut = 'tous') =>
  get('/titres/', { statut })

/** Fiche complète : cours 90j, fondamentaux, sentiment 30j, alertes, articles */
export const getTitreDetail = (ticker) =>
  get(`/titres/${ticker}/`)

/** Ajouter un titre (déclenche l'import historique en arrière-plan) */
export const createTitre = (data) =>
  post('/titres/', data)

/** Modifier un titre (statut, notes, nb_actions, prix_revient_moyen…) */
export const updateTitre = (ticker, data) =>
  patch(`/titres/${ticker}/`, data)

/** Archiver un titre (soft delete) */
export const deleteTitre = (ticker) =>
  del(`/titres/${ticker}/`)

/**
 * Bougies OHLC + indicateurs au format Lightweight Charts
 * periode = '1S' | '1M' | '3M' | '6M' | '1A' | '3A' | 'MAX'
 */
export const getOHLC = (ticker, periode = '1A') =>
  get(`/titres/${ticker}/ohlc/`, { periode })

/** Relancer l'import historique bulk */
export const importerHistorique = (ticker) =>
  post(`/titres/${ticker}/importer/`, {})

/** Lire la config alertes d'un titre */
export const getConfigAlertes = (ticker) =>
  get(`/titres/${ticker}/config/`)

/** Modifier la config alertes d'un titre */
export const updateConfigAlertes = (ticker, data) =>
  patch(`/titres/${ticker}/config/`, data)

// ---------------------------------------------------------------------------
// ALERTES
// ---------------------------------------------------------------------------

/**
 * Liste des alertes avec filtres optionnels
 * params: { statut, niveau, ticker, depuis, limit }
 */
export const getAlertes = (params = {}) =>
  get('/alertes/', params)

/** Détail d'une alerte (avec signaux) */
export const getAlerteDetail = (id) =>
  get(`/alertes/${id}/`)

/** Marquer une alerte vue/archivée + ajouter une note */
export const updateStatutAlerte = (id, data) =>
  patch(`/alertes/${id}/statut/`, data)

// ---------------------------------------------------------------------------
// SENTIMENT
// ---------------------------------------------------------------------------

/**
 * Scores sentiment + articles récents pour un ticker
 * jours = 7 | 14 | 30
 */
export const getSentiment = (ticker, jours = 14) =>
  get(`/sentiment/${ticker}/`, { jours })

// ---------------------------------------------------------------------------
// DASHBOARD
// ---------------------------------------------------------------------------

export const getDashboard = () =>
  get('/dashboard/')

// ---------------------------------------------------------------------------
// PROFIL
// ---------------------------------------------------------------------------

export const getProfil = () =>
  get('/profil/')

export const updateProfil = (data) =>
  patch('/profil/', data)

// ---------------------------------------------------------------------------
// QUOTA
// ---------------------------------------------------------------------------

export const getQuota = () =>
  get('/quota/')
