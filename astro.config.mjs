import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  // This helps Astro understand the environment
  env: {
    schema: {
      TURSO_DATABASE_URL: { type: 'string', context: 'server', access: 'secret' },
      TURSO_AUTH_TOKEN: { type: 'string', context: 'server', access: 'secret' },
    }
  }
});