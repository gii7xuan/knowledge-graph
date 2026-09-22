import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': `http://127.0.0.1:${process.env.WORKBENCH_PORT || 8770}`
    }
  }
});
