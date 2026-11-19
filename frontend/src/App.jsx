import { useCallback, useEffect, useMemo, useState } from 'react'
import bs58 from 'bs58'
import { ComputeBudgetProgram, Keypair } from '@solana/web3.js'
import SiteHeader from './components/SiteHeader.jsx'
import Feedback from './components/Feedback.jsx'
import EventsPage from './components/EventsPage.jsx'
import EventDetailsPage from './components/EventDetailsPage.jsx'
import CreateEventPage from './components/CreateEventPage.jsx'
import SignTransactionPage from './components/SignTransactionPage.jsx'
import MyTicketsPage from './components/MyTicketsPage.jsx'
import TicketQrPage from './components/TicketQrPage.jsx'
import OrganizerEventsPage from './components/OrganizerEventsPage.jsx'
import TicketTypeForm from './components/TicketTypeForm.jsx'
import SiteFooter from './components/SiteFooter.jsx'
import { apiRequest } from './utils/api.js'
import { readPageFromLocation } from './utils/events.js'
import {
  decodeTransaction,
  encodeTransaction,
  formatSol,
  getPhantom,
  shortAddress,
  solToLamports,
} from './utils/solana.js'
import './App.css'

const TOKEN_KEY = 'nft-tickets-auth-token'
const THEME_KEY = 'nftickets-theme'

function getStoredTheme() {
  const savedTheme = window.localStorage.getItem(THEME_KEY)
  return savedTheme === 'light' || savedTheme === 'dark' ? savedTheme : 'dark'
}

function getInitialSearch() {
  return new URLSearchParams(window.location.search).get('q') || ''
}

function getInitialSessionLoading() {
  return Boolean(window.localStorage.getItem(TOKEN_KEY))
}

