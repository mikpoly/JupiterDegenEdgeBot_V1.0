# JupiterDegenEdgeBot V1.0

<p align="center">
  <img src="1.bmp" width="900" alt="JupiterDegenEdgeBot Dashboard">
</p>

<p align="center">
  <strong>Quantitative research, adaptive learning and automated analysis for Jupiter Prediction Markets.</strong>
</p>

---

## Overview

**JupiterDegenEdgeBot V1.0** is a Windows-first quantitative research and prediction-market bot designed for the **Jupiter Prediction ecosystem on Solana**.

The project combines:

- Jupiter Prediction market discovery
- Multi-source cryptocurrency market data
- Quantitative probability estimation
- PAPER and SHADOW learning
- 5-minute / 15-minute UP/DOWN market analysis
- Adaptive model profiles
- Neural-model research and validation
- Brier score and log-loss calibration
- Local AI-assisted review through Ollama
- SQLite persistence
- A real-time Streamlit monitoring dashboard
- Optional protected LIVE execution

The public release is designed to start safely in **PAPER mode** and learn from market observations before real-money execution is enabled.

> **Important:** JupiterDegenEdgeBot is an experimental research project.  
> It does not guarantee profitability and should not be considered financial advice.

---

# Main Features

## 1. Supported crypto assets

The current release monitors and models:

- **BTC** — Bitcoin
- **ETH** — Ethereum
- **SOL** — Solana
- **XRP** — XRP
- **HYPE** — Hyperliquid
- **DOGE** — Dogecoin
- **BNB** — BNB

Each asset can maintain independent statistics, learning profiles and calibration state.

---

## 2. Jupiter Prediction market analysis

The bot discovers and analyzes Jupiter Prediction markets and evaluates opportunities using quantitative probability estimates.

It supports standard prediction-market structures such as:

- YES / NO markets
- Crypto price targets
- Crypto price ranges
- Directional markets
- Short-duration prediction events

The engine compares:

```text
Model probability
        vs
Jupiter market probability
        ↓
Estimated statistical edge
```

A prediction does not automatically become a trade.

Additional filters and risk controls are applied before PAPER or LIVE execution.

---

## 3. TIMED Direction V2 — 5m / 15m UP-DOWN research

JupiterDegenEdgeBot includes a dedicated short-horizon engine for directional crypto markets.

The TIMED Direction V2 subsystem continuously evaluates:

```text
BTC
ETH
SOL
XRP
HYPE
DOGE
BNB
```

for short-duration **UP / DOWN** prediction markets.

The TIMED worker operates independently from the slower general prediction-market cycle.

Typical workflow:

```text
Jupiter 5m / 15m market
        ↓
Crypto market snapshot
        ↓
Quantitative feature extraction
        ↓
TIMED probability model
        ↓
SHADOW prediction
        ↓
Market expiration
        ↓
Result resolution
        ↓
Brier score / Log-loss update
        ↓
Calibration improvement
```

### Continuous learning protection

The V1.0 TIMED worker continues collecting SHADOW/PAPER observations even when an asset is currently:

```text
live_ready = False
```

This is important because LIVE eligibility must **not stop model learning**.

The architecture therefore separates:

```text
LEARNING
    ↓
Always allowed for configured assets

LIVE EXECUTION
    ↓
Only allowed after calibration and risk gates pass
```

This prevents a calibration deadlock where a model could stop receiving new labels simply because it temporarily failed a LIVE threshold.

---

## 4. PAPER and SHADOW learning

The bot contains multiple research layers.

### SHADOW predictions

SHADOW predictions allow the models to record what they would have predicted without sending an order.

They are used to measure:

- Probability calibration
- Brier score
- Log-loss
- Prediction accuracy
- Market-relative behavior
- Performance through time

### PAPER positions

PAPER mode simulates actual positions without risking real funds.

The default public configuration is:

```env
TRADING_MODE=paper
AUTO_EXECUTE=false
LIVE_RELEASE_ENABLED=false
TIMED_DIRECTION_LIVE_ENABLED=false
```

This allows a new installation to build its own learning history before LIVE trading is considered.

---

# Adaptive Learning

JupiterDegenEdgeBot contains an adaptive research layer.

The system can maintain multiple active profiles across:

