import { useEffect, useState } from 'react'
import { ArrowIcon, CalendarIcon, PlusIcon, TicketIcon, WalletIcon } from './Icons.jsx'
import { formatEventDate } from '../utils/events.js'
import { apiRequest } from '../utils/api.js'

function getEventStatus(event) {
  if (event.is_canceled) return 'Скасована'
  if (event.is_completed) return 'Завершена'
  if (event.is_frozen) return 'Призупинена'
  if (!event.is_active) return 'Очікує підтвердження в Solana'
  return 'Активна'
}

export default function OrganizerEventsPage({
  session,
  refreshKey,
  busyAction,
  onNavigate,
  onConnect,
  onWithdraw,
  onCheckWithdrawal,
  onCancel,
  onCheckCancellation,
}) {
  const [eventData, setEventData] = useState({ token: null, events: [], error: '' })
  const loading = Boolean(session) && eventData.token !== session.token
  const error = session && eventData.token === session.token ? eventData.error : ''
  const events = session && eventData.token === session.token ? eventData.events : []

  useEffect(() => {
    if (!session) return undefined

    let active = true
    apiRequest('/events/mine/', { token: session.token })
      .then((response) => {
        if (active) {
          setEventData({
            token: session.token,
            events: Array.isArray(response) ? response : response.results || [],
            error: '',
          })
        }
      })
      .catch((requestError) => {
        if (active) setEventData({ token: session.token, events: [], error: requestError.message })
      })

    return () => {
      active = false
    }
  }, [session, refreshKey])

  return (
    <section className="organizer-events-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> КАБІНЕТ ОРГАНІЗАТОРА</div>
          <h1>Твої події<br /><em>на одній сцені.</em></h1>
          <p>Переглядай статус, дату та відвідуваність створених подій.</p>
        </div>
        {session && (
          <button className="button button-primary" type="button" onClick={() => onNavigate('/events/new/')}>
            <PlusIcon /> Створити подію
          </button>
        )}
      </div>
      {!session ? (
        <div className="state-card auth-prompt">
          <span className="empty-icon"><WalletIcon /></span>
          <h2>Підключи гаманець</h2>
          <p>Увійди через гаманець, з якого створювалися події.</p>
          <button className="button button-primary" onClick={onConnect}><WalletIcon /> Підключити гаманець</button>
        </div>
      ) : error ? (
        <div className="state-card organizer-events-error" role="alert">{error}</div>
      ) : loading ? (
        <div className="state-card"><span className="spinner" /> Завантажуємо твої події…</div>
      ) : events.length ? (
        <>
          <div className="events-toolbar">
            <span>Усі події, створені цим акаунтом</span>
            <span className="result-count">{events.length} {events.length === 1 ? 'подія' : 'подій'}</span>
          </div>
          <div className="organizer-event-list">
            {events.map((event, index) => (
              <article className="organizer-event-card" key={event.slug}>
                <a
                  className={`organizer-event-art event-artwork-${index % 4}`}
                  href={`/events/${encodeURIComponent(event.slug)}/`}
                  onClick={(clickEvent) => {
                    clickEvent.preventDefault()
                    onNavigate(`/events/${encodeURIComponent(event.slug)}/`)
                  }}
                  aria-label={`Відкрити подію «${event.title}»`}
                >
                  {event.image_uri ? <img src={event.image_uri} alt="" loading="lazy" /> : <TicketIcon />}
                </a>
                <div className="organizer-event-info">
                  <span className={`organizer-event-status ${event.is_active ? 'status-active' : ''}`}>
                    <span /> {getEventStatus(event)}
                  </span>
                  <h2>{event.title}</h2>
                  <p className="organizer-event-date"><CalendarIcon /> {formatEventDate(event.event_date)}</p>
                  <div className="organizer-event-stats">
                    <span><strong>{event.total_tickets_sold}</strong> продано</span>
                    <span><strong>{event.total_ticket_scanned}</strong> відскановано</span>
                    <span><strong>{event.active_tickets}</strong> активних</span>
                  </div>
                </div>
                <div className="organizer-event-actions">
                  <button
                    className="button button-outline organizer-event-action"
                    type="button"
                    onClick={() => onNavigate(`/events/${encodeURIComponent(event.slug)}/`)}
                  >
                    Переглянути <ArrowIcon />
                  </button>
                  {event.is_active && !event.is_frozen && !event.is_completed && !event.is_canceled && (
                    event.cancellation_status === 'submitted' ? (
                      <button
                        className="button button-outline organizer-event-action"
                        type="button"
                        disabled={Boolean(busyAction)}
                        onClick={() => onCheckCancellation(event)}
                      >
                        {busyAction === `check-cancellation-${event.id}` ? 'Перевіряємо…' : 'Перевірити скасування'}
                      </button>
                    ) : (
                      <button
                        className="button button-outline organizer-event-action organizer-cancel-action"
                        type="button"
                        disabled={Boolean(busyAction)}
                        onClick={() => onCancel(event)}
                      >
                        {busyAction === `cancel-${event.id}` ? 'Скасовуємо…' : 'Скасувати подію'}
                      </button>
                    )
                  )}
                  {event.is_completed && !event.is_canceled && !event.is_frozen && (
                    event.withdrawal_status === 'confirmed' ? (
                      <span className="organizer-withdrawal-status" role="status">Кошти виведено</span>
                    ) : event.withdrawal_status === 'submitted' ? (
                      <button
                        className="button button-outline organizer-event-action"
                        type="button"
                        disabled={Boolean(busyAction)}
                        onClick={() => onCheckWithdrawal(event)}
                      >
                        {busyAction === `check-withdrawal-${event.id}` ? 'Перевіряємо…' : 'Перевірити виведення'}
                      </button>
                    ) : (
                      <button
                        className="button button-primary organizer-event-action"
                        type="button"
                        disabled={Boolean(busyAction)}
                        onClick={() => onWithdraw(event)}
                      >
                        {busyAction === `withdraw-${event.id}` ? 'Обробка…' : 'Вивести кошти'}
                      </button>
                    )
                  )}
                </div>
              </article>
            ))}
          </div>
        </>
      ) : (
        <div className="state-card empty-state">
          <span className="empty-icon"><TicketIcon /></span>
          <h2>Ти ще не створив подій</h2>
          <p>Створи першу подію — вона з’явиться тут разом зі своїм статусом.</p>
          <button className="button button-primary" onClick={() => onNavigate('/events/new/')}>
            <PlusIcon /> Створити подію
          </button>
        </div>
      )}
    </section>
  )
}
