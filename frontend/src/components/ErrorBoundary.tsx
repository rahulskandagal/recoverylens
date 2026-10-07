import { Component, type ReactNode } from 'react'
import { ErrorBox } from './ui'

/** Rendering errors show a titled message instead of a blank page; evidence is unaffected. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-3xl space-y-3 p-6">
          <ErrorBox title="This view failed to render" error={this.state.error} />
          <p className="text-sm text-muted">The evidence and analysis results are unaffected. <a href="/cases" className="font-semibold text-brand hover:underline">Back to cases</a></p>
        </div>
      )
    }
    return this.props.children
  }
}
