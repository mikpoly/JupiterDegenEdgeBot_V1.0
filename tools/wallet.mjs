import fs from 'node:fs';
import process from 'node:process';
import { Keypair } from '@solana/web3.js';
import bs58 from 'bs58';

function loadKeypair(path) {
  const raw = fs.readFileSync(path, 'utf8').trim();
  if (!raw) throw new Error('Wallet file is empty');
  let secret;
  if (raw.startsWith('[')) secret = JSON.parse(raw);
  else {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) secret = parsed;
    else if (Array.isArray(parsed.secretKey)) secret = parsed.secretKey;
    else if (typeof parsed.privateKey === 'string') secret = [...bs58.decode(parsed.privateKey)];
  }
  if (!Array.isArray(secret) || ![32, 64].includes(secret.length)) {
    throw new Error('Unsupported wallet JSON format or invalid secret length');
  }
  return secret.length === 32
    ? Keypair.fromSeed(Uint8Array.from(secret))
    : Keypair.fromSecretKey(Uint8Array.from(secret));
}

const command = process.argv[2] || 'pubkey';
const path = process.env.SOLANA_KEYPAIR_PATH || 'wallet/bot-keypair.json';
const keypair = loadKeypair(path);
if (command === 'pubkey') {
  process.stdout.write(keypair.publicKey.toBase58());
} else {
  throw new Error(`Unknown command: ${command}`);
}
