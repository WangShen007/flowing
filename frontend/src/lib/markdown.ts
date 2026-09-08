import DOMPurify from 'dompurify'
import { marked } from 'marked'

// Chat content can contain both model output and data returned by Feishu.
// Keep the Markdown presentation, but do not let either source provide
// executable markup or arbitrary navigation targets.
const MARKDOWN_TAGS = [
  'a', 'b', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4',
  'h5', 'h6', 'hr', 'i', 'img', 'input', 'kbd', 'li', 'ol', 'p', 'pre',
  's', 'strong', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'u',
  'ul'
]

const MARKDOWN_ATTRIBUTES = [
  'alt', 'checked', 'disabled', 'href', 'src', 'title', 'type'
]

const SAFE_URI = /^(?:(?:https?|mailto):|\/(?!\/)|#)/i

export const renderMarkdown = (content: string): string => {
  const normalized = (content || '').replace(/\\n/g, '\n')
  const html = marked.parse(normalized) as string
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: MARKDOWN_TAGS,
    ALLOWED_ATTR: MARKDOWN_ATTRIBUTES,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: SAFE_URI,
    FORBID_TAGS: ['base', 'embed', 'form', 'iframe', 'link', 'meta', 'object', 'script', 'style', 'template'],
    FORBID_ATTR: ['style']
  })
}
