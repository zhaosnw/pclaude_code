console.log(JSON.stringify({ type: 'init' }))

let buffer = ''
process.stdin.setEncoding('utf8')

process.stdin.on('data', chunk => {
  buffer += chunk
  let newlineIndex
  while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, newlineIndex).trim()
    buffer = buffer.slice(newlineIndex + 1)
    if (!line) continue

    const message = JSON.parse(line)
    const requestId = message.request_id
    if (message.type !== 'submit_prompt') continue

    console.log(
      JSON.stringify({
        type: 'stream_event',
        request_id: requestId,
        event: {
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: '中间过程' },
        },
      }),
    )
    console.log(
      JSON.stringify({
        type: 'result',
        request_id: requestId,
        subtype: 'success',
        is_error: false,
        result: '最终结果',
      }),
    )
    console.log(
      JSON.stringify({
        type: 'request_complete',
        request_id: requestId,
      }),
    )
  }
})
