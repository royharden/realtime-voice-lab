import {defineConfig} from 'vite';
import {readFileSync} from 'node:fs';

// Explicit allowlist: predecessor backup records remain on disk and cannot ship.
const worklet = new URL('./public/audio-worklet.js', import.meta.url);
export default defineConfig(({command, isPreview}) => {
  const serving = command === 'serve' || isPreview;
  const port = Number(process.env.VOICELAB_WEB_PORT);
  if (serving && (!Number.isInteger(port) || port < 22300 || port > 22399)) {
    throw new Error('VOICELAB_WEB_PORT must be a claimed M6 loopback port (22300–22399).');
  }
  return {
    publicDir: false,
    // Retain earlier artifacts: project policy forbids deleting predecessor files.
    build: {emptyOutDir: false},
    plugins: [{
      name: 'voice-lab-worklet-allowlist',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (req.url?.split('?')[0] !== '/audio-worklet.js') return next();
          res.setHeader('Content-Type', 'text/javascript');
          res.end(readFileSync(worklet));
        });
      },
      generateBundle() {
        this.emitFile({type: 'asset', fileName: 'audio-worklet.js', source: readFileSync(worklet)});
      },
    }],
    server: {host: '127.0.0.1', port: serving ? port : undefined, strictPort: true},
    preview: {host: '127.0.0.1', port: serving ? port : undefined, strictPort: true},
  };
});
