import asyncio
from engine.data_fetcher import fetch_ohlcv

async def test():
    symbols = [('SPY', 'INDICES'), ('GC=F', 'COMMODITY'), ('BTC/USDT', 'CRYPTO')]
    for sym, mtype in symbols:
        df = await fetch_ohlcv(sym, mtype, '4h', 50)
        if df is not None:
            last = df['close'].iloc[-1]
            print(f'{sym}: OK - {len(df)} candles, last close: {last:.4f}')
        else:
            print(f'{sym}: FAILED')

asyncio.run(test())
