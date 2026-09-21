import pandas as pd
df = pd.read_excel(r'D:/Windows/Documents/海南省村界/海南省村界/HainanMap.xlsx', header=None, usecols='A,C,D,E', dtype=str)
df.columns = ['key', 'en_name', 'city_cn', 'display_name']
print(df['city_cn'].unique())