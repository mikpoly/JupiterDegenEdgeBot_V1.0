# Security

- Never commit `.env`, a private API key, seed phrase, or `wallet/*.json`.
- The included `.gitignore` excludes runtime secrets, SQLite databases, logs, model artifacts, backups, and Node/Python environments.
- Use a dedicated, low-balance wallet for optional LIVE testing.
- Transaction signing is fail-closed: every required signer other than the local wallet must already be signed in the transaction payload.
- Simulation is enabled by default for LIVE transactions.
- Prediction API behavior can change because the Jupiter Prediction API is beta.
- Review dependency updates before installing them on a machine containing private keys.

If you discover a security issue, do not publish keys, signed transactions, or sensitive logs in a public GitHub issue.
