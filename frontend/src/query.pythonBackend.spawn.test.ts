import { describe, expect, it } from 'bun:test'
import { resolve } from 'path'

const { query } = await import('./query.ts')
const { createUserMessage } = await import('./utils/messages.ts')

describe('query python backend bridge spawn path', () => {
  it('streams intermediate and final output from a spawned backend', async () => {
    process.env.HARE_PYTHON_BACKEND_CMD = `bun ${JSON.stringify(
      resolve(import.meta.dir, 'test_helpers/fakePythonBackend.js'),
    )}`

    const abortController = new AbortController()
    const outputs: string[] = []

    for await (const event of query({
      messages: [createUserMessage({ content: '你是什么模型' })],
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
      if (event.type === 'stream_request_start') {
        outputs.push('stream_request_start')
      } else if (event.type === 'stream_event') {
        outputs.push(String(event.event?.delta?.text ?? ''))
      } else if (event.type === 'assistant') {
        outputs.push(String(event.message.content?.[0]?.text ?? ''))
      }
    }

    expect(outputs).toEqual(['stream_request_start', '中间过程', '最终结果'])
  })
})
