import { useEffect, useState } from 'react'
import { ArrowBackIcon, TicketIcon, WalletIcon } from './Icons.jsx'
import { apiRequest } from '../utils/api.js'

function getRemainingSeconds(expiresAt) {
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 1000))
}

export default function TicketQrPage({ ticketId, session, onNavigate, onConnect }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [qrState, setQrState] = useState({ key: -1, qr: null, image: '', error: '' })
  const [secondsRemaining, setSecondsRemaining] = useState(0)
  const loading = Boolean(session) && qrState.key !== refreshKey
  const qr = qrState.key === refreshKey ? qrState.qr : null
  const qrImage = qrState.key === refreshKey ? qrState.image : ''
  const error = qrState.key === refreshKey ? qrState.error : ''

  useEffect(() => {
    if (!session) return undefined

    let active = true
    apiRequest(`/tickets/${ticketId}/qr/`, { token: session.token })
      .then(async (response) => {
        const { default: QRCode } = await import('qrcode')
        const image = await QRCode.toDataURL(response.qr_token, {
          errorCorrectionLevel: 'M',
          margin: 2,
          width: 320,
          color: { dark: '#11162a', light: '#ffffff' },
        })
        if (!active) return
        setQrState({ key: refreshKey, qr: response, image, error: '' })
        setSecondsRemaining(getRemainingSeconds(response.expires_at))
      })
      .catch((requestError) => {
        if (active) {
          setQrState({ key: refreshKey, qr: null, image: '', error: requestError.message })
        }
      })

    return () => {
      active = false
    }
  }, [ticketId, session, refreshKey])

  useEffect(() => {
    if (!qr?.expires_at) return undefined
    const timerId = window.setInterval(() => {
      setSecondsRemaining(getRemainingSeconds(qr.expires_at))
    }, 1000)
    return () => window.clearInterval(timerId)
  }, [qr])

  return (
    <section className="ticket-qr-page">
      <button className="text-button back-button" onClick={() => onNavigate('/tickets/')}>
        <ArrowBackIcon /> Мої квитки
      </button>
      {!session ? (
        <div className="state-card auth-prompt">
          <span className="empty-icon"><WalletIcon /></span>
          <h2>Підключи гаманець</h2>
          <p>QR-код доступний лише власнику квитка після входу через Phantom.</p>
          <button className="button button-primary" onClick={onConnect}><WalletIcon /> Підключити гаманець</button>
        </div>
      ) : (
        <div className="ticket-qr-card">
          <div className="eyebrow"><span className="eyebrow-line" /> ВХІД НА ПОДІЮ</div>
          <h1>Твій квиток<br /><em>на вході.</em></h1>
          <p className="ticket-qr-description">Покажи цей QR-код працівнику на вході. Код короткочасний і призначений для одноразового сканування.</p>
          <div className={`qr-display ${secondsRemaining === 0 && qr ? 'qr-expired' : ''}`}>
            {loading ? (
              <div className="qr-loading"><span className="spinner" /> Створюємо захищений QR…</div>
            ) : error ? (
              <div className="qr-error" role="alert">
                <TicketIcon />
                <p>{error}</p>
                <button className="button button-outline" onClick={() => setRefreshKey((key) => key + 1)}>Спробувати ще</button>
              </div>
            ) : secondsRemaining > 0 ? (
              <img src={qrImage} alt="Одноразовий QR-код квитка" />
            ) : (
              <div className="qr-expired-message">
                <TicketIcon />
                <strong>Термін QR-коду завершився</strong>
                <span>Створи новий код перед скануванням.</span>
              </div>
            )}
          </div>
          {!loading && !error && (
            <div className="qr-expiry">
              <span className={`expiry-indicator ${secondsRemaining <= 10 ? 'expiry-warning' : ''}`} />
              {secondsRemaining > 0
                ? `Оновиться через ${secondsRemaining} с`
                : 'Цей QR-код більше не дійсний'}
            </div>
          )}
          {!loading && (
            <button
              className="button button-primary qr-refresh-button"
              onClick={() => setRefreshKey((key) => key + 1)}
              disabled={loading}
            >
              Створити новий QR-код
            </button>
          )}
          <p className="qr-safety-note">Не надсилай і не зберігай зображення QR-коду: будь-хто, хто його відсканує, може використати твій квиток.</p>
        </div>
      )}
    </section>
  )
}