- Assets
- Market types
- Prediction horizons
- Calibration conditions
- Feature regimes
- Model configurations

The dashboard displays the current number of active adaptive profiles.

For example:

```text
ADAPTIVE PHASE: ACTIVE
ACTIVE PROFILES: 14
```

No manual intervention is normally required.

The bot manages these profiles automatically according to the available observations and validation results.

---

# Neural Research

The project also contains bounded neural-model research functionality.

Neural models are not automatically trusted simply because they obtain good training results.

They can be subjected to:

- Training validation
- Out-of-sample evaluation
- Walk-forward validation
- Calibration testing
- Brier score comparison
- Log-loss comparison
- Baseline comparison
- Promotion / rejection rules

A model can therefore remain inactive if its validation performance does not improve sufficiently over the baseline.

This is intentional.

---

# Probability Calibration

Two important metrics are used by the project:

## Brier Score

The Brier score measures the quality of probabilistic predictions.

Lower values are generally better.

Example:

```text
Predicted probability: 0.70
Actual result:          1
```

The model is evaluated on the distance between its probability estimate and the final outcome.

---

## Log-Loss

Log-loss penalizes highly confident incorrect predictions more strongly.

This helps prevent a model from becoming overconfident.

---

## LIVE calibration gates

The TIMED system can require statistical criteria such as:

```text
Maximum Brier score
Maximum log-loss
Minimum resolved events
```

before an asset becomes:

```text
live_ready = True
```

These gates are intentionally separate from SHADOW learning.

---

# Multi-Source Crypto Data

The quantitative engine can combine crypto-market observations from several sources when available.

The objective is to avoid relying on a single exchange or single market feed.

The engine can derive features from:

- Spot prices
- Price changes
- Short-term momentum
- Cross-exchange observations
- Market dispersion
- Directional movement
- Volatility information
- Time-to-expiration
- Prediction-market prices
- Market liquidity information

The exact feature set may evolve with future releases.

---

# Local AI Review with Ollama

JupiterDegenEdgeBot can use a local Ollama model as an additional research/review component.

Default model:

```text
qwen2.5:1.5b-instruct-q4_K_M
```

The AI reviewer is **not the primary trading engine**.

Quantitative rules, probabilities and risk controls remain authoritative.

Ollama can be used to assist with contextual review and research without requiring a cloud-hosted language model.

---

# Dashboard

The project includes a local **Streamlit dashboard**.

Default address:

```text
http://127.0.0.1:8501
```

The dashboard can display information such as:

- Wallet / capital state
- LIVE exposure
- PAPER exposure
- Realized and unrealized results
- Claimable positions
- Current crypto prices
- Prediction-market activity
- Current engine decision
- Probability estimates
- Confidence
- Estimated edge
- Reliability
- Current cycle
- Signals
- PAPER orders
- Adaptive-learning status
- Active model profiles
- Database memory usage
- Research / validation information
- LIVE state
- System state

Start it with:

```powershell
.\DASHBOARD.ps1
```

---

# Architecture

A simplified architecture looks like this:

```text
                 ┌──────────────────────┐
                 │ Jupiter Prediction   │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ Market Discovery     │
                 └──────────┬───────────┘
                            │
        ┌───────────────────┴───────────────────┐
        │                                       │
        ▼                                       ▼
┌─────────────────┐                    ┌─────────────────┐
│ General Markets │                    │ TIMED 5m / 15m │
│ YES / NO        │                    │ UP / DOWN       │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         ▼                                      ▼
┌─────────────────┐                    ┌─────────────────┐
│ Quant Analysis  │                    │ FAST TIMED      │
│ + Features      │                    │ Quant Analysis  │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         └───────────────────┬──────────────────┘
                             ▼
                  ┌──────────────────────┐
                  │ Probability Models   │
                  └──────────┬───────────┘
                             ▼
                  ┌──────────────────────┐
                  │ SHADOW / PAPER       │
                  │ Learning             │
                  └──────────┬───────────┘
                             ▼
                  ┌──────────────────────┐
                  │ Calibration          │
                  │ Brier / Log-loss     │
                  └──────────┬───────────┘
                             ▼
                  ┌──────────────────────┐
                  │ LIVE Safety Gates    │
                  └──────────┬───────────┘
                             ▼
                  ┌──────────────────────┐
                  │ Optional LIVE Trade  │
                  └──────────────────────┘
```

