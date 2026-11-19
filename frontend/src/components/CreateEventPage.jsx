import { useEffect, useRef, useState } from 'react'
import { ArrowBackIcon, ArrowIcon, PlusIcon, TicketIcon, WalletIcon } from './Icons.jsx'

function createTicketType(id) {
  return { id, name: '', description: '', price_sol: '', max_supply: '', image: null, preview: '' }
}

export default function CreateEventPage({
  busy,
  isAuthenticated,
  organizerWallet,
  onSubmit,
  onConnect,
  onCancel,
}) {
  const [form, setForm] = useState({ title: '', description: '', event_date: '', image: null })
  const [formError, setFormError] = useState('')
  const titleBytes = new TextEncoder().encode(form.title).length
  const [imagePreview, setImagePreview] = useState('')
  const [ticketTypes, setTicketTypes] = useState([createTicketType(1)])
  const nextTicketTypeId = useRef(2)
  const ticketPreviews = useRef(new Map())

  useEffect(() => () => {
    if (imagePreview) URL.revokeObjectURL(imagePreview)
  }, [imagePreview])

  useEffect(() => () => {
    ticketPreviews.current.forEach((preview) => URL.revokeObjectURL(preview))
  }, [])

  function updateField(event) {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }))
    setFormError('')
  }

  function updateImage(event) {
    const image = event.target.files?.[0] || null
    if (image && image.size > 5 * 1024 * 1024) {
      setFormError('Зображення має бути не більшим за 5 МБ.')
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

  function updateTicketField(ticketTypeId, event) {
    const { name, value } = event.target
    setTicketTypes((current) => current.map((ticketType) =>
      ticketType.id === ticketTypeId ? { ...ticketType, [name]: value } : ticketType,
    ))
    setFormError('')
  }

  function updateTicketImage(ticketTypeId, event) {
    const image = event.target.files?.[0] || null
    if (image && image.size > 5 * 1024 * 1024) {
      setFormError('Зображення NFT-квитка має бути не більшим за 5 МБ.')
      event.target.value = ''
      return
    }
    if (image && !['image/jpeg', 'image/png', 'image/webp'].includes(image.type)) {
      setFormError('Для NFT-квитків підтримуються формати JPEG, PNG та WebP.')
      event.target.value = ''
      return
    }
    const previousPreview = ticketPreviews.current.get(ticketTypeId)
    if (previousPreview) URL.revokeObjectURL(previousPreview)
    const preview = image ? URL.createObjectURL(image) : ''
    if (preview) ticketPreviews.current.set(ticketTypeId, preview)
    else ticketPreviews.current.delete(ticketTypeId)
    setTicketTypes((current) => current.map((ticketType) =>
      ticketType.id === ticketTypeId ? { ...ticketType, image, preview } : ticketType,
    ))
    setFormError('')
  }

  function addTicketType() {
    const id = nextTicketTypeId.current++
    setTicketTypes((current) => [...current, createTicketType(id)])
    setFormError('')
  }

  function removeTicketType(ticketTypeId) {
    const preview = ticketPreviews.current.get(ticketTypeId)
    if (preview) URL.revokeObjectURL(preview)
    ticketPreviews.current.delete(ticketTypeId)
    setTicketTypes((current) => current.filter((ticketType) => ticketType.id !== ticketTypeId))
    setFormError('')
  }

  function handleSubmit(event) {
    event.preventDefault()
    const date = new Date(form.event_date)
    if (Number.isNaN(date.getTime()) || date <= new Date()) {
      setFormError('Дата події має бути в майбутньому.')
      return
    }
    if (new TextEncoder().encode(form.title).length > 32) {
      setFormError('Назва має бути не довша за 32 байти у UTF-8 для Solana.')
      return
    }
    if (!form.image) {
      setFormError('Додай зображення афіші події.')
      return
    }
    if (!ticketTypes.length) {
      setFormError('Додай хоча б один тип квитка.')
      return
    }
    for (const [index, ticketType] of ticketTypes.entries()) {
      if (!ticketType.name.trim()) {
        setFormError(`Вкажи назву для типу квитка №${index + 1}.`)
        return
      }
      if (!Number.isFinite(Number(ticketType.price_sol)) || Number(ticketType.price_sol) <= 0) {
        setFormError(`Вкажи ціну в SOL для типу квитка «${ticketType.name}».`)
        return
      }
      if (!/^[1-9]\d*$/.test(ticketType.max_supply)) {
        setFormError(`Кількість квитків «${ticketType.name}» має бути цілим числом більше нуля.`)
        return
      }
      if (!ticketType.image) {
        setFormError(`Додай зображення NFT-квитка «${ticketType.name}».`)
        return
      }
    }
    onSubmit({
      ...form,
      event_date: date.toISOString(),
      ticketTypes: ticketTypes.map((ticketType) => ({
        name: ticketType.name,
        description: ticketType.description,
        price_sol: ticketType.price_sol,
        max_supply: ticketType.max_supply,
        image: ticketType.image,
      })),
    })
  }

  return (
    <section className="create-page">
      <button className="text-button back-button" onClick={onCancel}><ArrowBackIcon /> Усі події</button>
      <div className="create-page-heading">
        <div className="eyebrow"><span className="eyebrow-line" /> ДЛЯ ОРГАНІЗАТОРІВ</div>
        <h1>Створи подію<br /><em>для своєї спільноти.</em></h1>
        <p>Опиши подію та підготуй її до публікації в Solana.</p>
      </div>
      {isAuthenticated ? (
        <form className="create-form" onSubmit={handleSubmit}>
          <label>
            Назва події <span>{titleBytes} / 32 байти у Solana</span>
            <input name="title" value={form.title} onChange={updateField} placeholder="Наприклад, Ніч музики" required />
          </label>
          <label>
            Опис <span>необов’язково</span>
            <textarea name="description" value={form.description} onChange={updateField} placeholder="Розкажи, чого очікувати…" rows={5} />
          </label>
          <div className="form-grid">
            <label>
              Дата й час події
              <input type="datetime-local" name="event_date" value={form.event_date} onChange={updateField} required />
            </label>
            <label>
              Зображення афіші <span>JPEG, PNG або WebP · до 5 МБ</span>
              <input type="file" name="image" accept="image/jpeg,image/png,image/webp" onChange={updateImage} required />
            </label>
          </div>
          {imagePreview && (
            <div className="image-preview-card">
              <img src={imagePreview} alt="Попередній перегляд афіші події" />
              <span>{form.image?.name}</span>
            </div>
          )}
          <section className="event-ticket-types">
            <div className="event-ticket-types-heading">
              <div>
                <div className="eyebrow"><span className="eyebrow-line" /> КАТЕГОРІЇ ДОСТУПУ</div>
                <h2>Типи <em>квитків.</em></h2>
                <p>Для кожного типу задай назву, ціну, кількість і зображення NFT.</p>
              </div>
              <button type="button" className="button button-outline" onClick={addTicketType} disabled={busy}>
                <PlusIcon /> Додати тип
              </button>
            </div>
            <div className="event-ticket-type-list">
              {ticketTypes.map((ticketType, index) => (
                <fieldset className="event-ticket-type-card" key={ticketType.id}>
                  <div className="event-ticket-type-card-heading">
                    <span><TicketIcon /> Тип квитка {String(index + 1).padStart(2, '0')}</span>
                    {ticketTypes.length > 1 && (
                      <button
                        type="button"
                        className="remove-ticket-type"
                        onClick={() => removeTicketType(ticketType.id)}
                        disabled={busy}
                      >
                        Видалити
                      </button>
                    )}
                  </div>
                  <label>
                    Назва квитка
                    <input
                      name="name"
                      maxLength={100}
                      value={ticketType.name}
                      onChange={(changeEvent) => updateTicketField(ticketType.id, changeEvent)}
                      placeholder={index === 0 ? 'Наприклад, Fan-Zone' : 'Наприклад, VIP-Zone'}
                      required
                    />
                  </label>
                  <label>
                    Опис <span>необов’язково</span>
                    <textarea
                      name="description"
                      value={ticketType.description}
                      onChange={(changeEvent) => updateTicketField(ticketType.id, changeEvent)}
                      placeholder="Що входить у цю категорію?"
                      rows={2}
                    />
                  </label>
                  <div className="form-grid">
                    <label>
                      Ціна в SOL
                      <input
                        type="number"
                        name="price_sol"
                        min="0.000000001"
                        step="0.000000001"
                        value={ticketType.price_sol}
                        onChange={(changeEvent) => updateTicketField(ticketType.id, changeEvent)}
                        placeholder="0.05"
                        required
                      />
                    </label>
                    <label>
                      Доступна кількість
                      <input
                        type="number"
                        name="max_supply"
                        min="1"
                        step="1"
                        value={ticketType.max_supply}
                        onChange={(changeEvent) => updateTicketField(ticketType.id, changeEvent)}
                        placeholder="100"
                        required
                      />
                    </label>
                  </div>
                  <label>
                    Зображення NFT-квитка <span>JPEG, PNG або WebP · до 5 МБ</span>
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      onChange={(changeEvent) => updateTicketImage(ticketType.id, changeEvent)}
                      required={!ticketType.image}
                    />
                  </label>
                  {ticketType.preview && (
                    <div className="image-preview-card ticket-type-image-preview">
                      <img src={ticketType.preview} alt={`Попередній перегляд NFT-квитка ${ticketType.name || index + 1}`} />
                      <span>{ticketType.image?.name}</span>
                    </div>
                  )}
                </fieldset>
              ))}
            </div>
          </section>
          <label>
            Гаманець організатора <span>визначено з підключеного гаманця</span>
            <input value={organizerWallet || ''} readOnly />
          </label>
          <div className="form-note">
            <span>◎</span>
            <span>
              Backend завантажить афішу та окремі зображення й JSON-метадані квитків до IPFS через Pinata. Після підтвердження події типи квитків створюватимуться в Solana по черзі; кожну транзакцію потрібно буде підписати у Phantom.
            </span>
          </div>
          {formError && <p className="form-error" role="alert">{formError}</p>}
          <div className="form-actions">
            <button type="button" className="button button-outline" onClick={onCancel} disabled={busy}>Скасувати</button>
            <button type="submit" className="button button-primary" disabled={busy}>
              {busy ? <><span className="spinner" /> Створення…</> : <>Створити й підписати <ArrowIcon /></>}
            </button>
          </div>
        </form>
      ) : (
        <div className="state-card auth-prompt">
          <span className="empty-icon"><WalletIcon /></span>
          <h2>Спершу підключи гаманець</h2>
          <p>Вхід через Phantom потрібен, щоб підтвердити організатора і підписати створення події.</p>
          <button className="button button-primary" onClick={onConnect}><WalletIcon /> Підключити гаманець</button>
        </div>
      )}
    </section>
  )
}
