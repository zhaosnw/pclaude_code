import { describe, expect, it, mock } from 'bun:test'

let backendStreamFactory: (
  prompt: string,
  signal?: AbortSignal,
  queryContext?: {
    systemPrompt?: readonly string[]
    userContext?: Record<string, string>
    systemContext?: Record<string, string>
  },
) => AsyncGenerator<Record<string, unknown>, void, void> = async function* () {}

let lastQueryContext:
  | {
      systemPrompt?: readonly string[]
      userContext?: Record<string, string>
      systemContext?: Record<string, string>
    }
  | undefined

mock.module('./pythonBackendClient.js', () => ({
  submitPromptToPythonBackend: (
    prompt: string,
    signal?: AbortSignal,
    queryContext?: {
      systemPrompt?: readonly string[]
      userContext?: Record<string, string>
      systemContext?: Record<string, string>
    },
  ) => {
    lastQueryContext = queryContext
    return backendStreamFactory(prompt, signal, queryContext)
  },
}))

const { query } = await import('./query.ts')
const { submitPromptToPythonBackend } = await import('./pythonBackendClient.ts')
const { createUserMessage } = await import('./utils/messages.ts')

describe('query python backend bridge', () => {
  it('exports a streaming function instead of an AsyncFunction wrapper', () => {
    expect(submitPromptToPythonBackend.constructor.name).not.toBe(
      'AsyncFunction',
    )
  })

  it('forwards stream events before the final synthesized assistant result', async () => {
    lastQueryContext = undefined
    backendStreamFactory = async function* () {
      yield {
        type: 'stream_event',
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: '目录' },
        },
      }
      yield {
        type: 'result',
        subtype: 'success',
        is_error: false,
        result: '目录分析完成',
      }
    }

    const abortController = new AbortController()
    const outputs: Array<{ type: string; [key: string]: unknown }> = []

    for await (const message of query({
      messages: [createUserMessage({ content: '分析一下整个目录' })],
      systemPrompt: ['sys-a', 'sys-b'] as never,
      userContext: { cwd: '/repo' },
      systemContext: { platform: 'darwin' },
      canUseTool: (() => {
        throw new Error('not used in python backend bridge')
      }) as never,
      toolUseContext: { abortController } as never,
      querySource: 'sdk',
      usePythonBackend: true,
    })) {
      outputs.push(message as { type: string; [key: string]: unknown })
    }

    expect(outputs.map(message => message.type)).toEqual([
      'stream_request_start',
      'stream_event',
      'assistant',
    ])
    expect(outputs[2]?.message?.content?.[0]?.text).toBe('目录分析完成')
    expect(lastQueryContext).toEqual({
      systemPrompt: ['sys-a', 'sys-b'],
      userContext: { cwd: '/repo' },
      systemContext: { platform: 'darwin' },
    })
  })

  it('emits the final result text when it differs from earlier assistant blocks', async () => {
    backendStreamFactory = async function* () {
      yield {
        type: 'assistant',
        is_error: false,
        message: {
          content: [{ type: 'text', text: '我来帮你分析整个代码目录。让我先探索一下目录结构。' }],
        },
      }
      yield {
        type: 'user',
        message: {
          content: [
            {
              type: 'tool_result',
              tool_use_id: 'toolu_1',
              content: 'Searched for 3 patterns',
              is_error: false,
            },
          ],
        },
      }
      yield {
        type: 'result',
        subtype: 'success',
        is_error: false,
        result: '这个项目是一个以 Python 为主、兼有 TypeScript 子项目的多模块代码库。',
      }
    }

    const abortController = new AbortController()
    const assistantTexts: string[] = []

    for await (const message of query({
      messages: [createUserMessage({ content: '帮我分析一下整个代码目录' })],
      systemPrompt: [] as never,
      userContext: {},
      systemContext: {},
      canUseTool: (() => {
        throw new Error('not used in python backend bridge')
      }) as never,
      toolUseContext: { abortController } as never,
      querySource: 'sdk',
      usePythonBackend: true,
    })) {
      if (message.type === 'assistant') {
        assistantTexts.push(String(message.message.content?.[0]?.text ?? ''))
      }
    }

    expect(assistantTexts).toEqual([
      '我来帮你分析整个代码目录。让我先探索一下目录结构。',
      '这个项目是一个以 Python 为主、兼有 TypeScript 子项目的多模块代码库。',
    ])
  })

})