---

# Windows Installation

## Requirements

Recommended environment:

- Windows 10 or Windows 11
- Python 3.13 recommended
- Python 3.11–3.13 supported by this release
- Node.js LTS
- npm
- PowerShell
- Ollama
- Jupiter Prediction API key

---

## Fresh Windows installation

Open PowerShell inside the project directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\SETUP_FROM_ZERO_WINDOWS.ps1
```

Then configure:

```powershell
notepad .env
```

---

## Existing Python / Node.js installation

If Python and Node.js are already available:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\INSTALL.ps1
.\INSTALL_OLLAMA.ps1 -InstallIfMissing
```

Then:

```powershell
notepad .env
```

---

# Jupiter API Key

A Jupiter API key is required for the relevant Prediction API functionality.

Configure:

```env
JUPITER_API_KEY=PASTE_YOUR_JUPITER_API_KEY_HERE
```

The Prediction API uses the:

```text
x-api-key
```

header.

Jupiter Prediction APIs may evolve over time, so future API changes may require updates to the bot.

---

# Ollama Installation

Install Ollama on Windows:

```powershell
irm https://ollama.com/install.ps1 | iex
```

Pull the default model:

```powershell
ollama pull qwen2.5:1.5b-instruct-q4_K_M
```

Verify:

```powershell
.\OLLAMA_STATUS.ps1
```

The model can be changed using:

```env
OLLAMA_MODEL=
```

See:

```text
docs/OLLAMA.md
```

for additional information.

---

# Validate the Installation

Before starting the bot:

```powershell
.\DOCTOR.ps1
.\OLLAMA_STATUS.ps1
.\SOURCE_TEST.ps1
.\SCAN_ONCE.ps1
```

Then start:

```powershell
.\START_BOT.ps1
```

Start the dashboard:

```powershell
.\DASHBOARD.ps1
```

Dashboard:

```text
http://127.0.0.1:8501
```

---

# First Run

The public release intentionally contains:

- No personal `.env`
- No private wallet
- No user SQLite database
- No user-trained models
- No logs
- No private backups

A fresh installation creates its own local data.

The bot starts learning from scratch using:

```text
Market observations
        ↓
SHADOW predictions
        ↓
PAPER exploration
        ↓
Resolved events
        ↓
Calibration
        ↓
Adaptive learning
```

Do not expect a fresh installation to reproduce the statistics of another installation immediately.

---

# Useful Commands

Check bot activity:

```powershell
.\CHECK_ACTIVITY.ps1
```

Check TIMED Direction:

```powershell
.\TIMED_DIRECTION_STATUS.ps1
```

Check adaptive learning:

```powershell
.\LEARNING_STATUS.ps1
```

Check LIVE state:

```powershell
.\LIVE_STATUS.ps1
```

Stop the trading engine:

```powershell
.\STOP_BOT.ps1
```

Stop the dashboard:

```powershell
.\STOP_DASHBOARD.ps1
```

Run tests:

```powershell
.\RUN_TESTS.ps1
```

---

# LIVE Trading

LIVE trading is optional and intentionally disabled by default.

Public defaults:

```env
TRADING_MODE=paper
AUTO_EXECUTE=false
LIVE_RELEASE_ENABLED=false
TIMED_DIRECTION_LIVE_ENABLED=false
```

Do not enable LIVE simply to generate more trades.

LIVE execution may depend on several independent controls such as:

- Model calibration
- Minimum sample size
- Brier score
- Log-loss
- Estimated edge
- Market price
- Price drift
- Spread
- Available balance
- Maximum stake
- Maximum exposure
- Positions already open
- Asset exposure
- Event exposure
- Transaction simulation
- Signer validation
- Jupiter API availability

Failure of a safety condition should normally result in:

```text
NO LIVE TRADE
```

rather than bypassing the protection.

Read:

```text
docs/LIVE_TRADING.md
```

before enabling LIVE execution.

---

# Solana / Jupiter Transaction Safety

Optional LIVE execution uses Solana transactions generated through the relevant Jupiter infrastructure.

The project uses fail-closed transaction validation.

A transaction requiring an unexpected signer should not be blindly submitted.

