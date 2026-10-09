import { ApiError, errorMessage } from '../api/client.ts'
import type { ReferenceProblem } from './types.ts'

/** A refused request, with the unknown references the server named (422 invalid_reference). */
export interface Failure {
  message: string
  problems: ReferenceProblem[]
}

function problemsOf(error: unknown): ReferenceProblem[] {
  if (!(error instanceof ApiError)) return []
  const problems = error.details.problems
  return Array.isArray(problems) ? (problems as ReferenceProblem[]) : []
}

export function failureFrom(error: unknown): Failure {
  return { message: errorMessage(error), problems: problemsOf(error) }
}
