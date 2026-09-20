import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './App.css'

// Deliberately no StrictMode: it mounts, unmounts and remounts in dev, which
// would open two WebSockets, and the bridge only lets one tab hold the
// controls — the second would be refused and the UI would look broken.
createRoot(document.getElementById('root')).render(<App />)
