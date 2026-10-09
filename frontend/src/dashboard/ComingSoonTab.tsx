export function ComingSoonTab({ title, description }: { title: string; description: string }) {
  return (
    <section className="panel" aria-labelledby="coming-soon-title">
      <h2 id="coming-soon-title">{title}</h2>
      <p>{description}</p>
      <p className="muted">Coming soon in a later release.</p>
    </section>
  )
}
