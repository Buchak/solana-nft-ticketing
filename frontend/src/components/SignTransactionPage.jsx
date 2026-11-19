import { useState } from 'react'
import { ArrowBackIcon, WalletIcon } from './Icons.jsx'

export default function SignTransactionPage({
  busy,
  isAuthenticated,
  onSubmit,
  onConnect,
  onCancel,
}) {
  const [transactionId, setTransactionId] = useState('')
  const [unsignedTransaction, setUnsignedTransaction] = useState('')
  const [formError, setFormError] = useState('')

  function handleSubmit(event) {
    event.preventDefault()
    const normalizedId = transactionId.trim()
    const normalizedTransaction = unsignedTransaction.trim()

    if (!/^[1-9]\d*$/.test(normalizedId) || !Number.isSafeInteger(Number(normalizedId))) {
      setFormError('Введи коректний blockchain_transaction.id.')
      return
    }
    if (!normalizedTransaction) {
      setFormError('Встав unsigned_transaction у форматі Base64.')
      return
    }

    setFormError('')
    onSubmit(Number(normalizedId), normalizedTransaction)
  }

  return (
    <section className="sign-transaction-page">
      <button className="text-button back-button" type="button" onClick={onCancel}>
        <ArrowBackIcon /> Усі події
      </button>
      <div className="create-page-heading">
        <div className="eyebrow"><span className="eyebrow-line" /> ДЛЯ АДМІНІСТРАТОРА</div>
        <h1>Підпиши транзакцію<br /><em>через Phantom.</em></h1>
        <p>Встав ID і Base64-транзакцію з відповіді API. Phantom попросить підтвердити підпис перед відправленням у Solana.</p>
      </div>
      {isAuthenticated ? (
        <form className="create-form sign-transaction-form" onSubmit={handleSubmit}>
          <label>
            ID транзакції
            <input
              inputMode="numeric"
              autoComplete="off"
              value={transactionId}
              onChange={(event) => {
                setTransactionId(event.target.value)
                setFormError('')
              }}
              placeholder="Наприклад, 12"
              required
            />
          </label>
          <label>
            unsigned_transaction <span>Base64 з blockchain_transaction</span>
            <textarea
              value={unsignedTransaction}
              onChange={(event) => {
                setUnsignedTransaction(event.target.value)
                setFormError('')
              }}
              placeholder="Встав Base64-транзакцію"
              rows={7}
              required
            />
          </label>
          <div className="form-note">
            <span>◎</span> Перед підписом перевір адресу гаманця та деталі транзакції у Phantom. Цей endpoint доступний лише staff-користувачам.
          </div>
          {formError && <p className="form-error" role="alert">{formError}</p>}
          <div className="form-actions">
            <button type="button" className="button button-outline" onClick={onCancel} disabled={busy}>
              Скасувати
            </button>
            <button type="submit" className="button button-primary" disabled={busy}>
              {busy ? <><span className="spinner" /> Очікуємо підтвердження…</> : <><WalletIcon /> Підписати й відправити</>}
            </button>
          </div>
        </form>
      ) : (
        <div className="state-card auth-prompt">
          <span className="empty-icon"><WalletIcon /></span>
          <h2>Спершу підключи гаманець</h2>
          <p>Увійди через Phantom гаманцем адміністратора, для якого підготовлено транзакцію.</p>
          <button className="button button-primary" type="button" onClick={onConnect}>
            <WalletIcon /> Підключити гаманець
          </button>
        </div>
      )}
    </section>
  )
}
