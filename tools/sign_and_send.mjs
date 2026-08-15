import fs from 'node:fs';
import process from 'node:process';
import bs58 from 'bs58';
import { Connection, Keypair, VersionedTransaction } from '@solana/web3.js';
import { validateSignerPlan } from './signer_policy.mjs';

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
  return secret.length === 32 ? Keypair.fromSeed(Uint8Array.from(secret)) : Keypair.fromSecretKey(Uint8Array.from(secret));
}
async function readInput() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
function requiredSignerKeys(tx) {
  const count = Number(tx.message.header?.numRequiredSignatures || 0);
  return tx.message.staticAccountKeys.slice(0, count).map((key) => key.toBase58());
}
async function signatureState(connection, signature) {
  try {
    const result = await connection.getSignatureStatuses([signature], { searchTransactionHistory: true });
    return result?.value?.[0] || null;
  } catch { return null; }
}
async function main() {
  const payload = await readInput();
  const keypair = loadKeypair(payload.walletPath);
  const tx = VersionedTransaction.deserialize(Buffer.from(payload.transaction, 'base64'));
  const required = requiredSignerKeys(tx);
  const owner = keypair.publicKey.toBase58();
  const signaturePresent = (signature) =>
    signature instanceof Uint8Array && signature.length === 64 && signature.some((byte) => byte !== 0);
  const signedBefore = tx.signatures.slice(0, required.length).map(signaturePresent);
  const signerPlan = validateSignerPlan(required, signedBefore, owner);
  const signaturesBefore = tx.signatures.map((signature) => Uint8Array.from(signature));
  tx.sign([keypair]);
  if (!signaturePresent(tx.signatures[signerPlan.ownerSignerIndex])) {
    throw new Error(`Local wallet signature was not applied at signer index ${signerPlan.ownerSignerIndex}`);
  }
  for (let index = 0; index < required.length; index += 1) {
    if (index === signerPlan.ownerSignerIndex) continue;
    if (!signaturePresent(tx.signatures[index])) throw new Error(`Required co-signer ${required[index]} has no signature after local signing`);
    if (!Buffer.from(tx.signatures[index]).equals(Buffer.from(signaturesBefore[index]))) {
      throw new Error(`Pre-applied signature for co-signer ${required[index]} changed during local signing`);
    }
  }
  const signerInfo = { owner, feePayer: signerPlan.feePayer, ownerSignerIndex: signerPlan.ownerSignerIndex,
    requiredSigners: signerPlan.requiredSigners, sponsoredFeePayer: signerPlan.sponsoredFeePayer };
  const expectedSignature = bs58.encode(tx.signatures[0]);
  const rpcUrls = Array.isArray(payload.rpcUrls) ? payload.rpcUrls.filter(Boolean) : [];
  if (!rpcUrls.length) throw new Error('No Solana RPC URL configured');
  const commitment = payload.commitment || 'confirmed';
  const errors = [];
  for (const rpcUrl of rpcUrls) {
    const connection = new Connection(rpcUrl, commitment);
    try {
      if (payload.simulate !== false) {
        const simulation = await connection.simulateTransaction(tx, { sigVerify: true, replaceRecentBlockhash: false, commitment: 'processed' });
        if (simulation?.value?.err) {
          process.stdout.write(JSON.stringify({ ok:false, ambiguous:false, signature:expectedSignature, rpcUrl,
            simulationError:simulation.value.err, logs:simulation.value.logs || [],
            error:`Simulation failed: ${JSON.stringify(simulation.value.err)}`, signerInfo }));
          process.exitCode=1; return;
        }
      }
      const signature = await connection.sendRawTransaction(tx.serialize(), { skipPreflight:false, maxRetries:3, preflightCommitment:'confirmed' });
      const meta = payload.txMeta || {};
      let confirmation;
      try {
        if (meta.blockhash && meta.lastValidBlockHeight) {
          confirmation = await connection.confirmTransaction({signature, blockhash:meta.blockhash, lastValidBlockHeight:Number(meta.lastValidBlockHeight)}, commitment);
        } else confirmation = await connection.confirmTransaction(signature, commitment);
      } catch (error) {
        const state = await signatureState(connection, signature);
        if (state && !state.err && ['confirmed','finalized'].includes(state.confirmationStatus)) {
          process.stdout.write(JSON.stringify({ok:true,signature,rpcUrl,recoveredAfterConfirmError:true,status:state,signerInfo})); return;
        }
        process.stdout.write(JSON.stringify({ok:false,ambiguous:true,signature,rpcUrl,status:state,error:`Transaction sent but confirmation is uncertain: ${String(error?.message||error)}`,signerInfo}));
        process.exitCode=2; return;
      }
      if (confirmation?.value?.err) {
        process.stdout.write(JSON.stringify({ok:false,ambiguous:false,signature,rpcUrl,error:`Transaction confirmation failed: ${JSON.stringify(confirmation.value.err)}`,signerInfo}));
        process.exitCode=1; return;
      }
      process.stdout.write(JSON.stringify({ok:true,signature,rpcUrl,confirmation,signerInfo})); return;
    } catch (error) {
      const state = await signatureState(connection, expectedSignature);
      if (state && !state.err && ['confirmed','finalized'].includes(state.confirmationStatus)) {
        process.stdout.write(JSON.stringify({ok:true,signature:expectedSignature,rpcUrl,recoveredAfterSendError:true,status:state,signerInfo})); return;
      }
      errors.push(`${rpcUrl}: ${String(error?.message||error)}`);
      if (state || String(error?.message||error).toLowerCase().includes('already processed')) {
        process.stdout.write(JSON.stringify({ok:false,ambiguous:true,signature:expectedSignature,rpcUrl,status:state,error:errors.join(' | '),signerInfo}));
        process.exitCode=2; return;
      }
    }
  }
  process.stdout.write(JSON.stringify({ok:false,ambiguous:false,signature:expectedSignature,error:errors.join(' | '),signerInfo}));
  process.exitCode=1;
}
main().catch((error)=>{ process.stdout.write(JSON.stringify({ok:false,ambiguous:false,error:String(error?.stack||error)})); process.exitCode=1; });
