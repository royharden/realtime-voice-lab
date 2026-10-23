import {defineConfig} from 'vite';

// Set only after agent-ports claim/check at later authorized runtime activation.
const port = Number(process.env.VOICELAB_WEB_PORT);
if (!Number.isInteger(port) || port < 20000 || port > 29999) {
  throw new Error('VOICELAB_WEB_PORT must be an operator-supplied registered port (20000–29999).');
}
export default defineConfig({server:{host:'127.0.0.1',port,strictPort:true},preview:{host:'127.0.0.1',port,strictPort:true}});
