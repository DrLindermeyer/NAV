# -*- coding: utf-8 -*-
"""
Created on Wed Nov 12 09:30:17 2025

@author: Administrator
"""

import pandas as pd
from datetime import datetime

def load_data():

    data = pd.read_csv('lastPrices_Nav.txt')
    data.columns = ['TICKER', 'DATE', 'OPEN', 'HIGH',  'LOW','CLOSE', 'VOLUME']
    data = data[data['DATE'] != "{D.DATE.trans('-')}"]
    data['DATE'] = pd.to_datetime(data['DATE'], format='%Y%m%d')
    
    cols_to_convert = data.columns.difference(['TICKER', 'DATE'])
    data[cols_to_convert] = data[cols_to_convert].apply(pd.to_numeric, errors='coerce')

    return data

data = load_data()

prices = data[['TICKER', 'DATE', 'CLOSE']].copy()
prices.columns = ['ID', 'Date', 'Price']
prices['Date'] = pd.to_datetime(prices['Date'])
#prices.set_index('Date', inplace = True)

date_min = '2017-12-01'
date_max = prices['Date'].max()
dates_range = pd.DataFrame(pd.date_range(date_min, date_max), columns = ['Date'])

prices_wide = prices.pivot(index= 'Date', values = 'Price', columns = 'ID')

prices_wide_merged = dates_range.merge(prices_wide, on = 'Date', how = 'left').ffill()
prices_df = prices_wide_merged.melt(id_vars = 'Date', var_name= 'ID', value_name= 'Price')

prices_df.set_index('Date', inplace= True)
prices_df.to_csv('Quinenco_Prices.csv')