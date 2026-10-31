import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// examples/ and schemas/ live one level up, in the repo root.
export default defineConfig({
  plugins: [react()],
  server: { fs: { allow: ['..'] } },
})
