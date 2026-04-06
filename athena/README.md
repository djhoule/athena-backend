# ATHENA AI — High-Probability Trade Scanner

Application mobile complète qui scanne Forex, Indices, Crypto et Commodités
toutes les 15 minutes et sort les 2-3 meilleurs setups du moment.

---

## 📁 Structure

```
athena/
├── backend/          ← Python + FastAPI (le moteur)
│   ├── main.py
│   ├── config.py
│   ├── requirements.txt
│   ├── engine/
│   │   ├── data_fetcher.py    ← OHLCV (CCXT, yFinance, Alpha Vantage)
│   │   ├── technical.py       ← RSI, MACD, EMA, Support/Résistance
│   │   ├── fundamental.py     ← Calendrier éco + Sentiment NLP
│   │   ├── scorer.py          ← Scoring composite 0-100
│   │   ├── scanner.py         ← Orchestrateur (tourne toutes les 15min)
│   │   └── notifications.py   ← Push notifications Expo
│   ├── routers/
│   │   ├── trades.py          ← GET /trades/top, /history, /{id}
│   │   ├── auth.py            ← POST /auth/login, /register, /push-token
│   │   └── alerts.py          ← GET/PUT /alerts/config
│   └── models/
│       └── database.py        ← SQLAlchemy models + init DB
└── mobile/           ← React Native + Expo (l'app)
    ├── App.tsx                 ← Navigation principale
    ├── package.json
    └── src/
        ├── services/api.ts     ← Axios client + tous les appels API
        ├── store/useStore.ts   ← Zustand global state
        ├── components/
        │   ├── TradeCard.tsx   ← Carte principale d'un setup
        │   └── ScoreGauge.tsx  ← Gauge circulaire du score
        └── screens/
            ├── DashboardScreen.tsx
            ├── HistoryScreen.tsx
            ├── SettingsScreen.tsx
            └── LoginScreen.tsx
```

---

## 🚀 Installation Backend

### 1. Prérequis
- Python 3.11+
- PostgreSQL (ou Supabase pour hébergement gratuit)
- Redis (optionnel, pour cache)

### 2. Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Variables d'environnement
Crée un fichier `.env` dans `backend/` :
```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost/athena
SECRET_KEY=ton-secret-32-chars-minimum
POLYGON_API_KEY=ton-cle-polygon       # polygon.io (gratuit pour démarrer)
ALPHA_VANTAGE_KEY=ton-cle-av          # alphavantage.co (gratuit 25 req/day)
NEWS_API_KEY=ton-cle-newsapi          # newsapi.org (gratuit 100 req/day)
EXPO_ACCESS_TOKEN=ton-token-expo      # expo.dev (gratuit)
```

> **Note:** L'app fonctionne sans toutes les clés. yFinance est le fallback
> gratuit pour Forex (EURUSD=X) et Commodités. CCXT/Binance ne requiert
> pas de clé pour les données publiques. ForexFactory RSS est gratuit.

### 4. Lancer
```bash
uvicorn main:app --reload --port 8000
```

Swagger UI disponible sur: http://localhost:8000/docs

---

## 📱 Installation Mobile

### 1. Prérequis
- Node.js 18+
- Expo CLI: `npm install -g expo-cli`
- Expo Go sur ton téléphone (iOS/Android)

### 2. Setup
```bash
cd mobile
npm install
```

### 3. Config API
Crée `.env` dans `mobile/` :
```env
EXPO_PUBLIC_API_URL=http://TON_IP_LOCAL:8000
```
> Pour tester en local, utilise ton IP LAN (pas `localhost` depuis le téléphone).
> Ex: `EXPO_PUBLIC_API_URL=http://192.168.1.100:8000`

### 4. Lancer
```bash
npx expo start
```
Scan le QR code avec Expo Go.

---

## 📊 Logique de Scoring

| Critère | Poids | Logique |
|---|---|---|
| Support/Résistance | 25pts | Niveau clé testé, 52W H/L |
| RSI | 15pts | Survente (<35) ou surachat (>65) |
| MACD | 15pts | Crossover confirmé + position zéro |
| EMA Stack | 15pts | Alignement des 3 EMAs (20/50/200) |
| Calendrier Éco | 15pts | Événement high-impact = pénalité |
| Sentiment NLP | 15pts | FinBERT sur 30 headlines récentes |

**Seuils:**
- Score ≥ 80 → Grade A 🏆
- Score ≥ 65 → Grade B ⭐
- Score < 65 → Ignoré

**R:R minimum:** 1:1.5 (calculé avec ATR × 1.5 pour le SL)

---

## 🔧 Personnalisation

Pour modifier la watchlist, édite `backend/config.py` :
```python
FOREX_PAIRS = ["EURUSD", "GBPUSD", ...]
CRYPTO_PAIRS = ["BTC/USDT", "ETH/USDT", ...]
```

Pour modifier les seuils de scoring, édite `backend/engine/scorer.py`.

Pour changer l'intervalle de scan, modifie dans `config.py` :
```python
SCAN_INTERVAL_MINUTES = 15  # ex: 30 pour toutes les 30min
```

---

## 🚢 Déploiement Production

### Backend
```bash
# Railway.app (recommandé — gratuit tier)
railway login
railway up
```

### Mobile
```bash
# Build EAS
npm install -g eas-cli
eas build --platform all
```

---

## ⚠️ Disclaimer

Cet outil est fourni à titre informatif uniquement.
Les signaux générés algorithmiquement ne constituent pas des conseils financiers.
Tradez responsablement. Le capital est à risque.
