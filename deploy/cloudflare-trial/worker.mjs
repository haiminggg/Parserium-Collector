import { Container } from '@cloudflare/containers';
import { allowed } from './access.mjs';

export class ParsingTrial extends Container {
  defaultPort = 8080;
  sleepAfter = '20s';
  enableInternet = false;

  async runOnce() {
    if (await this.ctx.storage.get('attempted')) {
      return new Response('Trial already consumed', {status: 409});
    }
    await this.startAndWaitForPorts();
    const claimed = await this.ctx.storage.transaction(async txn => {
      if (await txn.get('attempted')) return false;
      await txn.put('attempted', true);
      return true;
    });
    if (!claimed) return new Response('Trial already consumed', {status: 409});
    try {
      const response = await this.containerFetch('http://localhost/run', {method: 'POST'});
      const body = await response.text();
      return new Response(body, {status: response.status, headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}});
    } finally {
      await this.stop();
    }
  }
}

export default {
  async fetch(request, env) {
    if (!allowed(request, env.TRIAL_TOKEN)) return new Response('Not found', {status: 404});
    return env.TRIAL.getByName('single-fixture-trial').runOnce();
  }
};
