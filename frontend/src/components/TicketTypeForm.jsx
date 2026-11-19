import { useEffect, useState } from 'react'
import { ArrowIcon } from './Icons.jsx'

export default function TicketTypeForm({ busy, onClose, onSubmit }) {
  const [form, setForm] = useState({ name: '', description: '', price_sol: '', max_supply: '', image: null })
  const [imagePreview, setImagePreview] = useState('')
  const [formError, setFormError] = useState('')

  useEffect(() => () => {
    if (imagePreview) URL.revokeObjectURL(imagePreview)
  }, [imagePreview])

  function updateField(event) {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }))
  }

  function updateImage(event) {
    const image = event.target.files?.[0] || null
    if (image && image.size > 5 * 1024 * 1024) {
      setFormError('Зображення NFT-квитка має бути не більшим за 5 МБ.')
      event.target.value = ''
      return
    }
    if (image && !['image/jpeg', 'image/png', 'image/webp'].includes(image.type)) {
      setFormError('Підтримуються формати JPEG, PNG та WebP.')
      event.target.value = ''
      return
    }
    if (imagePreview) URL.revokeObjectURL(imagePreview)
    setImagePreview(image ? URL.createObjectURL(image) : '')
    setForm((current) => ({ ...current, image }))
    setFormError('')
  }

  function handleSubmit(event) {
    event.preventDefault()
    if (!form.image) {
      setFormError('Додай зображення NFT-квитка.')
      return
    }
    setFormError('')
    onSubmit({ ...form, max_supply: Number(form.max_supply) })
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section className="event-modal" role="dialog" aria-modal="true" aria-labelledby="ticket-type-modal-title">
        <div className="modal-heading">
          <div><div className="eyebrow"><span className="eyebrow-line" /> КВИТКОВИЙ ТИП</div><h2 id="ticket-type-modal-title">Задай <em>умови.</em></h2></div>
          <button className="icon-button modal-close" onClick={onClose} disabled={busy} aria-label="Закрити">×</button>
        </div>
        <p className="modal-description">Тип стане доступним для продажу після підтвердження транзакції в Solana.</p>
        <form className="create-form modal-form" onSubmit={handleSubmit}>
          <label>Назва квитка<input autoFocus maxLength={100} name="name" value={form.name} onChange={updateField} placeholder="Наприклад, Стандарт" required /></label>
          <label>Опис <span>необов’язково</span><textarea name="description" value={form.description} onChange={updateField} placeholder="Що входить у квиток?" rows={3} /></label>
          <div className="form-grid">
            <label>Ціна в SOL<input type="number" min="0.000000001" step="0.000000001" name="price_sol" value={form.price_sol} onChange={updateField} placeholder="0.05" required /></label>
            <label>Кількість<input type="number" min="1" step="1" name="max_supply" value={form.max_supply} onChange={updateField} placeholder="100" required /></label>
          </div>
          <label>
            Зображення NFT-квитка <span>JPEG, PNG або WebP · до 5 МБ</span>
            <input type="file" accept="image/jpeg,image/png,image/webp" onChange={updateImage} required />
          </label>
          {imagePreview && (
            <div className="image-preview-card ticket-type-image-preview">
              <img src={imagePreview} alt={`Попередній перегляд NFT-квитка ${form.name || ''}`} />
              <span>{form.image?.name}</span>
            </div>
          )}
          <div className="form-note"><span>◎</span> Зображення та JSON-метадані NFT-квитка збережуться в IPFS; ціна та кількість будуть записані в Solana.</div>
          {formError && <p className="form-error" role="alert">{formError}</p>}
          <div className="form-actions">
            <button type="button" className="button button-outline" onClick={onClose} disabled={busy}>Скасувати</button>
            <button type="submit" className="button button-primary" disabled={busy}>
              {busy ? <><span className="spinner" /> Створення…</> : <>Створити й підписати <ArrowIcon /></>}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
