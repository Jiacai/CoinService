# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import pymysql
import requests
import json
import bs4
import time
import sys
from datetime import datetime
import talib
import xutils

# coding=utf-8

from binance.client import Client
import json
from datetime import datetime


def downsideDeviation(s):
    return np.sqrt(np.sum((s.where(s < 0)) ** 2) / len(s)) * np.sqrt(252.0)


if __name__ == "__main__":
    config = xutils.getLocalConfigJson()
    api_key = config['api_key']
    api_secret = config['api_secret']


    DD_LAG = 45

    conn = xutils.getLocalConn()
    cursor = conn.cursor()

    client = Client(api_key, api_secret)

    codes = {
        'BTC': 'BTCUSDT',
        'ETH': 'ETHUSDT',
        'EOS': 'EOSUSDT'
    }

    for code in codes:
        print ('Get Close', codes[code])

        candles = client.get_klines(symbol=codes[code], interval=Client.KLINE_INTERVAL_1DAY)

        upsert_sql = xutils.buildUpsertOnDuplicateSql('coin_close', ['code', 'date', 'close'])

        # discard last one
        for c in candles[:-1]:
            dt = datetime.fromtimestamp(c[0]/1000).date()
            cursor.execute(upsert_sql, (code, dt, float(c[4])) * 2)
        conn.commit()
        time.sleep(3)

    s_lst = []
    for code in codes:
        sql = "select date, close from coin_close where code = %(code)s order by date"
        df = pd.read_sql(sql, con=conn, params={'code':code})
        df = df.set_index('date')
        s_lst.append(df['close'])

    cursor.close()
    conn.close()

    # 选择今日币种
    df = pd.concat(s_lst, axis=1)
    df = df.fillna(method='ffill')
    df.columns = codes

    # df = df.loc[datetime(2016, 1, 1).date(): ]

    print df.head()
    print df.tail()

    df['masig'] = 0
    df['cnt'] = 0

    for code in codes:
        df[code + 'mmtm7'] = np.log(df[code] / (df[code].shift(6) + df[code].shift(7) + df[code].shift(8)) * 3)
        df[code + 'mmtm30'] = np.log(df[code] / (df[code].shift(29) + df[code].shift(30) + df[code].shift(31)) * 3)
        df[code + 'mmtm7'] = df[code + 'mmtm7'].fillna(0)
        df[code + 'mmtm30'] = df[code + 'mmtm30'].fillna(0)
        df[code + 'rsi'] = talib.RSI(df[code].values, 15)
        df[code + 'rsi'] = df[code + 'rsi'].fillna(0)

        df[code + 'ma'] = df[code].rolling(30).mean()
        df[code + 'ma'] = df[code + 'ma'].fillna(0)

        df[code + 'ma7'] = df[code].rolling(7).mean()
        df[code + 'ma7'] = df[code + 'ma7'].fillna(0)

        # df[code + 'xma'] = pd.rolling_mean(df[code], 180, 180)
        # df[code + 'xma'] = df[code + 'xma'].fillna(0)

        df[code + 'mmtm1'] = np.log(df[code] / df[code].shift(1))

        s = np.log(df[code] / df[code].shift(1))
    
        df[code + 'dd'] = 0.0
        for i in range(DD_LAG, len(s)):
            tmp_s = s[i - DD_LAG + 1: i + 1]
            dd = downsideDeviation(tmp_s)
            df.loc[s.index[i], code + 'dd'] = dd

    last_pick = None
    cnt = 0
    dd_bar = 0.75
    rsi_bar = 90
    for index, row in df.iterrows():
        pick = None
        mmtm7_lst = []
        mmtm30_lst = []
        for code in codes:
            stoploss_bar = -0.15
            if row[code + 'mmtm1'] < stoploss_bar:
                continue
            if row[code + 'mmtm7'] > 0 and row[code + 'dd'] < dd_bar and row[code + 'rsi'] < rsi_bar:
                mmtm7_lst.append(row[code + 'mmtm7'])
            if row[code + 'mmtm7'] > 0 and row[code + 'mmtm30'] > 0 and row[code + 'dd'] < dd_bar and row[code + 'rsi'] < rsi_bar:
                mmtm30_lst.append(row[code + 'mmtm30'])
        if len(mmtm7_lst) == 0:
            pass
        elif last_pick is None:
            if len(mmtm30_lst) != 0:
                for code in codes:
                    if row[code + 'mmtm30'] == np.max(mmtm30_lst) and row[code] >= row[code + 'ma'] and row[code] >= row[code + 'ma7']:
                        pick = code
                        break
        elif row[last_pick + 'mmtm7'] not in mmtm7_lst:
            if len(mmtm30_lst) != 0:
                for code in codes:
                    if row[code + 'mmtm30'] == np.max(mmtm30_lst) and row[code] >= row[code + 'ma']:
                        pick = code
                        break
        else:
            pick = last_pick
            # 试一下择强
            if len(mmtm7_lst) != 0 and len(mmtm30_lst) != 0:
                pick_7 = None
                pick_30 = None
                min_7 = None
                for code in codes:
                    if row[code + 'mmtm7'] == np.max(mmtm7_lst):
                        pick_7 = code
                    if row[code + 'mmtm30'] == np.max(mmtm30_lst):
                        pick_30 = code
                    if row[code + 'mmtm7'] == np.min(mmtm7_lst):
                        min_7 = code
                if pick_7 == pick_30  and row[pick_7] >= row[pick_7 + 'ma']:
                    pick = pick_7

        if pick == last_pick:
            pass
        else:
            if last_pick is None:
                last_close = 1.0
            else:
                last_close = row[last_pick]
            if pick is None:
                this_close = 1.0
            else:
                this_close = row[pick]
            print index, last_pick, last_close, '==>', pick, this_close
            cnt += 1
        df.loc[index, 'pick'] = str(pick)
        last_pick = pick

