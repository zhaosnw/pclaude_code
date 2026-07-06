import { spawn, type ChildProcessWithoutNullStreams } from 'child_process'
import { randomUUID } from 'crypto'
import { logForDebugging } from './utils/debug.js'

type BackendEvent = Record<string, unknown>

type PythonBackendQueryContext = {
  systemPrompt?: readonly string[]
  userContext?: Record<string, string>
  systemContext?: Record<string, string>
}

type PendingRequest = {
  events: BackendEvent[]
  resolve: (events: BackendEvent[]) => void
  reject: (error: Error) => void
  pushToStream?: (event: BackendEvent) => void
  completeStream?: () => void
  failStream?: (error: Error) => void
}

class PythonBackendClient {
  private child: ChildProcessWithoutNullStreams | null = null
  private buffer = ''
  private initialized = false
  private attached = false
  private pending = new Map<string, PendingRequest>()
  private initPromise: Promise<void> | null = null

  async ensureStarted(): Promise<void> {
    if (this.initialized) {
      return
    }
    if (this.initPromise) {
      return this.initPromise
    }

    this.initPromise = new Promise<void>((resolve, reject) => {
      const cmd = process.env.HARE_PYTHON_BACKEND_CMD
      if (!cmd) {
        reject(new Error('HARE_PYTHON_BACKEND_CMD is not set'))
        return
      }
      logForDebugging(`[python-backend] starting: ${cmd}`)

      const child = spawn(cmd, {
        shell: true,
        stdio: ['pipe', 'pipe', 'pipe'],
        cwd: process.cwd(),
        env: process.env,
      })
      this.child = child
      this.attach()

      child.stderr.on('data', chunk => {
        const text = chunk.toString('utf8')
        if (text.trim()) {
          logForDebugging(`[python-backend:stderr] ${text.trim()}`)
          process.stderr.write(text)
        }
      })

      child.on('exit', code => {
        logForDebugging(`[python-backend] exited: ${code ?? 'unknown'}`)
        const error = new Error(
          `Python backend exited with code ${code ?? 'unknown'}`,
        )
        this.initialized = false
        this.initPromise = null
        this.child = null
        this.attached = false
        for (const request of this.pending.values()) {
          request.failStream?.(error)
          request.reject(error)
        }
        this.pending.clear()
        for (const listener of this.errorListeners) {
          listener(error)
        }
      })

      this.eventListeners.add(event => {
        if (event.type === 'init') {
          logForDebugging('[python-backend] init received')
          this.initialized = true
          resolve()
        }
      })
      this.errorListeners.add(reject)
    })

    return this.initPromise
  }

  private eventListeners = new Set<(event: BackendEvent) => void>()
  private errorListeners = new Set<(error: Error) => void>()

  private attach(): void {
    if (!this.child || this.attached) {
      return
    }
    this.attached = true
    this.child.stdout.on('data', chunk => {
      this.buffer += chunk.toString('utf8')
      while (true) {
        const newline = this.buffer.indexOf('\n')
        if (newline === -1) break
        const line = this.buffer.slice(0, newline).trim()
        this.buffer = this.buffer.slice(newline + 1)
        if (!line) continue
        try {
          const event = JSON.parse(line) as BackendEvent
          logForDebugging(
            `[python-backend:event] ${String(event.type ?? 'unknown')}`,
          )
          this.dispatchEvent(event)
        } catch (error) {
          const err =
            error instanceof Error ? error : new Error(String(error))
          for (const listener of this.errorListeners) {
            listener(err)
          }
        }
      }
    })
  }

  private dispatchEvent(event: BackendEvent): void {
    for (const listener of this.eventListeners) {
      listener(event)
    }
    const requestId =
      typeof event.request_id === 'string' ? event.request_id : null
    if (!requestId) {
      return
    }
    const pending = this.pending.get(requestId)
    if (!pending) {
      return
    }
    if (event.type === 'request_complete') {
      pending.completeStream?.()
      this.pending.delete(requestId)
      pending.resolve(pending.events)
      return
    }
    pending.events.push(event)
    pending.pushToStream?.(event)
  }

  async *submitPrompt(
    prompt: string,
    signal?: AbortSignal,
    queryContext?: PythonBackendQueryContext,
  ): AsyncGenerator<BackendEvent, void, void> {
    await this.ensureStarted()
    const requestId = randomUUID()
    let notify: (() => void) | null = null
    let failure: Error | null = null
    let done = false
    const queue: BackendEvent[] = []

    const result = new Promise<BackendEvent[]>((resolve, reject) => {
      this.pending.set(requestId, {
        events: [],
        resolve,
        reject,
        pushToStream: event => {
          queue.push(event)
          notify?.()
          notify = null
        },
        completeStream: () => {
          done = true
          notify?.()
          notify = null
        },
        failStream: error => {
          failure = error
          done = true
          notify?.()
          notify = null
        },
      })
    })
    logForDebugging(`[python-backend] submit_prompt ${requestId}`)
    this.send({
      type: 'submit_prompt',
      request_id: requestId,
      prompt,
      system_prompt: queryContext?.systemPrompt,
      user_context: queryContext?.userContext,
      system_context: queryContext?.systemContext,
    })
    if (signal) {
      signal.addEventListener(
        'abort',
        () => {
          logForDebugging(`[python-backend] interrupt ${requestId}`)
          this.send({ type: 'interrupt', request_id: requestId })
        },
        { once: true },
      )
    }

    while (!done || queue.length > 0) {
      if (queue.length === 0) {
        await new Promise<void>(resolve => {
          notify = resolve
        })
      }
      while (queue.length > 0) {
        yield queue.shift()!
      }
    }

    if (failure) {
      throw failure
    }

    await result
  }

  private send(message: Record<string, unknown>): void {
    if (!this.child) {
      throw new Error('Python backend is not running')
    }
    this.child.stdin.write(JSON.stringify(message) + '\n')
  }
}

const client = new PythonBackendClient()

export function submitPromptToPythonBackend(
  prompt: string,
  signal?: AbortSignal,
  queryContext?: PythonBackendQueryContext,
): AsyncGenerator<BackendEvent, void, void> {
  return client.submitPrompt(prompt, signal, queryContext)
}