Private keys and wallet files must remain local.

Never publish:

```text
.env
wallet/*.json
private keys
seed phrases
API secrets
SQLite databases containing personal trading history
```

---

# Position Reconciliation and Claims

The lifecycle subsystem can monitor known positions and reconcile their state with Jupiter.

It can also detect potentially claimable positions.

Claim execution remains subject to:

- Jupiter API response
- Transaction validity
- Required signatures
- Wallet permissions
- Network availability
- Solana transaction simulation / submission conditions

The bot should never fabricate or bypass a required transaction signature.

---

# Repository Security

The included `.gitignore` is designed to prevent accidental publication of sensitive runtime data.

Do not commit:

```text
.env
wallet JSON files
SQLite databases
logs
backups
temporary exports
private model artifacts
API keys
private keys
```

See:

```text
SECURITY.md
```

---

# Development and Testing

Run the project test suite:

```powershell
.\RUN_TESTS.ps1
```

The project contains validation and safety tests for several parts of the trading and learning architecture.

Changes involving LIVE execution should always be tested in PAPER mode first.

---

# Documentation

Additional documentation is available in:

- `docs/CONFIGURATION.md` — configuration and safe defaults
- `docs/OLLAMA.md` — local AI setup
- `docs/ARCHITECTURE.md` — project architecture
- `docs/TROUBLESHOOTING.md` — Windows, API and Ollama troubleshooting
- `docs/LIVE_TRADING.md` — optional real-money execution
- `GET_JUPITER_API_KEY.txt` — Jupiter API-key configuration
- `INSTALL_COMMANDS_WINDOWS.txt` — Windows copy/paste installation commands
- `README_FR.md` — French documentation

---

# Ecosystem

JupiterDegenEdgeBot is designed around technologies from the:

- **Solana ecosystem**
- **Jupiter Developer Platform**
- **Jupiter Prediction ecosystem**

The project is independent and community-developed.

> **JupiterDegenEdgeBot is not an official Jupiter, Solana Labs or Solana Foundation product and does not imply endorsement, partnership or sponsorship by those organizations.**

---

# Project Goals

The main objective of JupiterDegenEdgeBot is not to maximize the number of trades.

Its objective is to provide an experimental framework for:

- Prediction-market research
- Quantitative probability modelling
- Crypto-market feature engineering
- Probability calibration
- Short-horizon market research
- Adaptive learning
- Neural-model validation
- PAPER experimentation
- Risk-controlled automated execution
- Local monitoring and diagnostics

A valid decision can therefore be:

```text
NO TRADE
```

when available opportunities do not satisfy the model and risk requirements.

---

# Disclaimer

This software is experimental.

Cryptocurrency markets and prediction markets involve substantial financial risk.

The software may:

- Make incorrect predictions
- Lose money
- Experience API failures
- Experience network failures
- Encounter market changes
- Encounter incompatible Jupiter API changes
- Experience software bugs
- Produce inaccurate model estimates

You are solely responsible for how you use the software.

Nothing in this repository constitutes:

- Financial advice
- Investment advice
- Trading advice
- A guarantee of future performance

Past PAPER or LIVE performance does not guarantee future results.

See:

```text
DISCLAIMER.md
```

---

# License

No open-source license has currently been selected for JupiterDegenEdgeBot V1.0.

Until a license is explicitly added, standard copyright rules apply.

If you intend to allow redistribution, modification or commercial use, add an appropriate license to the repository.

---

# Contributing

Contributions, bug reports and technical discussions are welcome.

Useful contributions may include:

- Jupiter Prediction API compatibility improvements
- Quantitative research
- Calibration research
- Additional tests
- Dashboard improvements
- Documentation
- Solana transaction safety
- Data-source reliability
- Performance optimization

When modifying trading logic, preserve the separation between:

```text
Research / SHADOW
PAPER
LIVE
```

and do not bypass LIVE safety gates merely to increase trade frequency.

---

# Status

**Version:** V1.0  
**Platform:** Windows-first  
**Language:** Python / PowerShell / JavaScript  
**Database:** SQLite  
**Dashboard:** Streamlit  
**Local AI:** Ollama  
**Blockchain ecosystem:** Solana  
**Prediction platform:** Jupiter Prediction  

