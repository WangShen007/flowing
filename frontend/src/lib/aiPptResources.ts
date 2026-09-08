import { buildAuthHeaders } from './auth'

export class AiPptResourceError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'AiPptResourceError'
    this.status = status
  }
}

export const safeAiPptResourceUrl = (url: string): string => {
  try {
    const target = new URL(url, window.location.origin)
    if (target.origin !== window.location.origin || !target.pathname.startsWith('/api/v1/ai-ppt/')) {
      throw new Error('AI PPT 资源地址无效')
    }
    return target.href
  } catch (error) {
    if (error instanceof AiPptResourceError) throw error
    throw new AiPptResourceError(400, 'AI PPT 资源地址无效')
  }
}

/** Fetch an AI PPT file or preview image with the current account credential. */
export const fetchAiPptBlob = async (url: string): Promise<Blob> => {
  const response = await fetch(safeAiPptResourceUrl(url), {
    headers: buildAuthHeaders(),
    cache: 'no-store'
  })
  if (!response.ok) {
    throw new AiPptResourceError(response.status, `AI PPT 资源加载失败（HTTP ${response.status}）`)
  }
  return response.blob()
}
