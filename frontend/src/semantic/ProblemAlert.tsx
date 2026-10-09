import type { Failure } from './failure.ts'

/** Server refusal with the unknown references it named (422 invalid_reference). */
export function ProblemAlert({ failure }: { failure: Failure }) {
  return (
    <div className="error" role="alert">
      <p>{failure.message}</p>
      {failure.problems.length > 0 && (
        <ul>
          {failure.problems.map((p) => (
            <li key={`${p.reference}-${p.problem}`}>
              {p.reference}: {p.problem}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
