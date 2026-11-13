import { defineConfig, type Plugin, type ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";

// Dev only: serve api/public/*.ts with the same Web Request/Response handlers Vercel runs.
function devApi(): Plugin {
  return {
    name: "dev-api",
    apply: "serve",
    configureServer(server: ViteDevServer) {
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        const url = new URL(req.url ?? "/", "http://localhost");
        const m = url.pathname.match(/^\/api\/public\/(aai-(?:chat|tts|stt-token|stt))$/);
        if (!m) return next();
        try {
          const mod = await server.ssrLoadModule(`/api/public/${m[1]}.ts`);
          const chunks: Buffer[] = [];
          for await (const c of req) chunks.push(c as Buffer);
          const request = new Request(url, {
            method: req.method,
            headers: req.headers as Record<string, string>,
            body: req.method === "GET" || req.method === "HEAD" ? undefined : Buffer.concat(chunks),
          });
          const handler = mod[req.method ?? "GET"];
          const response: Response = handler ? await handler(request) : new Response("Method not allowed", { status: 405 });
          res.statusCode = response.status;
          response.headers.forEach((v, k) => res.setHeader(k, v));
          res.end(Buffer.from(await response.arrayBuffer()));
        } catch (e) {
          res.statusCode = 500;
          res.end(String(e));
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), devApi()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  optimizeDeps: { include: ["pdfjs-dist", "mammoth/mammoth.browser"] },
});
