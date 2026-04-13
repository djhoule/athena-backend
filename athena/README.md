# ATHENA AI — High-Probability Trade Scanner

Scanner algorithmique qui analyse Forex, Indices, Crypto et Commodités
toutes les 15 minutes et sort les meilleurs setups selon un score composite.

---

## Liens rapides

| Ressource | URL |
|---|---|
| Backend prod | https://athena-backend-production-7bd6.up.railway.app |
| Swagger UI (prod) | https://athena-backend-production-7bd6.up.railway.app/docs |
| Swagger UI (local) | http://localhost:8000/docs |
| Railway dashboard | https://railway.app |
| Supabase dashboard | https://supabase.com/dashboard |

---

## Structure du projet

```
3 - SWISH TRADING/
├── athena-backend/
│   ├── Dockerfile
│   ├── railway.toml
│   └── athena/backend/
│       ├── .env                  ← secrets locaux (sur OneDrive, non commité)
│       ├── .env.example          ← template de référence
│       ├── requirements.txt
│       ├── config.py             ← tous les paramètres modifiables
│       ├── main.py
│       ├── engine/
│       │   ├── data_fetcher.py   ← OHLCV: Yahoo Finance v8 + Binance public
│       │   ├── technical.py      ← RSI, MACD, EMA, Ichimoku, S/R, Bollinger
│       │   ├── scorer.py         ← score composite 0-100 + trade builder
│       │   ├── scanner.py        ← orchestrateur (scan toutes les 15min)
│       │   ├── fundamental.py    ← calendrier éco + sentiment
│       │   ├── outcome_checker.py← ferme les trades arrivés à TP/SL
│       │   └── notifications.py  ← push Expo + embeds Discord
│       ├── routers/
│       │   ├── trades.py         ← GET /trades, /live-prices, /history
│       │   ├── auth.py           ← POST /auth/login, /register
│       │   └── alerts.py         ← GET/PUT /alerts/config
│       └── models/
│           └── database.py       ← SQLAlchemy models + migration auto
├── athena-mobile/
│   └── athena/mobile/
│       ├── .env                  ← EXPO_PUBLIC_API_URL (sur OneDrive)
│       ├── .env.example
│       ├── App.tsx
│       ├── package.json
│       └── src/
│           ├── services/api.ts
│           ├── store/useStore.ts
│           ├── components/
│           └── screens/
├── setup.bat                     ← POINT D'ENTRÉE nouveau PC
└── run-backend.bat               ← lance le backend local
```

---

## Setup sur nouveau PC

### Prérequis
- **Python 3.11+** — https://python.org/downloads (cocher "Add to PATH")
- **Node.js 18+** — https://nodejs.org
- **Git** — https://git-scm.com (optionnel si OneDrive déjà synchronisé)

### Démarrage rapide

```
1. Attendre que OneDrive finisse de synchroniser le dossier
2. Double-cliquer sur setup.bat  (recrée venv + node_modules)
3. Double-cliquer sur run-backend.bat  (démarre le backend local)
```

> **Pourquoi setup.bat ?**
> Le `venv/` Python et `node_modules/` sont spécifiques à chaque machine.
> Même si OneDrive les synchronise, ils ne fonctionneront pas directement.
> `setup.bat` les supprime et les recrée proprement à chaque nouveau PC.

### Exclure venv/ et node_modules/ de OneDrive (recommandé)

Ces dossiers font plusieurs centaines de MB et ne doivent pas se synchroniser.
Pour les exclure :

```
Clic droit sur le dossier venv\ (ou node_modules\)
→ OneDrive → "Ne pas synchroniser sur cet appareil"
```

Ou depuis les paramètres OneDrive :
```
Icône OneDrive dans la barre → Paramètres → Compte → Choisir les dossiers
```

### Variables d'environnement

Le fichier `.env` est sur OneDrive — il sera déjà présent après la sync.
Si ce n'est pas le cas, copie `.env.example` → `.env` et remplis les valeurs.

