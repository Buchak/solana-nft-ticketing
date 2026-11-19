import { SOLANA_CLUSTER } from '../utils/solana.js'

export default function Feedback({ error, notice, onDismissError, onDismissNotice }) {
  return (
    <>
      {error && (
        <div className="feedback feedback-error" role="alert">
          <span className="feedback-icon">!</span><span>{error}</span>
          <button aria-label="Закрити повідомлення" onClick={onDismissError}>×</button>
        </div>
      )}
      {notice && (
        <div className={`feedback feedback-${notice.type}`} role="status">
          <span className="feedback-icon">{notice.type === 'success' ? '✓' : '…'}</span>
          <span>{notice.text}</span>
          {notice.signature && SOLANA_CLUSTER !== 'localnet' && (
            <a
              href={`https://explorer.solana.com/tx/${notice.signature}${SOLANA_CLUSTER === 'mainnet-beta' ? '' : `?cluster=${encodeURIComponent(SOLANA_CLUSTER)}`}`}
              target="_blank"
              rel="noreferrer"
              className="feedback-link"
            >
              Переглянути ↗
            </a>
          )}
          <button aria-label="Закрити повідомлення" onClick={onDismissNotice}>×</button>
        </div>
      )}
    </>
  )
}
