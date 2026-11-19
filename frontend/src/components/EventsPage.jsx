import { CalendarIcon, ArrowIcon, PlusIcon, TicketIcon } from './Icons.jsx'
import { formatEventDate } from '../utils/events.js'

function EventArtwork({ event, index }) {
  return (
    <div className={`event-artwork event-artwork-${index % 4}`} aria-hidden="true">
      {event.image_uri ? (
        <img className="event-poster-image" src={event.image_uri} alt="" loading="lazy" />
      ) : (
        <>
          <span className="artwork-kicker">NFTICKET / EVENT {String(index + 1).padStart(2, '0')}</span>
          <span className="artwork-orbit" />
          <span className="artwork-symbol">{index % 2 === 0 ? '✳' : '◌'}</span>
          <span className="artwork-title">{event.title.slice(0, 22)}</span>
          <span className="artwork-bottom">ON SOLANA <span>◎</span></span>
        </>
      )}
    </div>
  )
}

export default function EventsPage({
  events,
  loading,
  query,
  isAuthenticated,
  onOpenEvent,
  onCreateEvent,
  onConnect,
}) {
  return (
    <section className="events-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> ВІДКРИВАЙ НОВЕ</div>
          <h1>Події, що <em>об’єднують.</em></h1>
          <p>Знаходь враження, які хочеться пережити наживо.</p>
        </div>
        {isAuthenticated && (
          <button className="button button-primary" type="button" onClick={onCreateEvent}>
            <PlusIcon /> Створити подію
          </button>
        )}
      </div>
      <div className="events-toolbar">
        <span>{query ? `Результати пошуку: «${query}»` : 'Усі доступні події'}</span>
        <span className="result-count">{loading ? '…' : `${events.length} ${events.length === 1 ? 'подія' : 'подій'}`}</span>
      </div>
      {loading ? (
        <div className="state-card"><span className="spinner" /> Завантажуємо події…</div>
      ) : events.length ? (
        <div className="event-grid">
          {events.map((event, index) => (
            <a
              className="event-card"
              href={`/events/${encodeURIComponent(event.slug)}/`}
              key={event.slug}
              onClick={(clickEvent) => { clickEvent.preventDefault(); onOpenEvent(event.slug) }}
            >
              <EventArtwork event={event} index={index} />
              <div className="event-card-body">
                <div className="event-card-date"><CalendarIcon /> {formatEventDate(event.event_date)}</div>
                <h2>{event.title}</h2>
                <span className="event-card-link">ДЕТАЛІ ПОДІЇ <ArrowIcon /></span>
              </div>
            </a>
          ))}
        </div>
      ) : (
        <div className="state-card empty-state">
          <span className="empty-icon"><TicketIcon /></span>
          <h2>{query ? 'Нічого не знайдено' : 'Поки що немає подій'}</h2>
          <p>{query ? 'Спробуй змінити пошуковий запит.' : 'Тут з’являться події, доступні для перегляду та придбання квитків.'}</p>
          {!query && !isAuthenticated && <button className="button button-outline" onClick={onConnect}>Підключити гаманець</button>}
          {!query && isAuthenticated && <button className="button button-primary" onClick={onCreateEvent}>Створити першу подію</button>}
        </div>
      )}
    </section>
  )
}
