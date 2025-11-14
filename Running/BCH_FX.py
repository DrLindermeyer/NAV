# -*- coding: utf-8 -*-
"""
Created on Tue Nov 11 17:54:35 2025

@author: Administrator
"""
import bcchapi

siete = bcchapi.Siete(
    "franz.lindermeyer@gmail.com", 
    "Hauneburg007"
    )

usdclp = siete.cuadro(
    series = ['F073.TCO.PRE.Z.D'],
    nombres = ['USDCLP'],
    desde = '2016-12-01',
    hasta = '2025-11-11'
).ffill()

usdclp.columns.name = 'Date'
usdclp.to_csv('usdclp.csv')