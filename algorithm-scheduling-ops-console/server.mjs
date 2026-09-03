import { createReadStream, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'node:http'

const root = join(fileURLToPath(new URL('.', import.meta.url)), 'dist')
const types = { '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.html': 'text/html; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml' }

createServer((request, response) => {
  const requested = normalize(decodeURIComponent(request.url?.split('?')[0] || '/')).replace(/^\/+/, '')
  if (requested.startsWith('..')) { response.statusCode = 400; response.end('Bad Request'); return }
  const candidate = join(root, requested)
  let file = join(root, 'index.html')
  try { if (requested && statSync(candidate).isFile()) file = candidate } catch { /* SPA fallback */ }
  response.setHeader('Cache-Control', file.endsWith('index.html') ? 'no-store' : 'public, max-age=31536000, immutable')
  response.setHeader('Content-Type', types[extname(file)] || 'application/octet-stream')
  createReadStream(file).on('error', () => { response.statusCode = 404; response.end('Not Found') }).pipe(response)
}).listen(Number(process.env.PORT || 80), '0.0.0.0')
