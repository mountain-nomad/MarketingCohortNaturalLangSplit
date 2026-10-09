import { Link, Route, Routes } from 'react-router-dom'
import './App.css'

function HomePage() {
  return (
    <section className="page">
      <h2>Welcome</h2>
      <p>Describe an audience in plain English, review the interpretation, and split it into test and control groups.</p>
    </section>
  )
}

function LoginPage() {
  return (
    <section className="page" aria-labelledby="login-title">
      <h2 id="login-title">Sign in</h2>
      <p>Login is not available yet. It arrives with the authentication feature.</p>
    </section>
  )
}

function NotFoundPage() {
  return (
    <section className="page">
      <h2>Page not found</h2>
      <p>
        <Link to="/">Back to home</Link>
      </p>
    </section>
  )
}

export function App() {
  return (
    <div className="shell">
      <header className="shell-header">
        <h1>
          <Link to="/" className="brand">
            CohortSplit
          </Link>
        </h1>
        <nav aria-label="Primary">
          <Link to="/login">Sign in</Link>
        </nav>
      </header>
      <main className="shell-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  )
}
