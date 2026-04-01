/**
 * frontend/src/components/ChatIA.jsx
 * ------------------------------------
 * Chat IA contextuel — Phase 2, étape 29.
 * Panel flottant en bas à droite du dashboard.
 * Envoie les questions à POST /api/chat/ avec le ticker actif en contexte.
 */

import React, { useState, useRef, useEffect, useCallback } from 'react'
import { chatIA } from '../api/client'

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const STYLES = {
  // Bouton flottant
  fab: {
    position: 'fixed',
    bottom: 24,
    right: 24,
    width: 80,
    height: 80,
    borderRadius: '50%',
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
    boxShadow: '0 4px 20px rgba(0,0,0,0.35)',
    zIndex: 1000,
    transition: 'transform 0.2s',
  },

  // Panel chat
  panel: {
    position: 'fixed',
    bottom: 86,
    right: 24,
    width: 380,
    maxHeight: 520,
    borderRadius: 'var(--border-radius-xl, 16px)',
    background: 'var(--color-background-primary)',
    border: '0.5px solid var(--color-border-tertiary)',
    boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
    display: 'flex',
    flexDirection: 'column',
    zIndex: 1000,
    overflow: 'hidden',
  },

  // Header
  header: {
    padding: '12px 16px',
    borderBottom: '0.5px solid var(--color-border-tertiary)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: 'var(--color-text-primary)',
  },
  headerTicker: {
    fontSize: 11,
    color: 'var(--color-text-tertiary)',
    marginLeft: 8,
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: 'var(--color-text-tertiary)',
    cursor: 'pointer',
    fontSize: 16,
    padding: 4,
  },

  // Messages
  messagesArea: {
    flex: 1,
    overflowY: 'auto',
    padding: '12px 14px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    minHeight: 200,
    maxHeight: 360,
  },
  msgUser: {
    alignSelf: 'flex-end',
    background: 'var(--color-accent, #6366f1)',
    color: '#fff',
    padding: '8px 12px',
    borderRadius: '14px 14px 4px 14px',
    fontSize: 12,
    lineHeight: 1.5,
    maxWidth: '85%',
    wordBreak: 'break-word',
  },
  msgBot: {
    alignSelf: 'flex-start',
    background: 'var(--color-background-secondary)',
    color: 'var(--color-text-primary)',
    padding: '8px 12px',
    borderRadius: '14px 14px 14px 4px',
    fontSize: 12,
    lineHeight: 1.6,
    maxWidth: '85%',
    wordBreak: 'break-word',
    whiteSpace: 'pre-wrap',
  },
  disclaimer: {
    fontSize: 10,
    color: 'var(--color-text-tertiary)',
    marginTop: 4,
    fontStyle: 'italic',
  },
  typing: {
    alignSelf: 'flex-start',
    fontSize: 12,
    color: 'var(--color-text-tertiary)',
    fontStyle: 'italic',
    padding: '8px 12px',
  },

  // Input
  inputArea: {
    padding: '10px 14px',
    borderTop: '0.5px solid var(--color-border-tertiary)',
    display: 'flex',
    gap: 8,
    alignItems: 'center',
  },
  input: {
    flex: 1,
    background: 'var(--color-background-secondary)',
    border: '0.5px solid var(--color-border-tertiary)',
    borderRadius: 'var(--border-radius-md, 8px)',
    padding: '8px 12px',
    fontSize: 12,
    color: 'var(--color-text-primary)',
    outline: 'none',
    resize: 'none',
    fontFamily: 'inherit',
  },
  sendBtn: {
    background: 'var(--color-accent, #6366f1)',
    color: '#fff',
    border: 'none',
    borderRadius: 'var(--border-radius-md, 8px)',
    padding: '8px 12px',
    fontSize: 12,
    cursor: 'pointer',
    fontWeight: 500,
    whiteSpace: 'nowrap',
  },

  // Suggestions
  suggestions: {
    padding: '8px 14px 4px',
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  suggestion: {
    background: 'var(--color-background-secondary)',
    border: '0.5px solid var(--color-border-tertiary)',
    borderRadius: 20,
    padding: '4px 10px',
    fontSize: 10,
    color: 'var(--color-text-secondary)',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
}

// ---------------------------------------------------------------------------
// Composant
// ---------------------------------------------------------------------------

export default function ChatIA({ ticker }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const messagesEndRef = useRef(null)

  // Auto-scroll en bas
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const handleSend = useCallback(async (text) => {
    const question = (text || input).trim()
    if (!question || loading) return

    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: question }])
    setLoading(true)

    try {
      const data = await chatIA(question, ticker || null)
      setMessages(prev => [...prev, {
        role: 'bot',
        content: data.reponse,
        disclaimer: data.disclaimer,
      }])
    } catch (err) {
      setMessages(prev => [...prev, {
        role: 'bot',
        content: "Erreur lors de la communication avec l'IA. Veuillez reessayer.",
      }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, ticker])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const suggestions = ticker
    ? [
        `Analyse complete de ${ticker}`,
        `Compare ${ticker} aux autres titres`,
        `Alertes et signaux sur ${ticker}`,
      ]
    : [
        'Resume global du dashboard',
        'Quelles alertes meritent attention ?',
        'Etat de mon portefeuille',
      ]

  return (
    <>
      {/* Bouton flottant */}
      <button
        style={STYLES.fab}
        onClick={() => setOpen(o => !o)}
        title="Chat IA"
      >
        <img src="/chatbot.png" alt="Chat IA" style={{ width: 80, height: 80, borderRadius: '50%', objectFit: 'cover' }} />
      </button>

      {/* Panel */}
      {open && (
        <div style={STYLES.panel}>
          {/* Header */}
          <div style={STYLES.header}>
            <div>
              <span style={STYLES.headerTitle}>Chat IA</span>
              {ticker && <span style={STYLES.headerTicker}>{ticker}</span>}
            </div>
            <button style={STYLES.closeBtn} onClick={() => setOpen(false)}>{'\u2715'}</button>
          </div>

          {/* Messages */}
          <div style={STYLES.messagesArea}>
            {messages.length === 0 && !loading && (
              <div style={{ textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 12, padding: '30px 0' }}>
                Posez une question sur vos titres ou votre portefeuille.
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i}>
                <div style={msg.role === 'user' ? STYLES.msgUser : STYLES.msgBot}>
                  {msg.content}
                </div>
                {msg.disclaimer && (
                  <div style={STYLES.disclaimer}>{msg.disclaimer}</div>
                )}
              </div>
            ))}

            {loading && <div style={STYLES.typing}>L'IA reflechit...</div>}
            <div ref={messagesEndRef} />
          </div>

          {/* Suggestions */}
          {messages.length === 0 && (
            <div style={STYLES.suggestions}>
              {suggestions.map((s, i) => (
                <button
                  key={i}
                  style={STYLES.suggestion}
                  onClick={() => handleSend(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div style={STYLES.inputArea}>
            <input
              style={STYLES.input}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={ticker ? `Question sur ${ticker}...` : 'Posez votre question...'}
              disabled={loading}
            />
            <button
              style={{ ...STYLES.sendBtn, opacity: loading || !input.trim() ? 0.5 : 1 }}
              onClick={() => handleSend()}
              disabled={loading || !input.trim()}
            >
              Envoyer
            </button>
          </div>
        </div>
      )}
    </>
  )
}
