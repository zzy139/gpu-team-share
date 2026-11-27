import {HTTPMethod} from '../http/executeRequest'
import {ILogger} from './ILogger'

enum LogLevel {
  info = 'INFO',
  warning = 'WARNING',
  debug = 'DEBUG',
  error = 'ERROR',
  critical = 'CRITICAL',
}

interface LogEntry {
  message: Error | string
  level: LogLevel
  extra?: Record<string, unknown>
}

type BufferConfig = {
  size: number
  window: number
}

const DEFAULT_MESSAGE = 'This log entry has no message.'
// Upper bound on entries kept for retry after a failed flush, as a multiple of the buffer size.
const MAX_RETAINED_BUFFERS = 5

export class Logger implements ILogger {
  private readonly executeRequest
  private logsBuffer: LogEntry[] = []
  private readonly bufferConfig: BufferConfig
  private timeout: NodeJS.Timeout | undefined
  // Set when the backend refuses /logs (401/403, e.g. team-lead sessions): stop sending for this page.
  private disabled = false

  constructor(
    executeRequest: (
      method: HTTPMethod,
      url: string,
      body?: any,
    ) => Promise<any>,
    bufferConfig: BufferConfig = {
      size: 20,
      window: 5 * 1000,
    },
  ) {
    this.executeRequest = executeRequest
    this.bufferConfig = bufferConfig
  }

  private log(
    logLevel: LogLevel,
    message: Error | string,
    extra: Record<string, unknown> = {},
    source?: string,
  ) {
    if (this.disabled) return
    extra['source'] = source || 'frontend'

    const logEntry = this.buildMessage(logLevel, message, extra)
    this.logsBuffer.push(logEntry)
    if (this.logsBuffer.length >= this.bufferConfig.size) {
      this.flushBuffer()
    } else if (!this.timeout) {
      this.timeout = setTimeout(
        () => this.flushBuffer(),
        this.bufferConfig.window,
      )
    }
  }

  private async flushBuffer() {
    clearTimeout(this.timeout)
    this.timeout = undefined

    const entries = [...this.logsBuffer]
    this.logsBuffer = []
    try {
      await this.executeRequest('post', '/logs', {logs: entries})
    } catch (error: any) {
      const status = error?.response?.status
      if (status === 401 || status === 403) {
        // Not allowed to push logs: drop everything and stop, otherwise every flush re-queues and retries.
        this.disabled = true
        this.logsBuffer = []
        clearTimeout(this.timeout)
        this.timeout = undefined
        return
      }
      // Retry failed logs when logging is called again, keeping only the most recent entries
      const maxRetained = this.bufferConfig.size * MAX_RETAINED_BUFFERS
      this.logsBuffer = [...entries, ...this.logsBuffer].slice(-maxRetained)
    }
  }

  info(message: string, extra?: Record<string, unknown>, source?: string) {
    this.log(LogLevel.info, message, extra, source)
  }

  warning(message: string, extra?: Record<string, unknown>, source?: string) {
    this.log(LogLevel.warning, message, extra, source)
  }

  debug(message: string, extra?: Record<string, unknown>, source?: string) {
    this.log(LogLevel.debug, message, extra, source)
  }

  error(
    message: Error | string,
    extra?: Record<string, unknown>,
    source?: string,
  ) {
    this.log(LogLevel.error, message, extra, source)
  }

  critical(
    message: Error | string,
    extra?: Record<string, unknown>,
    source?: string,
  ) {
    this.log(LogLevel.critical, message, extra, source)
  }

  private buildMessage(
    level: LogLevel,
    payload: Error | string,
    extra?: Record<string, unknown>,
  ): LogEntry {
    if (!payload) {
      return {message: DEFAULT_MESSAGE, level, extra}
    }

    if (payload instanceof Error) {
      extra = {
        ...extra,
        stackTrace: payload.stack,
      }

      return {message: payload.message, level, extra}
    }

    if (typeof payload !== 'string') {
      const message = JSON.stringify(payload)
      return {message, level, extra}
    }

    return {message: payload, level, extra}
  }
}