function App() {
  const [session, setSession] = useState(null)
  const [loadingSession, setLoadingSession] = useState(getInitialSessionLoading)
  const [events, setEvents] = useState([])
  const [loadingEvents, setLoadingEvents] = useState(true)
  const [detailData, setDetailData] = useState(null)
  const [detailRefreshKey, setDetailRefreshKey] = useState(0)
  const [organizerEventsRefreshKey, setOrganizerEventsRefreshKey] = useState(0)
  const [pendingPurchase, setPendingPurchase] = useState(null)
  const [page, setPage] = useState(readPageFromLocation)
  const [searchOpen, setSearchOpen] = useState(() => Boolean(getInitialSearch()))
  const [searchQuery, setSearchQuery] = useState(getInitialSearch)
  const [theme, setTheme] = useState(getStoredTheme)
  const [profileOpen, setProfileOpen] = useState(false)
  const [showTicketTypeForm, setShowTicketTypeForm] = useState(false)
  const [busyAction, setBusyAction] = useState('')
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState('')

  const authToken = session?.token
  const currentDetail =
    page.type === 'detail' &&
    detailData?.slug === page.slug &&
    detailData?.token === authToken
      ? detailData
      : null
  const eventDetails = currentDetail?.event || null
  const ticketTypes = currentDetail?.ticketTypes || []
  const loadingDetails = page.type === 'detail' && (!currentDetail || currentDetail.status === 'loading')

  const filteredEvents = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLocaleLowerCase('uk-UA')
    if (!normalizedQuery) return events
    return events.filter((event) =>
      `${event.title} ${event.description || ''}`.toLocaleLowerCase('uk-UA').includes(normalizedQuery),
    )
  }, [events, searchQuery])

  const navigate = useCallback((path) => {
    window.history.pushState({}, '', path)
    setPage(readPageFromLocation())
    setSearchQuery(new URLSearchParams(window.location.search).get('q') || '')
    setError('')
    setProfileOpen(false)
  }, [])

  useEffect(() => {
    function handlePopState() {
      setPage(readPageFromLocation())
      setSearchQuery(new URLSearchParams(window.location.search).get('q') || '')
      setError('')
    }
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    window.localStorage.setItem(THEME_KEY, theme)
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      'content',
      theme === 'dark' ? '#0e1221' : '#f4f5fb',
    )
  }, [theme])

  useEffect(() => {
    const storedToken = window.localStorage.getItem(TOKEN_KEY)
    if (!storedToken) return

    apiRequest('/auth/me/', { token: storedToken })
      .then((profile) => {
        const walletAddress = profile.wallet_addresses?.[0]
        if (walletAddress) setSession({ token: storedToken, walletAddress })
        else window.localStorage.removeItem(TOKEN_KEY)
      })
      .catch(() => window.localStorage.removeItem(TOKEN_KEY))
      .finally(() => setLoadingSession(false))
  }, [])

  useEffect(() => {
    let active = true
    apiRequest('/events/', { token: authToken })
      .then((response) => {
        if (active) setEvents(Array.isArray(response) ? response : response.results || [])
      })
      .catch((requestError) => {
        if (active) setError(requestError.message)
      })
      .finally(() => {
        if (active) setLoadingEvents(false)
      })
    return () => {
      active = false
    }
  }, [authToken])

  useEffect(() => {
    if (page.type !== 'detail') return undefined

    let active = true
    Promise.all([
      apiRequest(`/events/${encodeURIComponent(page.slug)}/`, { token: authToken }),
      apiRequest(`/events/${encodeURIComponent(page.slug)}/ticket-types/`, { token: authToken }),
    ])
      .then(([details, response]) => {
        if (!active) return
        setDetailData({
          slug: page.slug,
          token: authToken,
          event: details,
          ticketTypes: Array.isArray(response) ? response : response.results || [],
          status: 'loaded',
        })
      })
      .catch((requestError) => {
        if (!active) return
        setError(requestError.message)
        setDetailData({ slug: page.slug, token: authToken, status: 'error' })
      })

    return () => {
      active = false
    }
  }, [page.type, page.slug, authToken, detailRefreshKey])

  useEffect(() => {
    if (!pendingPurchase || !authToken) return undefined

    let active = true
    let timeoutId

    async function checkPurchaseConfirmation() {
      try {
        const confirmation = await apiRequest(
          `/blockchain/transactions/${pendingPurchase.transactionId}/confirm/`,
          { method: 'POST', token: authToken },
        )
        if (!active) return

        if (confirmation.status === 'confirmed') {
          setPendingPurchase(null)
          setDetailRefreshKey((current) => current + 1)
          setNotice({
            type: 'success',
            text: `Квиток придбано за ${formatSol(pendingPurchase.priceInLamports)}. Дані події оновлено.`,
            signature: confirmation.signature || pendingPurchase.signature,
          })
          return
        }

        timeoutId = window.setTimeout(checkPurchaseConfirmation, 3000)
      } catch (requestError) {
        if (!active) return
        setPendingPurchase(null)
        setError(`Не вдалося перевірити підтвердження покупки: ${requestError.message}`)
      }
    }

    checkPurchaseConfirmation()
    return () => {
      active = false
      window.clearTimeout(timeoutId)
    }
  }, [pendingPurchase, authToken])

  async function loginWithPhantom() {
    setBusyAction('login')
    setError('')
    setNotice(null)
    try {
      const provider = getPhantom()
      const connection = await provider.connect()
      const walletAddress = connection.publicKey.toString()
      const challenge = await apiRequest('/auth/nonce/', {
        method: 'POST',
        body: JSON.stringify({ wallet_address: walletAddress }),
      })
      const signedMessage = await provider.signMessage(
        new TextEncoder().encode(challenge.message),
        'utf8',
      )
      const signature = bs58.encode(signedMessage.signature)
      const login = await apiRequest('/auth/verify/', {
        method: 'POST',
        body: JSON.stringify({
          wallet_address: walletAddress,
          nonce: challenge.nonce,
          signature,
        }),
      })
      window.localStorage.setItem(TOKEN_KEY, login.token)
      setSession({ token: login.token, walletAddress: login.wallet_address })
      setNotice({ type: 'success', text: 'Гаманець успішно авторизовано.' })
    } catch (requestError) {
      setError(requestError.message || 'Не вдалося увійти через Phantom.')
    } finally {
      setBusyAction('')
    }
  }

  async function logout() {
    setBusyAction('logout')
    setError('')
    try {
      if (session?.token) {
        await apiRequest('/auth/logout/', { method: 'POST', token: session.token })
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      window.localStorage.removeItem(TOKEN_KEY)
      setSession(null)
      setNotice(null)
      setProfileOpen(false)
      setBusyAction('')
    }
  }

  async function requireMatchingWallet() {
    if (!session) throw new Error('Спочатку увійди через Phantom.')
    const provider = getPhantom()
    const connection = await provider.connect()
    const connectedAddress = connection.publicKey.toString()
    if (connectedAddress !== session.walletAddress) {
      window.localStorage.removeItem(TOKEN_KEY)
      setSession(null)
      throw new Error('У Phantom вибрано інший гаманець. Увійди повторно з потрібною адресою.')
    }
    if (typeof provider.signTransaction !== 'function') {
      throw new Error('Ця версія Phantom не підтримує підпис транзакцій.')
    }
    return provider
  }

  async function submitAndConfirm(chainTransaction, provider, partialSigner) {
    const transaction = decodeTransaction(chainTransaction.unsigned_transaction)
    const feePayer = transaction.feePayer?.toBase58()
    if (feePayer !== session.walletAddress) {
      throw new Error(
        `Цю транзакцію має підписати гаманець адміністратора ${feePayer || '(не вказаний)'}. ` +
          `Зараз підключено ${session.walletAddress}.`,
      )
    }

    if (partialSigner) transaction.partialSign(partialSigner)
    const expectedMessage = transaction.serializeMessage()
    const expectedFeePayer = transaction.feePayer?.toBase58()
    const expectedBlockhash = transaction.recentBlockhash
    const expectedInstructions = transaction.instructions.map((instruction) => ({
      programId: instruction.programId.toBase58(),
      data: Uint8Array.from(instruction.data),
      keys: instruction.keys.map((key) => ({
        pubkey: key.pubkey.toBase58(),
        isSigner: key.isSigner,
        isWritable: key.isWritable,
      })),
    }))
    const signedTransaction = await provider.signTransaction(transaction)
    const signedMessage = signedTransaction.serializeMessage()
    if (
      expectedMessage.length !== signedMessage.length ||
      expectedMessage.some((byte, index) => byte !== signedMessage[index])
    ) {
      const differences = []
      const signedFeePayer = signedTransaction.feePayer?.toBase58()
      if (expectedFeePayer !== signedFeePayer) {
        differences.push(
          `платника (${expectedFeePayer || 'не вказаний'} → ${signedFeePayer || 'не вказаний'})`,
        )
      }
      if (expectedBlockhash !== signedTransaction.recentBlockhash) {
        differences.push('blockhash')
      }

      const signedInstructions = signedTransaction.instructions
      const signedProgramInstructions = []
      const seenComputeBudgetVariants = new Set()
      for (const instruction of signedInstructions) {
        if (instruction.programId.equals(ComputeBudgetProgram.programId)) {
          const variant = instruction.data[0]
          const validLengths = { 1: 5, 2: 5, 3: 9, 4: 5 }
          if (
            instruction.keys.length !== 0 ||
            validLengths[variant] !== instruction.data.length ||
            seenComputeBudgetVariants.has(variant)
          ) {
            differences.push('некоректної інструкції Compute Budget')
            break
          }
          seenComputeBudgetVariants.add(variant)
          continue
        }
        signedProgramInstructions.push(instruction)
      }

      if (expectedInstructions.length !== signedProgramInstructions.length) {
        differences.push(
          `кількості програмних інструкцій (${expectedInstructions.length} → ${signedProgramInstructions.length})`,
        )
      }
      for (let index = 0; index < Math.min(expectedInstructions.length, signedProgramInstructions.length); index += 1) {
        const expected = expectedInstructions[index]
        const signed = signedProgramInstructions[index]
        const changes = []
        if (expected.programId !== signed.programId.toBase58()) changes.push('програма')
        if (
          expected.data.length !== signed.data.length ||
          expected.data.some((byte, dataIndex) => byte !== signed.data[dataIndex])
        ) {
          changes.push('дані')
        }
        if (expected.keys.length !== signed.keys.length) {
          changes.push('кількість акаунтів')
        } else if (
          expected.keys.some((key, keyIndex) => {
            const signedKey = signed.keys[keyIndex]
            return (
              key.pubkey !== signedKey.pubkey.toBase58() ||
              key.isSigner !== signedKey.isSigner ||
              key.isWritable !== signedKey.isWritable
            )
          })
        ) {
          changes.push('акаунти або їхні права')
        }
        if (changes.length > 0) differences.push(`інструкція ${index + 1}: ${changes.join(', ')}`)
      }
      if (differences.length > 0) {
        throw new Error(
          `Після підпису повідомлення транзакції змінилося: ${differences.join('; ')}. ` +
            'Транзакцію не відправлено. Дані у дужках — публічні параметри транзакції.',
        )
      }
    }

    const submission = await apiRequest(
      `/blockchain/transactions/${chainTransaction.id}/submit/`,
      {
        method: 'POST',
        token: session.token,
        body: JSON.stringify({
          signed_transaction: encodeTransaction(signedTransaction),
        }),
      },
    )

    for (let attempt = 0; attempt < 30; attempt += 1) {
      const confirmation = await apiRequest(
        `/blockchain/transactions/${chainTransaction.id}/confirm/`,
        { method: 'POST', token: session.token },
      )
      if (confirmation.status === 'confirmed') {
        return { signature: confirmation.signature || submission.signature }
      }
      if (confirmation.status === 'failed') {
        throw new Error(confirmation.error || 'Solana відхилила транзакцію.')
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2000))
    }

    return { signature: submission.signature, pending: true }
  }

  async function signPreparedTransaction(transactionId, unsignedTransaction) {
    setBusyAction('sign-transaction')
    setError('')
    setNotice(null)
    try {
      const provider = await requireMatchingWallet()
      const result = await submitAndConfirm(
        { id: transactionId, unsigned_transaction: unsignedTransaction },
        provider,
      )
      setNotice({
        type: result.pending ? 'pending' : 'success',
        text: result.pending
          ? 'Транзакцію відправлено. Вона ще очікує підтвердження мережею.'
          : 'Транзакцію підписано й підтверджено в Solana.',
        signature: result.signature,
      })
    } catch (requestError) {
      setError(requestError.message || 'Не вдалося підписати й відправити транзакцію.')
    } finally {
      setBusyAction('')
    }
  }

  async function withdrawEventFunds(event) {
    setBusyAction(`withdraw-${event.id}`)
    setError('')
    setNotice(null)
    try {
      const provider = await requireMatchingWallet()
      const prepared = await apiRequest(`/blockchain/events/${event.id}/withdraw/`, {
        method: 'POST',
        token: session.token,
      })
      const result = await submitAndConfirm(
        {
          id: prepared.chain_transaction_id,
          unsigned_transaction: prepared.unsigned_transaction,
        },
        provider,
      )
      setOrganizerEventsRefreshKey((current) => current + 1)
      setNotice({
        type: result.pending ? 'pending' : 'success',
        text: result.pending
          ? 'Транзакцію виведення відправлено. Перевір її підтвердження в Solana.'
          : 'Кошти успішно виведено на гаманець організатора.',
        signature: result.signature,
      })
    } catch (requestError) {
      setError(requestError.message || 'Не вдалося вивести кошти з події.')
    } finally {
      setBusyAction('')
    }
  }

  async function checkEventWithdrawal(event) {
    if (!session) return
    setBusyAction(`check-withdrawal-${event.id}`)
    setError('')
    setNotice(null)
    try {
      const result = await apiRequest(
        `/blockchain/transactions/${event.withdrawal_transaction_id}/confirm/`,
        { method: 'POST', token: session.token },
      )
      if (result.status === 'confirmed') {
        setOrganizerEventsRefreshKey((current) => current + 1)
        setNotice({
          type: 'success',
          text: 'Виведення коштів підтверджено в Solana.',
          signature: result.signature,
        })
      } else {
        setNotice({
          type: 'pending',
          text: 'Транзакція ще очікує підтвердження мережею Solana.',
          signature: result.signature,
        })
      }
    } catch (requestError) {
      setOrganizerEventsRefreshKey((current) => current + 1)
      setError(requestError.message || 'Не вдалося перевірити статус виведення.')
    } finally {
      setBusyAction('')
    }
  }

  async function cancelOrganizerEvent(event) {
    if (!window.confirm(
      `Скасувати подію «${event.title}»? Цю дію неможливо скасувати. Повернення коштів за квитки виконується окремо.`,
    )) return

    setBusyAction(`cancel-${event.id}`)
    setError('')
    setNotice(null)
    try {
      const provider = await requireMatchingWallet()
      const prepared = await apiRequest(`/blockchain/events/${event.id}/cancel/`, {
        method: 'POST',
        token: session.token,
      })
      const result = await submitAndConfirm(
        {
          id: prepared.chain_transaction_id,
          unsigned_transaction: prepared.unsigned_transaction,
        },
        provider,
      )
      setOrganizerEventsRefreshKey((current) => current + 1)
      setNotice({
        type: result.pending ? 'pending' : 'success',
        text: result.pending
          ? 'Транзакцію скасування відправлено. Подію буде позначено скасованою після підтвердження в Solana.'
          : 'Подію скасовано в Solana. Повернення коштів за квитки обробляється окремо.',
        signature: result.signature,
      })
    } catch (requestError) {
      setOrganizerEventsRefreshKey((current) => current + 1)
      setError(requestError.message || 'Не вдалося скасувати подію.')
    } finally {
      setBusyAction('')
    }
  }

  async function checkEventCancellation(event) {
    if (!session) return
    setBusyAction(`check-cancellation-${event.id}`)
    setError('')
    setNotice(null)
    try {
      const result = await apiRequest(
        `/blockchain/transactions/${event.cancellation_transaction_id}/confirm/`,
        { method: 'POST', token: session.token },
      )
      setOrganizerEventsRefreshKey((current) => current + 1)
      setNotice({
        type: result.status === 'confirmed' ? 'success' : 'pending',
        text: result.status === 'confirmed'
          ? 'Скасування події підтверджено в Solana. Повернення коштів за квитки обробляється окремо.'
          : 'Транзакція скасування ще очікує підтвердження мережею Solana.',
        signature: result.signature,
      })
    } catch (requestError) {
      setOrganizerEventsRefreshKey((current) => current + 1)
      setError(requestError.message || 'Не вдалося перевірити статус скасування.')
    } finally {
      setBusyAction('')
    }
  }

  async function refundTicket(ticket) {
    if (!session) return
    if (!window.confirm(
      `Повернути квиток «${ticket.name}» за ${formatSol(ticket.price_paid_lamports)}? NFT буде спалено, а кошти повернуться на гаманець.`,
    )) return

    setBusyAction(`refund-${ticket.id}`)
    setError('')
    setNotice(null)
    try {
      const provider = await requireMatchingWallet()
      const prepared = await apiRequest(`/tickets/${ticket.id}/refund/`, {
        method: 'POST',
        token: session.token,
      })
      const result = await submitAndConfirm(
        {
          id: prepared.chain_transaction_id,
          unsigned_transaction: prepared.unsigned_transaction,
        },
        provider,
      )
      setDetailRefreshKey((current) => current + 1)
      setNotice({
        type: result.pending ? 'pending' : 'success',
        text: result.pending
          ? 'Повернення надіслано в Solana. Перевір статус транзакції, щоб завершити повернення.'
          : `Квиток повернено за ${formatSol(ticket.price_paid_lamports)}. NFT спалено.`,
        signature: result.signature,
      })
    } catch (requestError) {
      setDetailRefreshKey((current) => current + 1)
      setError(requestError.message || 'Не вдалося повернути квиток.')
    } finally {
      setBusyAction('')
    }
  }

  async function checkTicketRefund(ticket) {
    if (!session || !ticket.refund_transaction_id) return
    setBusyAction(`check-refund-${ticket.id}`)
    setError('')
    setNotice(null)
    try {
      const result = await apiRequest(
        `/blockchain/transactions/${ticket.refund_transaction_id}/confirm/`,
        { method: 'POST', token: session.token },
      )
      setDetailRefreshKey((current) => current + 1)
      setNotice({
        type: result.status === 'confirmed' ? 'success' : 'pending',
        text: result.status === 'confirmed'
          ? `Повернення ${formatSol(ticket.price_paid_lamports)} підтверджено. Квиток більше не дійсний.`
          : 'Повернення ще очікує підтвердження мережею Solana.',
        signature: result.signature,
      })
    } catch (requestError) {
      setDetailRefreshKey((current) => current + 1)
      setError(requestError.message || 'Не вдалося перевірити статус повернення.')
    } finally {
      setBusyAction('')
    }
  }

  async function reportEventNotHeld(ticket) {
    if (!session || !eventDetails) return
    setBusyAction('report-event')
    setError('')
    setNotice(null)
    try {
      await apiRequest(
        `/events/${encodeURIComponent(eventDetails.slug)}/disputes/`,
        {
          method: 'POST',
          token: session.token,
          body: JSON.stringify({ ticket_id: ticket.id }),
        },
      )
      setDetailRefreshKey((current) => current + 1)
      setNotice({
        type: 'success',
        text: 'Скаргу прийнято та враховано для фіналізації події.',
      })
    } catch (requestError) {
      setError(requestError.message || 'Не вдалося подати скаргу.')
    } finally {
      setBusyAction('')
    }
  }

  async function createEvent(eventInput) {
    setBusyAction('create-event')
    setError('')
    setNotice(null)
    try {
      const provider = await requireMatchingWallet()
      const collectionKeypair = Keypair.generate()
      const eventFormData = new FormData()
      eventFormData.set('title', eventInput.title)
      eventFormData.set('description', eventInput.description)
      eventFormData.set('event_date', eventInput.event_date)
      eventFormData.set('image', eventInput.image)
      eventFormData.set('collection_address', collectionKeypair.publicKey.toBase58())
      const prepared = await apiRequest('/events/', {
        method: 'POST',
        token: session.token,
        body: eventFormData,
      })
      const result = await submitAndConfirm(
        prepared.blockchain_transaction,
        provider,
        collectionKeypair,
      )
      if (result.pending) {
        setNotice({
          type: 'pending',
          text: 'Транзакцію створення події відправлено, але вона ще не підтверджена. Додані у формі типи квитків поки не створено; їх можна буде додати на сторінці події після підтвердження.',
          signature: result.signature,
        })
        navigate(`/events/${encodeURIComponent(prepared.event.slug)}/`)
        return
      }

      let createdTicketTypeCount = 0
      let ticketTypeIssue = ''
      for (const ticketType of eventInput.ticketTypes) {
        try {
          const ticketTypeFormData = new FormData()
          ticketTypeFormData.set('name', ticketType.name)
          ticketTypeFormData.set('description', ticketType.description)
          ticketTypeFormData.set('price_in_lamports', solToLamports(ticketType.price_sol))
          ticketTypeFormData.set('max_supply', ticketType.max_supply)
          ticketTypeFormData.set('image', ticketType.image)
          const preparedTicketType = await apiRequest(
            `/events/${encodeURIComponent(prepared.event.slug)}/ticket-types/`,
            {
              method: 'POST',
              token: session.token,
              body: ticketTypeFormData,
            },
          )
          const ticketTypeResult = await submitAndConfirm(
            preparedTicketType.blockchain_transaction,
            provider,
          )
          if (ticketTypeResult.pending) {
            ticketTypeIssue = `Створення типу «${ticketType.name}» ще очікує підтвердження в Solana; інші типи поки не оброблялися.`
            break
          }
          createdTicketTypeCount += 1
        } catch (ticketTypeError) {
          ticketTypeIssue = `Не вдалося створити тип «${ticketType.name}»: ${ticketTypeError.message}`
          break
        }
      }

      let refreshWarning = ''
      try {
        const refreshedEvents = await apiRequest('/events/', { token: session.token })
        setEvents(Array.isArray(refreshedEvents) ? refreshedEvents : refreshedEvents.results || [])
      } catch (refreshError) {
        refreshWarning = ` Список подій не вдалося оновити: ${refreshError.message}`
      }
      setNotice({
        type: ticketTypeIssue ? 'pending' : 'success',
        text: ticketTypeIssue
          ? `Подію створено. Успішно створено типів квитків: ${createdTicketTypeCount} з ${eventInput.ticketTypes.length}. ${ticketTypeIssue} Відкрий деталі події, щоб додати решту.${refreshWarning}`
          : `Подію та ${createdTicketTypeCount} ${createdTicketTypeCount === 1 ? 'тип квитка створено' : 'типи квитків створено'} в Solana. Підпис: ${shortAddress(result.signature)}.${refreshWarning}`,
        signature: result.signature,
      })
      navigate(`/events/${encodeURIComponent(prepared.event.slug)}/`)
    } catch (requestError) {
      setError(requestError.message || 'Не вдалося створити подію.')
    } finally {
      setBusyAction('')
    }
  }

  async function createTicketType(ticketTypeInput) {
    if (!eventDetails) return
    setBusyAction('create-ticket-type')
    setError('')
    setNotice(null)
    try {
      const provider = await requireMatchingWallet()
      const ticketTypeFormData = new FormData()
      ticketTypeFormData.set('name', ticketTypeInput.name)
      ticketTypeFormData.set('description', ticketTypeInput.description)
      ticketTypeFormData.set('price_in_lamports', solToLamports(ticketTypeInput.price_sol))
      ticketTypeFormData.set('max_supply', ticketTypeInput.max_supply)
      ticketTypeFormData.set('image', ticketTypeInput.image)
      const prepared = await apiRequest(
        `/events/${encodeURIComponent(eventDetails.slug)}/ticket-types/`,
        {
          method: 'POST',
          token: session.token,
          body: ticketTypeFormData,
        },
      )
      const result = await submitAndConfirm(prepared.blockchain_transaction, provider)
      setShowTicketTypeForm(false)
      if (!result.pending) {
        const refreshedTypes = await apiRequest(
          `/events/${encodeURIComponent(eventDetails.slug)}/ticket-types/`,
          { token: session.token },
        )
        const updatedTicketTypes = Array.isArray(refreshedTypes) ? refreshedTypes : refreshedTypes.results || []
        setDetailData((current) =>
          current?.slug === eventDetails.slug && current?.token === session.token
            ? { ...current, ticketTypes: updatedTicketTypes }
            : current,
        )
      }
      setNotice({
        type: result.pending ? 'pending' : 'success',
        text: result.pending
          ? 'Створення типу квитка очікує підтвердження в Solana.'
          : `Тип квитка «${ticketTypeInput.name}» створено.`,
        signature: result.signature,
      })
    } catch (requestError) {
      setError(requestError.message || 'Не вдалося створити тип квитка.')
    } finally {
      setBusyAction('')
    }
  }

  async function buyTicket(ticketType) {
    if (!session) {
      await loginWithPhantom()
      return
    }
    setBusyAction(`buy-${ticketType.id}`)
    setError('')
    setNotice(null)
    try {
      const provider = await requireMatchingWallet()
      const assetKeypair = Keypair.generate()
      const prepared = await apiRequest(
        `/payments/events/${encodeURIComponent(eventDetails.slug)}/ticket-types/${ticketType.id}/purchase/`,
        {
          method: 'POST',
          token: session.token,
          body: JSON.stringify({
            buyer_wallet: session.walletAddress,
            asset_address: assetKeypair.publicKey.toBase58(),
            metadata_uri: ticketType.metadata_uri || eventDetails.metadata_uri,
          }),
        },
      )
      const result = await submitAndConfirm(
        {
          id: prepared.chain_transaction_id,
          unsigned_transaction: prepared.unsigned_transaction,
        },
        provider,
        assetKeypair,
      )
      if (result.pending) {
        setPendingPurchase({
          transactionId: prepared.chain_transaction_id,
          priceInLamports: prepared.price_in_lamports,
          signature: result.signature,
        })
        setNotice({
          type: 'pending',
          text: 'Оплату відправлено. Очікуємо підтвердження в Solana; дані квитків оновляться автоматично.',
          signature: result.signature,
        })
      } else {
        setDetailRefreshKey((current) => current + 1)
        setNotice({
          type: 'success',
          text: `Квиток придбано за ${formatSol(prepared.price_in_lamports)}. Дані події оновлюються.`,
          signature: result.signature,
        })
      }
    } catch (requestError) {
      setError(requestError.message || 'Не вдалося придбати квиток.')
    } finally {
      setBusyAction('')
    }
  }

  function submitSearch(event) {
    event.preventDefault()
    const query = searchQuery.trim()
    navigate(query ? `/?q=${encodeURIComponent(query)}` : '/')
  }

  function toggleSearch() {
    if (searchOpen) {
      setSearchOpen(false)
      setSearchQuery('')
      if (new URLSearchParams(window.location.search).has('q')) navigate('/')
      return
    }
    setSearchOpen(true)
    if (page.type !== 'list') navigate('/')
  }

  return (
    <div className="app-shell" data-theme={theme}>
      <SiteHeader
        pageType={page.type}
        session={session}
        theme={theme}
        searchOpen={searchOpen}
        searchQuery={searchQuery}
        busyAction={busyAction}
        profileOpen={profileOpen}
        onNavigate={navigate}
        onSearchChange={setSearchQuery}
        onSearchSubmit={submitSearch}
        onSearchToggle={toggleSearch}
        onThemeToggle={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
        onConnect={loginWithPhantom}
        onProfileToggle={() => setProfileOpen((current) => !current)}
        onLogout={logout}
      />

      <main className="page-main">
        <div className="page-content">
          <Feedback
            error={error}
            notice={notice}
            onDismissError={() => setError('')}
            onDismissNotice={() => setNotice(null)}
          />
          {page.type === 'list' && (
            <EventsPage
              events={filteredEvents}
              loading={loadingEvents}
              query={searchQuery}
              isAuthenticated={Boolean(session)}
              onOpenEvent={(slug) => navigate(`/events/${encodeURIComponent(slug)}/`)}
              onCreateEvent={() => navigate('/events/new/')}
              onConnect={loginWithPhantom}
            />
          )}
          {page.type === 'detail' && (
            <EventDetailsPage
              event={eventDetails}
              ticketTypes={ticketTypes}
              loading={loadingDetails}
              session={session}
              busyAction={busyAction}
              ticketRefreshKey={detailRefreshKey}
              onBuy={buyTicket}
              onRefundTicket={refundTicket}
              onCheckTicketRefund={checkTicketRefund}
              onReportEvent={reportEventNotHeld}
              onBack={() => navigate('/')}
              onCreateTicketType={() => setShowTicketTypeForm(true)}
            />
          )}
          {page.type === 'create' && (
            <CreateEventPage
              busy={busyAction === 'create-event'}
              isAuthenticated={Boolean(session)}
              organizerWallet={session?.walletAddress}
              onSubmit={createEvent}
              onConnect={loginWithPhantom}
              onCancel={() => navigate('/')}
            />
          )}
          {page.type === 'sign-transaction' && (
            <SignTransactionPage
              busy={busyAction === 'sign-transaction'}
              isAuthenticated={Boolean(session)}
              onSubmit={signPreparedTransaction}
              onConnect={loginWithPhantom}
              onCancel={() => navigate('/')}
            />
          )}
          {page.type === 'my-tickets' && (
            <MyTicketsPage
              session={session}
              refreshKey={detailRefreshKey}
              busyAction={busyAction}
              onNavigate={navigate}
              onConnect={loginWithPhantom}
              onRefundTicket={refundTicket}
              onCheckTicketRefund={checkTicketRefund}
            />
          )}
          {page.type === 'ticket-qr' && (
            <TicketQrPage
              ticketId={page.ticketId}
              session={session}
              onNavigate={navigate}
              onConnect={loginWithPhantom}
            />
          )}
          {page.type === 'organizer-events' && (
            <OrganizerEventsPage
              session={session}
              refreshKey={organizerEventsRefreshKey}
              busyAction={busyAction}
              onNavigate={navigate}
              onConnect={loginWithPhantom}
              onWithdraw={withdrawEventFunds}
              onCheckWithdrawal={checkEventWithdrawal}
              onCancel={cancelOrganizerEvent}
              onCheckCancellation={checkEventCancellation}
            />
          )}
        </div>
      </main>

      <SiteFooter onNavigate={navigate} />

      {showTicketTypeForm && (
        <TicketTypeForm
          busy={busyAction === 'create-ticket-type'}
          onClose={() => setShowTicketTypeForm(false)}
          onSubmit={createTicketType}
        />
      )}
      {loadingSession && <div className="session-loading" aria-label="Перевірка сесії"><span className="spinner" /></div>}
    </div>
  )
}

export default App
