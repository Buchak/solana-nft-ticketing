import { SOLANA_CLUSTER } from '../utils/solana.js'

export default function SiteFooter({ onNavigate }) {
  return (
    <footer className="site-footer">
      <a className="brand" href="/" onClick={(event) => { event.preventDefault(); onNavigate('/') }}>
        <span className="brand-mark">N</span><span>NFTICKET</span>
      </a>
      <span>Квиток у майбутнє. На Solana.</span>
      <span className="footer-network"><span className="online-dot" /> Solana · {SOLANA_CLUSTER}</span>
    </footer>
  )
}
