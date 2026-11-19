import { useEffect, useState } from 'react'
import { ArrowBackIcon, CalendarIcon, PlusIcon, TicketIcon } from './Icons.jsx'
import { formatEventDate } from '../utils/events.js'
import { formatSol } from '../utils/solana.js'
import { apiRequest } from '../utils/api.js'

export default function EventDetailsPage({
  event,
  ticketTypes,
  loading,
  session,
  busyAction,
  ticketRefreshKey,
  balanceRefreshKey,
  onBuy,
  onRefundTicket,
  onCheckTicketRefund,
  onReportEvent,
  onBack,
  onCreateTicketType,
}) {
  const [currentTime, setCurrentTime] = useState(() => Date.now())
  const [balanceReloadKey, setBalanceReloadKey] = useState(0)
  const [ownedTickets, setOwnedTickets] = useState({ token: null, slug: null, tickets: [], error: '' })
  const [eventBalance, setEventBalance] = useState({
    token: null,
    slug: null,
    data: null,
    loading: false,
    error: '',
  })
  const ticketsLoading = Boolean(session) &&
    (ownedTickets.token !== session.token || ownedTickets.slug !== event?.slug)
  const ticketsMatchCurrentEvent =
    session && ownedTickets.token === session.token && ownedTickets.slug === event?.slug
  const ticketsError = ticketsMatchCurrentEvent ? ownedTickets.error : ''
  const userTickets = ticketsMatchCurrentEvent ? ownedTickets.tickets : []

  useEffect(() => {
    const updateTime = () => setCurrentTime(Date.now())
    updateTime()
    const intervalId = window.setInterval(updateTime, 60_000)
    return () => window.clearInterval(intervalId)
  }, [event?.slug])

  useEffect(() => {
    if (!session || !event?.slug) return undefined

    let active = true
    apiRequest(`/tickets/mine/?event_slug=${encodeURIComponent(event.slug)}`, {
      token: session.token,
    })
      .then((response) => {
        if (active) {
          setOwnedTickets({
            token: session.token,
            slug: event.slug,
            tickets: Array.isArray(response) ? response : response.results || [],
            error: '',
          })
        }
      })
      .catch((requestError) => {
        if (active) {
          setOwnedTickets({
            token: session.token,
            slug: event.slug,
            tickets: [],
            error: requestError.message,
          })
        }
      })

    return () => {
      active = false
    }
  }, [session, event?.slug, ticketRefreshKey])

  useEffect(() => {
    if (
      !session ||
      !event?.slug ||
      session.walletAddress !== event.organizer_wallet
    ) {
      return undefined
    }

    let active = true
    const fetchBalance = async () => {
      setEventBalance((current) => ({
        token: session.token,
        slug: event.slug,
        data: current.token === session.token && current.slug === event.slug
          ? current.data
          : null,
        loading: true,
        error: '',
      }))
      try {
        const balance = await apiRequest(
          `/events/${encodeURIComponent(event.slug)}/balance/`,
          { token: session.token },
        )
        if (active) {
          setEventBalance({
            token: session.token,
            slug: event.slug,
            data: balance,
            loading: false,
            error: '',
          })
        }
      } catch (requestError) {
        if (active) {
          setEventBalance((current) => ({
            token: session.token,
            slug: event.slug,
            data: current.token === session.token && current.slug === event.slug
              ? current.data
              : null,
            loading: false,
            error: requestError.message,
          }))
        }
      }
    }

    fetchBalance()
    const intervalId = window.setInterval(fetchBalance, 30_000)
    return () => {
      active = false
      window.clearInterval(intervalId)
    }
  }, [session, event?.slug, event?.organizer_wallet, balanceRefreshKey, balanceReloadKey])

  if (loading) {
    return <div className="state-card"><span className="spinner" /> Завантажуємо подію…</div>
  }
  if (!event) {
    return (
      <div className="state-card empty-state">
        <h1>Подію не знайдено</h1>
        <p>Вона може бути недоступна або посилання неправильне.</p>
        <button className="button button-primary" onClick={onBack}>До всіх подій</button>
      </div>
    )
  }

  const isOrganizer = session?.walletAddress === event.organizer_wallet
  const eventHasPassed = currentTime >= new Date(event.event_date).getTime()
  const isAvailable =
    event.is_active &&
    !event.is_canceled &&
    !event.is_completed &&
    !event.is_frozen &&
    !eventHasPassed
  const hoursUntilEvent =
    (new Date(event.event_date).getTime() - currentTime) / (60 * 60 * 1000)
  const selfRefundWindowOpen = event.is_canceled || (currentTime > 0 && hoursUntilEvent > 48)
  const eventHasStarted = currentTime >= new Date(event.event_date).getTime()
  const complaintTickets = userTickets.filter(
    (ticket) =>
      ticket.status === 'active' &&
      ticket.owner_wallet === session?.walletAddress &&
      !ticket.has_event_dispute,
  )
  const balanceMatchesEvent =
    eventBalance.token === session?.token && eventBalance.slug === event.slug

  function formatLamports(lamports) {
    const value = BigInt(lamports || '0')
    const whole = (value / 1_000_000_000n).toString()
    const fraction = (value % 1_000_000_000n)
      .toString()
      .padStart(9, '0')
      .replace(/0+$/, '')
    return `${Number(whole).toLocaleString('uk-UA')}${fraction ? `,${fraction}` : ''} SOL`
  }

  return (
    <article className="event-details-page">
      <button className="text-button back-button" onClick={onBack}><ArrowBackIcon /> Усі події</button>
      <div className="detail-hero">
        <div className="detail-artwork">
          {event.image_uri ? (
            <img className="detail-poster-image" src={event.image_uri} alt={`Афіша події «${event.title}»`} />
          ) : (
            <>
              <div className="detail-artwork-orbit" />
              <div className="detail-artwork-symbol">✳</div>
              <span>NFTICKET / SOLANA EVENT</span>
            </>
          )}
        </div>
        <div className="detail-intro">
          <div className="eyebrow"><span className="eyebrow-line" /> {isAvailable ? 'ПОДІЯ ДОСТУПНА' : 'СТАТУС ПОДІЇ'}</div>
          <h1>{event.title}</h1>
          <p className="detail-date"><CalendarIcon /> {formatEventDate(event.event_date)}</p>
          {event.description && <p className="detail-description">{event.description}</p>}
          <div className="event-stats">
            <div><strong>{event.total_tickets_sold || 0}</strong><span>квитків продано</span></div>
            <div><strong>{event.active_tickets || 0}</strong><span>активних квитків</span></div>
          </div>
          {isOrganizer && event.finalization_scheduled_for && (
            <p className="finalization-schedule">
              Автоматичне завершення заплановане на{' '}
              <strong>{formatEventDate(event.finalization_scheduled_for)}</strong>
            </p>
          )}
          {!isAvailable && (
            <div className="status-note">
              {event.is_canceled ? 'Подію скасовано.' : event.is_completed || eventHasPassed ? 'Подія вже пройшла.' : event.is_frozen ? 'Продаж для цієї події призупинено.' : 'Подія ще очікує підтвердження в Solana.'}
            </div>
          )}
        </div>
      </div>
      {isOrganizer && (
        <section className="event-balance-card" aria-label="Баланс акаунта події">
          <div className="event-balance-heading">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> ON-CHAIN БАЛАНС</div>
              <h2>Кошти акаунта події</h2>
            </div>
            <button
              className="button button-outline event-balance-refresh"
              type="button"
              disabled={balanceMatchesEvent && eventBalance.loading}
              onClick={() => setBalanceReloadKey((current) => current + 1)}
            >
              Оновити
            </button>
          </div>
          {!balanceMatchesEvent || eventBalance.loading ? (
            <p className="event-balance-feedback"><span className="spinner" /> Завантажуємо актуальний баланс Solana…</p>
          ) : eventBalance.error ? (
            <p className="event-balance-feedback event-balance-error" role="alert">
              Не вдалося отримати баланс: {eventBalance.error}
            </p>
          ) : eventBalance.data ? (
            <>
              <div className="event-balance-values">
                <div>
                  <span>Баланс акаунта</span>
                  <strong>{formatLamports(eventBalance.data.balance_lamports)}</strong>
                </div>
                <div>
                  <span>Доступно для виведення</span>
                  <strong>{formatLamports(eventBalance.data.withdrawable_lamports)}</strong>
                </div>
              </div>
              <p className="event-balance-note">
                Rent-exempt резерв: {formatLamports(eventBalance.data.rent_exempt_lamports)}. Баланс оновлюється автоматично.
              </p>
            </>
          ) : (
            <p className="event-balance-feedback">Баланс акаунта поки недоступний.</p>
          )}
        </section>
      )}

      <section className="ticket-section">
        <div className="section-title-row">
          <div><div className="eyebrow"><span className="eyebrow-line" /> КВИТКИ</div><h2>Обери свій <em>формат.</em></h2></div>
          <span className="section-count">{ticketTypes.length} {ticketTypes.length === 1 ? 'тип' : 'типів'}</span>
        </div>
        {ticketTypes.length ? (
          <div className="ticket-list">
            {ticketTypes.map((ticketType) => {
              const remaining = Math.max(0, ticketType.max_supply - ticketType.minted)
              const soldOut = remaining === 0
              return (
                <div className="ticket-row" key={ticketType.id}>
                  <div className="ticket-row-mark">
                    {ticketType.image_uri
                      ? <img src={ticketType.image_uri} alt={`Зображення NFT-квитка ${ticketType.name}`} />
                      : <TicketIcon />}
                  </div>
                  <div className="ticket-row-info">
                    <h3>{ticketType.name}</h3>
                    <p>{ticketType.description || `${remaining} квитків залишилося`}</p>
                    <span>{ticketType.minted} придбано · {remaining} залишилося з {ticketType.max_supply}</span>
                  </div>
                  <strong className="ticket-price">{formatSol(ticketType.price_in_lamports)}</strong>
                  <button
                    className="button button-primary buy-button"
                    onClick={() => onBuy(ticketType)}
                    disabled={!isAvailable || soldOut || busyAction === `buy-${ticketType.id}`}
                  >
                    {busyAction === `buy-${ticketType.id}` ? <><span className="spinner" /> Обробка…</> : eventHasPassed || event.is_completed ? 'Подія пройшла' : soldOut ? 'Розпродано' : session ? 'Придбати' : 'Підключити гаманець'}
                  </button>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="state-card ticket-empty">
            <p>Організатор ще не додав типи квитків до цієї події.</p>
          </div>
        )}
        {isOrganizer && isAvailable && (
          <button className="button button-outline add-ticket-button" onClick={onCreateTicketType}>
            <PlusIcon /> Додати тип квитка
          </button>
        )}
      </section>
      {session && (
        <section className="ticket-section owned-event-tickets">
          <div className="section-title-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> ТВОЇ КВИТКИ</div>
              <h2>Квитки на цю <em>подію.</em></h2>
            </div>
            <span className="section-count">{userTickets.length}</span>
          </div>
          {ticketsError ? (
            <div className="state-card ticket-list-error" role="alert">{ticketsError}</div>
          ) : ticketsLoading ? (
            <div className="state-card"><span className="spinner" /> Завантажуємо твої квитки…</div>
          ) : userTickets.length ? (
            <div className="owned-ticket-list">
              {userTickets.map((ticket) => {
                const refundPending = ticket.refund_status === 'submitted'
                const canRefund =
                  ticket.status === 'active' &&
                  session.walletAddress === ticket.owner_wallet &&
                  !event.is_frozen &&
                  selfRefundWindowOpen
                const refundDisabled =
                  Boolean(busyAction) ||
                  (refundPending
                    ? busyAction === `check-refund-${ticket.id}`
                    : !canRefund)

                return (
                  <article className="owned-ticket-card" key={ticket.id}>
                    <div className="owned-ticket-art">
                      {event.image_uri && <img src={event.image_uri} alt="" loading="lazy" />}
                      {!event.image_uri && <TicketIcon />}
                    </div>
                    <div className="owned-ticket-info">
                      <span className="ticket-status" data-status={ticket.status}>
                        {ticket.status === 'active' ? 'Дійсний' : ticket.status}
                      </span>
                      <h2>{ticket.ticket_type_name} · {ticket.name}</h2>
                      <p>{formatSol(ticket.price_paid_lamports)} сплачено</p>
                      {!event.is_canceled && !selfRefundWindowOpen && (
                        <span className="ticket-refund-note">
                          Повернення доступне лише раніше ніж за 48 годин до початку.
                        </span>
                      )}
                      {event.is_frozen && (
                        <span className="ticket-refund-note">
                          Повернення тимчасово недоступне, поки подія призупинена.
                        </span>
                      )}
                      {session.walletAddress !== ticket.owner_wallet && ticket.status === 'active' && (
                        <span className="ticket-refund-note">
                          Підключи гаманець, на якому зберігається квиток, щоб повернути його.
                        </span>
                      )}
                    </div>
                    <button
                      className="button button-outline owned-ticket-action"
                      type="button"
                      disabled={refundDisabled}
                      onClick={() => refundPending
                        ? onCheckTicketRefund(ticket)
                        : onRefundTicket(ticket)}
                    >
                      {refundPending
                        ? busyAction === `check-refund-${ticket.id}` ? 'Перевіряємо…' : 'Перевірити повернення'
                        : busyAction === `refund-${ticket.id}` ? 'Обробка…'
                          : ticket.status === 'refunded' ? 'Квиток повернено'
                            : ticket.status === 'active' ? 'Повернути квиток' : 'Повернення недоступне'}
                    </button>
                  </article>
                )
              })}
            </div>
          ) : (
            <div className="state-card ticket-empty">
              <p>У тебе поки немає квитків на цю подію.</p>
            </div>
          )}
        </section>
      )}
      {session &&
        !isOrganizer &&
        eventHasStarted &&
        userTickets.some((ticket) => ticket.owner_wallet === session.walletAddress) && (
          <section className="event-dispute-section" aria-labelledby="event-dispute-title">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> ЗВОРОТНИЙ ЗВ’ЯЗОК</div>
              <h2 id="event-dispute-title">Подія не відбулась?</h2>
              <p>
                Поскаржитися може власник придбаного квитка, який ще не сканували.
                Для демонстрації кожна така скарга одразу враховується під час фіналізації.
              </p>
            </div>
            {complaintTickets.length > 0 ? (
              <button
                className="button button-outline"
                type="button"
                disabled={Boolean(busyAction) || ticketsLoading}
                onClick={() => onReportEvent(complaintTickets[0])}
              >
                {busyAction === 'report-event' ? 'Надсилаємо скаргу…' : 'Поскаржитись'}
              </button>
            ) : (
              <p className="event-dispute-feedback" role="status">
                {ticketsLoading
                  ? 'Перевіряємо квитки…'
                  : userTickets.some(
                      (ticket) =>
                        ticket.owner_wallet === session.walletAddress &&
                        ticket.has_event_dispute,
                    )
                    ? 'Скаргу за цим квитком уже подано.'
                    : 'Поскаржитися можна лише з невикористаним квитком.'}
              </p>
            )}
          </section>
        )}
    </article>
  )
}