---

# Français

## Présentation

**JupiterDegenEdgeBot V1.0** est un bot expérimental de recherche quantitative et d'analyse des marchés de prédiction Jupiter, conçu principalement pour Windows.

Il combine :

- Analyse des marchés Jupiter Prediction
- Données crypto provenant de plusieurs sources
- Estimation quantitative des probabilités
- Apprentissage SHADOW
- Positions PAPER
- Analyse directionnelle UP/DOWN 5 minutes et 15 minutes
- Profils adaptatifs
- Recherche avec modèles neuronaux
- Validation statistique
- Score de Brier
- Log-loss
- Analyse locale avec Ollama
- Base de données SQLite
- Dashboard Streamlit
- Exécution LIVE optionnelle avec protections

---

## Cryptomonnaies prises en charge

Le bot analyse actuellement :

- BTC
- ETH
- SOL
- XRP
- HYPE
- DOGE
- BNB

Chaque actif peut avoir ses propres observations, statistiques, modèles et état de calibration.

---

## Marchés YES / NO

Le moteur principal analyse les marchés Jupiter et compare :

```text
Probabilité calculée par le modèle
            VS
Probabilité implicite du marché Jupiter
            ↓
Edge statistique estimé
```

Une prédiction n'entraîne pas automatiquement un pari.

Les règles de risque et de qualité doivent également être satisfaites.

---

## UP / DOWN 5m et 15m

Le moteur **TIMED Direction V2** est spécialement conçu pour les marchés directionnels courts.

Il analyse continuellement :

```text
BTC
ETH
SOL
XRP
HYPE
DOGE
BNB
```

et crée des observations permettant d'apprendre si un actif terminera dans la direction **UP** ou **DOWN** sur les marchés concernés.

Le fonctionnement est :

```text
Marché Jupiter
      ↓
Prix crypto
      ↓
Analyse quantitative
      ↓
Probabilité du modèle
      ↓
Prédiction SHADOW
      ↓
Fin du marché
      ↓
Résultat réel
      ↓
Score de Brier / log-loss
      ↓
Amélioration de la calibration
```

---

## Apprentissage permanent

Un point important de V1.0 est la séparation entre :

```text
APPRENTISSAGE
```

et :

```text
AUTORISATION LIVE
```

Même si une cryptomonnaie est :

```text
live_ready=False
```

elle peut continuer à générer des observations SHADOW/PAPER.

Cela permet au modèle de continuer à apprendre et éventuellement d'améliorer ses statistiques.

Le LIVE reste séparément verrouillé jusqu'à ce que les critères nécessaires soient atteints.

---

## PAPER et SHADOW

### SHADOW

Les prédictions SHADOW permettent au bot d'enregistrer une décision théorique sans engager d'argent.

Elles servent notamment à calculer :

- Le score de Brier
- Le log-loss
- La précision
- La calibration
- L'évolution du modèle

### PAPER

Le mode PAPER simule des positions sans argent réel.

Par défaut :

```env
TRADING_MODE=paper
AUTO_EXECUTE=false
LIVE_RELEASE_ENABLED=false
TIMED_DIRECTION_LIVE_ENABLED=false
```

Une nouvelle installation peut donc commencer à apprendre sans risque financier réel.

---

## Apprentissage adaptatif

Le dashboard peut afficher par exemple :

```text
PHASE : ADAPTATIF ACTIF
PROFILS ACTIFS : 14
```

Les profils sont gérés automatiquement.

L'utilisateur n'a normalement rien à modifier manuellement.

Ces profils peuvent correspondre à différentes combinaisons de :

- Cryptomonnaie
- Horizon temporel
- Type de marché
- Configuration du modèle
- Régime de marché
- Calibration

Le nombre de profils actifs peut évoluer avec le temps.

---

## Modèles neuronaux

Les modèles neuronaux ne sont pas automatiquement utilisés simplement parce que leur entraînement semble performant.

Le système peut vérifier :

- Performance hors échantillon
- Validation walk-forward
- Score de Brier
- Log-loss
- Comparaison avec une baseline
- Robustesse statistique

Un modèle insuffisamment performant peut donc rester inactif.

C'est volontaire.

---

## Dashboard

Le dashboard local est accessible à :