**Variables requises** (backend) :
| Variable | Description |
|---|---|
| `DATABASE_URL` | Connection string Supabase (postgresql+asyncpg://...) |
| `SECRET_KEY` | Clé JWT aléatoire 32+ caractères |

**Variables optionnelles** (backend) :
| Variable | Description |
|---|---|
| `ALPHA_VANTAGE_KEY` | Données fondamentales (gratuit 25 req/day) |
| `DISCORD_WEBHOOK_URL` | Alertes Grade A sur Discord |
| `EXPO_ACCESS_TOKEN` | Push notifications mobiles |

**Variable requise** (mobile) :
| Variable | Description |
|---|---|
| `EXPO_PUBLIC_API_URL` | URL du backend (production ou IP locale) |

---

## Lancer le backend en local

```bash
cd athena-backend/athena/backend
venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

Ou simplement : **double-cliquer `run-backend.bat`** à la racine.

Swagger UI : http://localhost:8000/docs

### Tester le scanner manuellement

```bash
# Déclenche un scan immédiat (sans attendre 15min)
curl -X POST http://localhost:8000/scan-now
```

---

## Lancer l'app mobile

```bash
cd athena-mobile/athena/mobile
npx expo start
```

Scan le QR code avec Expo Go (iOS/Android).

Pour dev local, mettre l'IP LAN dans `.env` :
```
EXPO_PUBLIC_API_URL=http://192.168.1.XXX:8000
```

---

## Déploiement Production (Railway)

```bash
cd athena-backend
railway login
railway up
```

Variables d'environnement Railway à configurer (Settings → Variables) :
- `DATABASE_URL`
- `SECRET_KEY`
- `DISCORD_WEBHOOK_URL`
- `ALPHA_VANTAGE_KEY` (optionnel)
- `EXPO_ACCESS_TOKEN` (optionnel)

Le backend se redémarre automatiquement à chaque `railway up`.

---

## Sources de données

| Asset | Source | Clé API requise |
|---|---|---|
| Forex (EURUSD=X...) | Yahoo Finance v8 chart API | Non |
| Indices (^GSPC...) | Yahoo Finance v8 chart API | Non |
| Commodités (GC=F...) | Yahoo Finance v8 chart API | Non |
| Crypto (BTC, ETH...) | Binance public API | Non |

Toutes les sources de données de marché sont gratuites et sans clé API.

---

## Logique de Scoring

| Critère | Poids | Signal |
|---|---|---|
| RSI + Divergence | 12 pts | Survente/surachat + divergence haussière/baissière |
| MACD | 12 pts | Crossover confirmé + position relative au zéro |
| EMA Stack (20/50/200) | 15 pts | Alignement directionnel des 3 EMAs |
| Support / Résistance | 20 pts | Niveau clé testé, touches multiples, 52W H/L |
| Trend + ADX | 10 pts | Tendance structurelle + force du trend |
| Bollinger Bands | 8 pts | Prix aux bandes, squeeze, %B |
| Ichimoku Cloud | 10 pts | Price vs cloud, TK cross, Chikou |
| Calendrier Éco | 12 pts | Pénalité si événement high-impact proche |
| Sentiment NLP | 11 pts | Headlines récentes |

**Seuils :**
- Score ≥ 82 → Grade A (Discord alert)
- Score ≥ 72 → Grade B
- Score < 72 → Ignoré

**Filtres additionnels :**
- R:R minimum 2.0 (calculé avec ATR × 1.5 pour le SL)
- Filtre de corrélation : pas 2 trades sur la même paire USD/EUR/JPY simultanément
- MTF confirmation : signal identique sur 1D + 4H = bonus

---

## Paramètres modifiables

Tout se configure dans `athena-backend/athena/backend/config.py` :

```python
MIN_SCORE_THRESHOLD = 74    # score minimum pour retenir un trade
MIN_CONFLUENCE = 3          # nombre minimum de signaux alignés
MIN_RISK_REWARD = 2.0       # R:R minimum
SCAN_INTERVAL_MINUTES = 15  # fréquence du scan
MAX_TRADES_OUTPUT = 10      # max trades actifs en même temps
```

---

## ⚠️ Disclaimer

Outil informatif uniquement. Les signaux algorithmiques ne constituent
pas des conseils financiers. Tradez responsablement. Le capital est à risque.
