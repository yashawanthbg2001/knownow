import { defineConfig } from "astro/config";
import cloudflare from "@astrojs/cloudflare";

export default defineConfig({
  output: "server",
  adapter: cloudflare(),

  env: {
    schema: {
      TURSO_DATABASE_URL: {
        type: "string",
        context: "server",
        access: "secret",
      },
      TURSO_AUTH_TOKEN: {
        type: "string",
        context: "server",
        access: "secret",
      },
    },
  },
});