```text
http://127.0.0.1:8501
```

Il permet notamment d'observer :

- Capital
- Positions
- P&L
- Marchés
- Prix crypto
- Décision actuelle du moteur
- Probabilités
- Edge
- Confiance
- Fiabilité
- Ordres PAPER
- Signaux
- Apprentissage adaptatif
- Profils actifs
- État LIVE
- État du système

Lancer avec :

```powershell
.\DASHBOARD.ps1
```

---

## Installation Windows

Pour une nouvelle machine :

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\SETUP_FROM_ZERO_WINDOWS.ps1
notepad .env
```

Si Python et Node.js sont déjà installés :

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\INSTALL.ps1
.\INSTALL_OLLAMA.ps1 -InstallIfMissing
notepad .env
```

Ajouter ensuite la clé Jupiter :

```env
JUPITER_API_KEY=VOTRE_CLE_API_JUPITER
```

Puis :

```powershell
.\DOCTOR.ps1
.\OLLAMA_STATUS.ps1
.\SOURCE_TEST.ps1
.\SCAN_ONCE.ps1
.\START_BOT.ps1
.\DASHBOARD.ps1
```

---

## Ollama

Installation :

```powershell
irm https://ollama.com/install.ps1 | iex
```

Modèle par défaut :

```powershell
ollama pull qwen2.5:1.5b-instruct-q4_K_M
```

---

## LIVE

Le LIVE est volontairement désactivé par défaut.

Ne l'activez pas simplement pour obtenir davantage de paris.

Avant une position LIVE, plusieurs protections peuvent intervenir :

- Calibration
- Score de Brier
- Log-loss
- Edge minimum
- Prix
- Spread
- Drift
- Solde
- Exposition
- Positions déjà ouvertes
- Simulation de transaction
- Validation des signatures
- Disponibilité de l'API Jupiter

Si les conditions ne sont pas suffisamment bonnes, la décision correcte du bot peut être :

```text
NO TRADE
```

---

## Sécurité

Ne publiez jamais sur GitHub :

```text
.env
wallet/*.json
clé privée
seed phrase
API key privée
base SQLite personnelle
logs privés
backups
```

Consultez :

```text
SECURITY.md
```

---

## Jupiter et Solana

JupiterDegenEdgeBot utilise des technologies et services appartenant à l'écosystème :

- Solana
- Jupiter Developer Platform
- Jupiter Prediction

Ce projet est indépendant.

> **JupiterDegenEdgeBot n'est pas un produit officiel de Jupiter, Solana Labs ou de la Solana Foundation et ne prétend pas être sponsorisé, approuvé ou partenaire de ces organisations.**

---

## Objectif du projet

Le but du projet n'est pas de lancer le plus grand nombre possible de paris.

L'objectif est de fournir un environnement expérimental pour :

- Recherche sur les prediction markets
- Modélisation quantitative
- Analyse crypto
- Calibration probabiliste
- Apprentissage adaptatif
- Recherche neuronale
- PAPER trading
- Analyse UP/DOWN courte durée
- Gestion du risque
- Automatisation
- Monitoring local

Il est donc parfaitement normal que le moteur décide parfois :

```text
NO TRADE
```

---

## Avertissement

Ce logiciel est expérimental.

Les cryptomonnaies et marchés de prédiction comportent des risques financiers importants.

Le programme peut :

- Faire de mauvaises prédictions
- Perdre de l'argent
- Rencontrer des erreurs API
- Rencontrer des erreurs réseau
- Subir des changements d'API Jupiter
- Contenir des bugs
- Produire de mauvaises estimations

Aucun résultat futur n'est garanti.

Ce projet ne constitue pas un conseil financier ou un conseil d'investissement.

Consultez également :

```text
DISCLAIMER.md
```

---

## Licence

Aucune licence open source n'est actuellement définie pour la V1.0.

Tant qu'une licence n'est pas ajoutée explicitement, les règles normales de copyright s'appliquent.

Si vous souhaitez autoriser la redistribution, la modification ou l'utilisation commerciale, ajoutez une licence adaptée au dépôt.

---

<p align="center">
  <strong>JupiterDegenEdgeBot V1.0</strong><br>
  Quantitative Prediction Market Research • Solana • Jupiter Prediction
</p>
