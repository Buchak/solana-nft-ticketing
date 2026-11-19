import { useEffect, useState } from 'react'
import { ArrowBackIcon, ArrowIcon, CalendarIcon, TicketIcon, WalletIcon } from './Icons.jsx'
import { formatEventDate } from '../utils/events.js'
import { apiRequest } from '../utils/api.js'

const TICKET_STATUS_LABELS = {
  active: 'Дійсний',
  used: 'Використаний',
  pending: 'Очікує підтвердження',
  refunded: 'Повернений',
  burned: 'Погашений',
  failed: 'Не оформлено',
}

export default function MyTicketsPage({
  session,
  refreshKey,
  busyAction,
  onNavigate,
  onConnect,
  onRefundTicket,
  onCheckTicketRefund,
}) {
  const [ticketData, setTicketData] = useState({ token: null, tickets: [], error: '' })
  const loading = Boolean(session) && ticketData.token !== session.token
  const error = session && ticketData.token === session.token ? ticketData.error : ''
  const tickets = session && ticketData.token === session.token ? ticketData.tickets : []

  useEffect(() => {
    if (!session) return undefined

    let active = true
    apiRequest('/tickets/mine/', { token: session.token })
      .then((response) => {
        if (active) {
          setTicketData({
            token: session.token,
            tickets: Array.isArray(response) ? response : response.results || [],
            error: '',
          })
        }
      })
      .catch((requestError) => {
        if (active) setTicketData({ token: session.token, tickets: [], error: requestError.message })
      })

    return () => {
      active = false
    }
  }, [session, refreshKey])

  return (
    <section className="my-tickets-page">
      <button className="text-button back-button" onClick={() => onNavigate('/')}><ArrowBackIcon /> Усі події</button>
      <div className="page-heading">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> ТВОЇ КВИТКИ</div>
          <h1>Твій доступ<br /><em>до вражень.</em></h1>
          <p>Тут зібрані придбані квитки та їхній поточний статус.</p>
        </div>
      </div>
      {!session ? (
        <div className="state-card auth-prompt">
          <span className="empty-icon"><WalletIcon /></span>
          <h2>Підключи гаманець</h2>
          <p>Увійди через гаманець, у якому зберігаються твої квитки.</p>
          <button className="button button-primary" onClick={onConnect}><WalletIcon /> Підключити гаманець</button>
        </div>
      ) : error ? (
        <div className="state-card ticket-list-error" role="alert">{error}</div>
      ) : loading ? (
        <div className="state-card"><span className="spinner" /> Завантажуємо квитки…</div>
      ) : tickets.length ? (
        <div className="owned-ticket-list">
          {tickets.map((ticket) => (
            <article className="owned-ticket-card" key={ticket.id}>
              <div className="owned-ticket-art">
                {ticket.event_image_uri && <img src={ticket.event_image_uri} alt="" loading="lazy" />}
                {!ticket.event_image_uri && <TicketIcon />}
              </div>
              <div className="owned-ticket-info">
                <span className="ticket-status" data-status={ticket.status}>
                  {TICKET_STATUS_LABELS[ticket.status] || ticket.status}
                </span>
                {ticket.event_is_canceled && (
                  <span className="ticket-event-canceled">Подію скасовано</span>
                )}
                <h2>{ticket.event_title}</h2>
                <p>{ticket.ticket_type_name} · {ticket.name}</p>
                <span className="owned-ticket-date"><CalendarIcon /> {formatEventDate(ticket.event_date)}</span>
              </div>
              <div className="owned-ticket-actions">
                {ticket.status === 'active' && ticket.event_is_canceled ? (
                  ticket.refund_status === 'submitted' ? (
                    <button
                      className="button button-outline owned-ticket-action"
                      disabled={busyAction === `check-refund-${ticket.id}`}
                      onClick={() => onCheckTicketRefund(ticket)}
                    >
                      {busyAction === `check-refund-${ticket.id}` ? 'Перевіряємо…' : 'Перевірити повернення'}
                    </button>
                  ) : !ticket.event_is_frozen ? (
                    <button
                      className="button button-primary owned-ticket-action"
                      disabled={Boolean(busyAction)}
                      onClick={() => onRefundTicket(ticket)}
                    >
                      {busyAction === `refund-${ticket.id}` ? 'Обробка…' : 'Повернути кошти'}
                    </button>
                  ) : (
                    <span className="owned-ticket-status">Повернення призупинено</span>
                  )
                ) : ticket.status === 'active' ? (
                  <button
                    className="button button-primary owned-ticket-action"
                    onClick={() => onNavigate(`/tickets/${ticket.id}/qr/`)}
                  >
                    Показати QR <ArrowIcon />
                  </button>
                ) : (
                  <span className="owned-ticket-status">{TICKET_STATUS_LABELS[ticket.status] || ticket.status}</span>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="state-card empty-state">
          <span className="empty-icon"><TicketIcon /></span>
          <h2>Поки немає квитків</h2>
          <p>Після підтвердження покупки квитки з’являться тут.</p>
          <button className="button button-outline" onClick={() => onNavigate('/')}>Знайти подію</button>
        </div>
      )}
    </section>
  )
}
