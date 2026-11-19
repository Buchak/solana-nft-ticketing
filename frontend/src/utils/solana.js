import { Transaction } from '@solana/web3.js'

export const SOLANA_CLUSTER = import.meta.env.VITE_SOLANA_CLUSTER || 'localnet'

export function decodeTransaction(encodedTransaction) {
  const binary = window.atob(encodedTransaction)
  return Transaction.from(Uint8Array.from(binary, (character) => character.charCodeAt(0)))
}

export function encodeTransaction(transaction) {
  const bytes = transaction.serialize()
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000))
  }
  return window.btoa(binary)
}

export function getPhantom() {
  const provider = window.phantom?.solana || window.solana
  if (!provider?.isPhantom) {
    throw new Error('Phantom не знайдено. Встанови розширення Phantom та онови сторінку.')
  }
  return provider
}

export function solToLamports(value) {
  const [whole, fraction = ''] = String(value).split('.')
  if (!/^\d+$/.test(whole || '0') || !/^\d*$/.test(fraction) || fraction.length > 9) {
    throw new Error('Введи коректну ціну в SOL (до 9 знаків після коми).')
  }
  const lamports =
    BigInt(whole || '0') * 1_000_000_000n +
    BigInt((fraction + '000000000').slice(0, 9))
  if (lamports <= 0n) throw new Error('Ціна має бути більшою за 0 SOL.')
  return lamports.toString()
}

export function shortAddress(address) {
  return address ? `${address.slice(0, 4)}…${address.slice(-4)}` : ''
}

export function formatSol(lamports) {
  return `${(Number(lamports) / 1_000_000_000).toLocaleString('uk-UA', {
    maximumFractionDigits: 4,
  })} SOL`
}
