export function formatEventDate(value) {
  return new Intl.DateTimeFormat('uk-UA', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

export function readPageFromLocation() {
  if (
    window.location.pathname === '/transactions/sign' ||
    window.location.pathname === '/transactions/sign/'
  ) {
    return { type: 'sign-transaction' }
  }
  if (window.location.pathname === '/tickets' || window.location.pathname === '/tickets/') {
    return { type: 'my-tickets' }
  }
  if (window.location.pathname === '/organizer/events' || window.location.pathname === '/organizer/events/') {
    return { type: 'organizer-events' }
  }
  const ticketQrMatch = window.location.pathname.match(/^\/tickets\/(\d+)\/qr\/?$/)
  if (ticketQrMatch) return { type: 'ticket-qr', ticketId: Number(ticketQrMatch[1]) }
  const match = window.location.pathname.match(/^\/events\/([^/]+)\/?$/)
  if (window.location.pathname === '/events/new' || window.location.pathname === '/events/new/') {
    return { type: 'create' }
  }
  if (match) return { type: 'detail', slug: decodeURIComponent(match[1]) }
  return { type: 'list' }
}
