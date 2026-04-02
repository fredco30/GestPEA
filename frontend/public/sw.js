// Service Worker minimal pour GestPEA PWA
// Permet l'installation sur l'écran d'accueil + cache basique

const CACHE_NAME = 'gestpea-v1'
const ASSETS_TO_CACHE = [
  '/',
  '/chatbot.png',
  '/banniereGestPEA.png',
]

// Installation : mettre en cache les assets statiques
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS_TO_CACHE))
  )
  self.skipWaiting()
})

// Activation : nettoyer les anciens caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  )
  self.clients.claim()
})

// Fetch : network-first (toujours les données fraîches, cache en fallback)
self.addEventListener('fetch', (event) => {
  // Ne pas intercepter les appels API
  if (event.request.url.includes('/api/')) return

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Mettre en cache la réponse fraîche
        if (response.ok && event.request.method === 'GET') {
          const clone = response.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone))
        }
        return response
      })
      .catch(() => caches.match(event.request))
  )
})
