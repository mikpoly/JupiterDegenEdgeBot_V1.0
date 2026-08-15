/**
 * Validate which signer is local and whether every other required signer was
 * already signed by the transaction provider. Jupiter Prediction may return a
 * sponsored transaction where a relayer is the fee payer and the user's wallet
 * is another required signer. Missing required signatures are fail-closed.
 */
export function validateSignerPlan(requiredSigners, signedBefore, owner) {
  if (!Array.isArray(requiredSigners) || !requiredSigners.length) {
    throw new Error('Transaction has no required signer');
  }
  if (!Array.isArray(signedBefore) || signedBefore.length !== requiredSigners.length) {
    throw new Error('Signer/signature metadata length mismatch');
  }
  const ownerSignerIndex = requiredSigners.indexOf(owner);
  if (ownerSignerIndex < 0) {
    throw new Error(`Local wallet ${owner} is not a required signer of the Jupiter transaction`);
  }
  const missingCosigners = [];
  for (let index = 0; index < requiredSigners.length; index += 1) {
    if (index === ownerSignerIndex) continue;
    if (!signedBefore[index]) missingCosigners.push(requiredSigners[index]);
  }
  if (missingCosigners.length) {
    throw new Error(
      'Jupiter transaction requires additional signer(s) without a pre-applied signature: ' +
      missingCosigners.join(', ')
    );
  }
  return {
    ownerSignerIndex,
    feePayer: requiredSigners[0],
    sponsoredFeePayer: ownerSignerIndex !== 0,
    requiredSigners: [...requiredSigners],
  };
}
