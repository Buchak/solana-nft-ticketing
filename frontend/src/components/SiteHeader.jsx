import {
  MoonIcon,
  SearchIcon,
  SunIcon,
  WalletIcon,
} from './Icons.jsx'
import { shortAddress } from '../utils/solana.js'

export default function SiteHeader({
  pageType,
  session,
  theme,
  searchOpen,
  searchQuery,
  busyAction,
  profileOpen,
  onNavigate,
  onSearchChange,
  onSearchSubmit,
  onSearchToggle,
  onThemeToggle,
  onConnect,
  onProfileToggle,
  onLogout,
}) {
  return (
    <header className="topbar">
      <a className="brand" href="/" onClick={(event) => { event.preventDefault(); onNavigate('/') }} aria-label="NFTicket — головна">
        <span className="brand-mark">N</span><span>NFTICKET</span>
      </a>
      <nav className="main-nav" aria-label="Головна навігація">
        <a className={pageType === 'list' ? 'nav-active' : ''} href="/" onClick={(event) => { event.preventDefault(); onNavigate('/') }}>Події</a>
        {session && (
          <a className={pageType === 'organizer-events' ? 'nav-active' : ''} href="/organizer/events/" onClick={(event) => { event.preventDefault(); onNavigate('/organizer/events/') }}>
            Мої події
          </a>
        )}
        {session && (
          <a className={pageType === 'my-tickets' || pageType === 'ticket-qr' ? 'nav-active' : ''} href="/tickets/" onClick={(event) => { event.preventDefault(); onNavigate('/tickets/') }}>
            Мої квитки
          </a>
        )}
        {session && (
          <a className={pageType === 'create' ? 'nav-active' : ''} href="/events/new/" onClick={(event) => { event.preventDefault(); onNavigate('/events/new/') }}>
            Створити подію
          </a>
        )}
        {session && (
          <a className={pageType === 'sign-transaction' ? 'nav-active' : ''} href="/transactions/sign/" onClick={(event) => { event.preventDefault(); onNavigate('/transactions/sign/') }}>
            Підписати транзакцію
          </a>
        )}
      </nav>
      <div className={`header-search ${searchOpen ? 'header-search-open' : ''}`}>
        {searchOpen && (
          <form onSubmit={onSearchSubmit}>
            <input
              aria-label="Пошук подій"
              autoFocus
              placeholder="Назва або опис події"
              value={searchQuery}
              onChange={(event) => onSearchChange(event.target.value)}
            />
          </form>
        )}
        <button className="icon-button" type="button" onClick={onSearchToggle} aria-label={searchOpen ? 'Закрити пошук' : 'Пошук подій'}>
          <SearchIcon />
        </button>
      </div>
      <div className="account-actions">
        <button
          className="icon-button theme-toggle"
          type="button"
          onClick={onThemeToggle}
          aria-label={`Увімкнути ${theme === 'dark' ? 'світлу' : 'темну'} тему`}
          title={`Увімкнути ${theme === 'dark' ? 'світлу' : 'темну'} тему`}
        >
          {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
        </button>
        {session ? (
          <div className="profile-menu">
            <button
              className="button button-outline profile-button"
              type="button"
              onClick={onProfileToggle}
              aria-expanded={profileOpen}
              aria-haspopup="menu"
            >
              <span className="profile-avatar">◎</span> Профіль
            </button>
            {profileOpen && (
              <div className="profile-dropdown" role="menu">
                <span className="dropdown-label">ПІДКЛЮЧЕНИЙ ГАМАНЕЦЬ</span>
                <strong>{shortAddress(session.walletAddress)}</strong>
                <button type="button" role="menuitem" onClick={() => onNavigate('/organizer/events/')}>
                  Мої події
                </button>
                <button type="button" role="menuitem" onClick={() => onNavigate('/tickets/')}>
                  Мої квитки
                </button>
                <button type="button" role="menuitem" onClick={onLogout} disabled={busyAction === 'logout'}>
                  {busyAction === 'logout' ? 'Вихід…' : 'Вийти з акаунта'}
                </button>
              </div>
            )}
          </div>
        ) : (
          <button className="button button-primary connect-button" onClick={onConnect} disabled={busyAction === 'login'}>
            {busyAction === 'login' ? <span className="spinner" /> : <WalletIcon />}
            {busyAction === 'login' ? 'Очікуємо Phantom' : 'Підключити гаманець'}
          </button>
        )}
      </div>
    </header>
  )
}
