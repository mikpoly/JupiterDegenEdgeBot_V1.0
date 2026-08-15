# JupiterDegenEdgeBot V1.0 — Guide français

Première version publique du bot Jupiter Degen Edge. Elle analyse les marchés Jupiter Prediction, suit BTC/ETH/SOL/XRP/HYPE/DOGE/BNB, produit des prédictions PAPER/SHADOW, apprend sur les résultats réglés, exécute le worker TIMED 5m/15m et utilise Ollama comme garde IA locale.

**La version publique démarre volontairement en PAPER. Aucun ordre réel n'est activé par l'installation.**

## Installation complète

### Option A — PC neuf, installation tout-en-un

Ouvre PowerShell dans le dossier extrait puis lance :

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\SETUP_FROM_ZERO_WINDOWS.ps1
```

Ce script installe Python 3.13 et Node.js LTS via `winget` s'ils sont absents, installe Ollama depuis son script Windows officiel, crée l'environnement Python, installe les dépendances Python/Node, initialise SQLite et télécharge le modèle Ollama.

### Option B — Python et Node.js déjà installés

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\INSTALL.ps1
.\INSTALL_OLLAMA.ps1 -InstallIfMissing
```

La commande Windows Ollama utilisée est :

```powershell
irm https://ollama.com/install.ps1 | iex
ollama pull qwen2.5:1.5b-instruct-q4_K_M
```

### Configurer Jupiter

Ouvre le fichier de configuration :

```powershell
notepad .env
```

Remplace `JUPITER_API_KEY=PASTE_YOUR_JUPITER_API_KEY_HERE` par ta clé Jupiter Developer. Ne publie jamais `.env`.

### Vérifier l'installation

```powershell
.\VERIFY_INSTALL.ps1
.\DOCTOR.ps1
.\OLLAMA_STATUS.ps1
.\SOURCE_TEST.ps1
.\SCAN_ONCE.ps1
```

### Démarrer

```powershell
.\START_BOT.ps1
.\DASHBOARD.ps1
```

Le dashboard s'ouvre sur `http://127.0.0.1:8501`.

## Vérifier que YES/NO et UP/DOWN apprennent

```powershell
.\CHECK_ACTIVITY.ps1
.\TIMED_DIRECTION_STATUS.ps1
.\LEARNING_STATUS.ps1
```

Le worker TIMED V2.5 intégré analyse tous les actifs configurés pour l'apprentissage SHADOW/PAPER même si aucun actif n'est encore autorisé en LIVE. Le LIVE reste séparément bloqué pour les actifs qui ne passent pas les critères Brier/log-loss.

## Profils adaptatifs

Les profils sont reconstruits automatiquement à partir des prédictions réglées quand suffisamment de labels sont disponibles. Il n'est pas nécessaire d'activer manuellement un profil pour commencer l'apprentissage.

## LIVE réel

Le LIVE est optionnel et désactivé par défaut. Lis `docs/LIVE_TRADING.md` avant toute activation. Ne baisse pas les seuils statistiques uniquement pour forcer des paris. Le système continue à apprendre en PAPER/SHADOW lorsque le LIVE est bloqué.

## Tests du dépôt

```powershell
.\RUN_TESTS.ps1
```

## Fichiers à ne jamais publier

`.env`, `wallet/bot-keypair.json`, `data/*.db`, les logs, les backups et les modèles entraînés sont ignorés par Git.

Pour toutes les commandes prêtes à copier/coller, ouvre **`INSTALL_COMMANDS_WINDOWS.txt`**.


## Documentation supplémentaire

- `GET_JUPITER_API_KEY.txt` : configuration de la clé Jupiter
- `docs/CONFIGURATION.md` : configuration
- `docs/OLLAMA.md` : installation/modèle Ollama
- `docs/ARCHITECTURE.md` : architecture du bot
- `docs/TROUBLESHOOTING.md` : dépannage
- `docs/LIVE_TRADING.md` : LIVE optionnel
